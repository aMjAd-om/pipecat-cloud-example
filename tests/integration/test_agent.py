"""Offline regression checks for streaming boundaries and failure cleanup."""
import asyncio
import os
import threading
import signal
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from voice_agent.providers import azure_stt as services
from voice_agent.providers.azure_tts import StreamingAzureTTS
from voice_agent.providers.foundry_llm import FoundryLLMService
from pipecat.services.openai.llm import OpenAILLMService
from voice_agent.observability.timing import TurnTiming
from pipecat.frames.frames import TranscriptionFrame, TTSAudioRawFrame, ErrorFrame


class Signal:
    def connect(self, callback):
        self.callback = callback

    def fire(self, event=None):
        if self.callback:
            self.callback(event)

    def disconnect_all(self):
        self.callback = None


class Future:
    def get(self):
        pass


class Synth:
    instances = []

    def __init__(self, **kwargs):
        self.synthesizing = Signal()
        self.synthesis_completed = Signal()
        self.synthesis_canceled = Signal()
        self.stopped = False
        self.instances.append(self)

    def speak_ssml_async(self, text):
        self.text = text
        def callback():
            self.synthesizing.fire(NS(result=NS(audio_data=b"\x00\x01" * 240)))
        thread = threading.Thread(target=callback)
        thread.start()
        thread.join()
        return Future()

    def stop_speaking_async(self):
        self.stopped = True
        return Future()


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {
            "DEFAULT_LANGUAGE": "ar",
            "AZURE_SPEECH_KEY": "test", "AZURE_SPEECH_REGION": "uaenorth",
            "AZURE_TTS_VOICE_AR": "ar-OM-AyshaNeural",
            "AZURE_TTS_VOICE_EN": "en-US-JennyNeural",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_stt_cancellation_reports_provider_error_without_credentials(self):
        stt = services.StreamingAzureSTT(timing=TurnTiming())
        stt._generation = 1
        stt._recover = AsyncMock()
        event = NS(reason="Error", error_code="AuthenticationFailure",
                   error_details="Invalid key test at https://speech.example/?token=private")
        with patch.object(services.logger, "warning") as warning:
            worker = asyncio.create_task(stt._consume())
            try:
                await stt._events.put((1, "canceled", event, 1.0))
                await asyncio.sleep(0.01)
                stt._recover.assert_awaited_once()
                details = warning.call_args.args[1]
                self.assertIn("AuthenticationFailure", details)
                self.assertNotIn("test", details)
                self.assertNotIn("private", details)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_stt_partial_final_dedup_silence_and_repeated_phrase(self):
        stt = services.StreamingAzureSTT(timing=TurnTiming())
        stt.push_frame = AsyncMock()
        stt._closed = False
        stt._generation = 1
        worker = asyncio.create_task(stt._consume())
        def event(text, identity):
            return NS(result=NS(text=text, result_id=identity,
                                reason=services.speechsdk.ResultReason.RecognizedSpeech))
        try:
            for kind, value in [("partial", event("السلام", "p")),
                                ("final", event("", "silence")),
                                ("final", event("السلام عليكم", "one")),
                                ("final", event("السلام عليكم", "one")),
                                ("final", event("السلام عليكم", "two"))]:
                await stt._events.put((1, kind, value, 1.0))
            # Old-session callback must not produce another final.
            await stt._events.put((0, "final", event("stale", "old"), 1.0))
            await asyncio.sleep(0.01)
            frames = [call.args[0] for call in stt.push_frame.await_args_list]
            self.assertEqual(len(frames), 2)
            self.assertTrue(all(isinstance(f, TranscriptionFrame) for f in frames))
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_tts_streams_before_completion_and_stops_on_cancel(self):
        with patch.object(services.speechsdk, "SpeechSynthesizer", Synth):
            tts = StreamingAzureTTS(timing=TurnTiming())
            tts._sample_rate = 24000
            output = []
            got_audio = asyncio.Event()
            async def consume():
                async for frame in tts.run_tts("مرحبا < &"):
                    output.append(frame)
                    if isinstance(frame, TTSAudioRawFrame):
                        got_audio.set()
            task = asyncio.create_task(consume())
            await asyncio.wait_for(got_audio.wait(), 2)
            self.assertFalse(task.done())  # No synthesis_completed event yet.
            synth = Synth.instances[-1]
            self.assertIn("ar-OM-AyshaNeural", synth.text)
            self.assertIn("&lt; &amp;", synth.text)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(synth.stopped)
            self.assertFalse(tts._active)
            # Late callbacks must not contaminate the next synthesis.
            synth.synthesizing.fire(NS(result=NS(audio_data=b"old")))
            generator = tts.run_tts("Hello")
            await anext(generator)
            await anext(generator)
            newer = Synth.instances[-1]
            self.assertIn("ar-OM-AyshaNeural", newer.text)  # Fixed session voice, even for English input.
            await generator.aclose()
            self.assertTrue(newer.stopped)

    async def test_tts_failure_returns_error_and_cleans_up(self):
        class FailingSynth(Synth):
            def speak_ssml_async(self, text):
                self.synthesis_canceled.fire(NS(result=NS(cancellation_details=NS(
                    reason="Error", error_code="Forbidden", error_details="Quota exceeded"))))
        with patch.object(services.speechsdk, "SpeechSynthesizer", FailingSynth):
            tts = StreamingAzureTTS(timing=TurnTiming())
            tts._sample_rate = 24000
            with patch.object(services.logger, "error") as error:
                frames = [f async for f in tts.run_tts("Hello")]
                self.assertIn("Forbidden", error.call_args.args[1])
            self.assertTrue(any(isinstance(f, ErrorFrame) for f in frames))
            self.assertTrue(Synth.instances[-1].stopped)

    async def test_stt_failure_exhausts_bounded_reconnects(self):
        stt = services.StreamingAzureSTT(timing=TurnTiming())
        stt._closed = False
        stt._connect = AsyncMock(side_effect=RuntimeError("failure"))
        stt._disconnect = AsyncMock()
        stt.push_error = AsyncMock()
        with patch.object(services.asyncio, "sleep", AsyncMock()):
            await stt._recover()
        self.assertEqual(stt._connect.await_count, 3)
        self.assertTrue(stt.push_error.call_args.args[0].fatal)

    async def test_llm_failure_is_recoverable_and_does_not_log_exception_content(self):
        llm = FoundryLLMService(api_key="test", base_url="https://example.com/openai/v1/")
        llm.push_error = AsyncMock()
        with patch.object(OpenAILLMService, "_process_context", AsyncMock(
                side_effect=RuntimeError("sensitive request details"))):
            await llm._process_context(None)
        error = llm.push_error.call_args.args[0]
        self.assertFalse(error.fatal)
        self.assertNotIn("sensitive", error.error)
        await llm._client.close()

    async def test_pipeline_constructs_with_pinned_smallwebrtc_and_service_apis(self):
        from voice_agent.voice import pipeline as bot
        from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection
        connection = SmallWebRTCConnection(ice_servers=[])
        with patch.dict(os.environ, {
            "AZURE_OPENAI_API_KEY": "test",
            "AZURE_OPENAI_BASE_URL": "https://example.com/openai/v1/",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
        }), patch.object(bot.PipelineRunner, "run", AsyncMock()) as run:
            await bot.main(connection)
            run.assert_awaited_once()
        await connection.disconnect()

    @unittest.skipUnless(os.name == "posix", "Linux signal lifecycle check")
    async def test_sigterm_runs_native_pipeline_cancellation_and_sdk_cleanup(self):
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask
        from pipecat.pipeline.runner import PipelineRunner
        stt = services.StreamingAzureSTT(timing=TurnTiming())
        tts = StreamingAzureTTS(timing=TurnTiming())
        recognizer = NS(stop_continuous_recognition_async=lambda: Future())
        stream = NS(close=unittest.mock.Mock())
        synth = Synth()
        async def connect():
            stt._recognizer, stt._stream = recognizer, stream
            tts._active.add(synth)
        stt._connect = connect
        task = PipelineTask(Pipeline([stt, tts]))
        runner = PipelineRunner()
        @task.event_handler("on_pipeline_started")
        async def terminate(_task, _frame):
            asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        try:
            await asyncio.wait_for(runner.run(task), 5)
            self.assertTrue(stt._closed)
            self.assertIsNone(stt._worker)
            stream.close.assert_called_once()
            self.assertTrue(synth.stopped)
        finally:
            loop = asyncio.get_running_loop()
            loop.remove_signal_handler(signal.SIGTERM)
            loop.remove_signal_handler(signal.SIGINT)


if __name__ == "__main__":
    unittest.main()
