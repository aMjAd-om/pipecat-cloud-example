"""Paused/split utterances and PCM activity checks without provider requests."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    CancelFrame, InputAudioRawFrame, StartInterruptionFrame, TranscriptionFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.clocks.system_clock import SystemClock
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_transport import TransportParams

from voice_agent.config import output_nonsilent_threshold, speech_endpoint_settings
from voice_agent.observability.audio import OutputAudioActivity
from voice_agent.observability.timing import TurnTiming
from voice_agent.providers.azure_stt import StreamingAzureSTT
from voice_agent.transports.asterisk import AsteriskInput, AsteriskPeer
from voice_agent.voice.endpointing import STTFinalFrame, UtteranceEndBuffer

D = FrameDirection.DOWNSTREAM


class EndpointSettingsTests(unittest.TestCase):
    def test_trial_defaults_explicit_overrides_and_sdk_default_rollback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(speech_endpoint_settings(), dict(vad_stop_ms=500, segmentation_ms=400))
            self.assertEqual(output_nonsilent_threshold(), 200)
        with patch.dict(os.environ, {"TELEPHONY_VAD_STOP_MS": "800",
                                     "AZURE_STT_SEGMENTATION_SILENCE_MS": ""}):
            self.assertEqual(speech_endpoint_settings(), dict(vad_stop_ms=800, segmentation_ms=None))
        for name, value in (("TELEPHONY_VAD_STOP_MS", "nan"), ("TELEPHONY_VAD_STOP_MS", "0"),
                            ("AZURE_STT_SEGMENTATION_SILENCE_MS", "99"),
                            ("AZURE_STT_SEGMENTATION_SILENCE_MS", "5001")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                with self.assertRaisesRegex(ValueError, name):
                    speech_endpoint_settings()
        with patch.dict(os.environ, {"TELEPHONY_NONSILENT_RMS_MIN": "0"}):
            with self.assertRaises(ValueError):
                output_nonsilent_threshold()


class UtteranceTests(unittest.IsolatedAsyncioTestCase):
    def buffer(self, **options):
        self.ready = asyncio.Event()
        processor = UtteranceEndBuffer(TurnTiming(), **options)
        self.sent = []

        async def output(frame, direction=D):
            self.sent.append(frame)
            if isinstance(frame, TranscriptionFrame):
                self.ready.set()

        processor.push_frame = AsyncMock(side_effect=output)
        processor.push_error = AsyncMock()
        self.addAsyncCleanup(processor.cleanup)
        return processor

    def final(self, text, end=None):
        frame = STTFinalFrame(text, "caller", "recognition-event", "en")
        if end is not None:
            frame.metadata["stt_audio_end"] = end
        return frame

    def stopped(self, end=None):
        frame = UserStoppedSpeakingFrame()
        if end is not None:
            frame.metadata["speech_audio_end"] = end
        return frame

    def transcripts(self):
        return [frame for frame in self.sent if isinstance(frame, TranscriptionFrame)]

    async def test_early_finals_join_correction_and_wait_for_vad_stop(self):
        processor = self.buffer()
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.final("three", 1), D)
        await processor.process_frame(self.final("actually four", 2), D)
        await asyncio.sleep(.01)
        self.assertEqual(self.transcripts(), [])
        await processor.process_frame(self.stopped(2), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual([f.text for f in self.transcripts()], ["three actually four"])
        self.assertEqual(self.transcripts()[0].metadata["utterance_segments"], 2)

    async def test_old_final_cannot_release_unrecognized_tail_after_pause(self):
        processor = self.buffer()
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.final("four", 1), D)
        # Caller continued with a control after a pause shorter than VAD stop.
        await processor.process_frame(self.stopped(2), D)
        await asyncio.sleep(.02)
        self.assertFalse(self.ready.is_set())
        await processor.process_frame(self.final("but wait, pause", 2), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(self.transcripts()[0].text, "four but wait, pause")

    async def test_resume_during_release_wait_keeps_one_utterance(self):
        processor = self.buffer(settle_seconds=.03)
        tasks = TaskManager()
        tasks.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        await processor.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=tasks))
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.queue_frame(self.final("three"), D)
        await processor.process_frame(self.stopped(), D)
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(StartInterruptionFrame(), D)
        await processor.queue_frame(self.final("actually four"), D)
        await asyncio.sleep(.04)
        self.assertEqual(self.transcripts(), [])
        await processor.process_frame(self.stopped(), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual([f.text for f in self.transcripts()], ["three actually four"])

    async def test_late_final_releases_once_after_vad_stop(self):
        processor = self.buffer()
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.stopped(1), D)
        await processor.process_frame(self.final("five", 1), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(len(self.transcripts()), 1)
        self.assertIn("utterance_ready", processor.timing.stamps)

    async def test_unknown_timeline_settles_and_preserves_repeated_words(self):
        processor = self.buffer(settle_seconds=.02)
        await processor.process_frame(self.final("five"), D)
        await processor.process_frame(self.final("five"), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(self.transcripts()[0].text, "five five")

    async def test_incomplete_tail_expires_without_sending_partial_answer(self):
        processor = self.buffer(final_timeout=.02)
        failed = asyncio.Event()

        async def error(_frame):
            failed.set()

        processor.push_error.side_effect = error
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.final("four", 1), D)
        await processor.process_frame(self.stopped(2), D)
        await asyncio.wait_for(failed.wait(), 1)
        self.assertEqual(self.transcripts(), [])
        await processor.process_frame(self.final("late old result", 2), D)
        self.assertEqual(self.transcripts(), [])
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.final("five", 3), D)
        await processor.process_frame(self.stopped(3), D)
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(self.transcripts()[0].text, "five")

    async def test_cancel_during_settling_never_emits_late_transcript(self):
        processor = self.buffer(settle_seconds=.03)
        await processor.process_frame(self.final("four"), D)
        await processor.process_frame(CancelFrame(), D)
        await asyncio.sleep(.04)
        self.assertEqual(self.transcripts(), [])
        self.assertIsNone(processor._release_task)
        self.assertIsNone(processor._timeout_task)

    async def test_overlong_split_answer_is_rejected_as_a_whole(self):
        processor = self.buffer()
        await processor.process_frame(UserStartedSpeakingFrame(), D)
        await processor.process_frame(self.final("x" * 1500, 1), D)
        await processor.process_frame(self.final("x" * 600, 2), D)
        await processor.process_frame(self.stopped(2), D)
        await asyncio.sleep(.01)
        self.assertEqual(self.transcripts(), [])
        processor.push_error.assert_awaited_once()


class ProviderClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_configuration_is_applied_on_every_connection(self):
        from voice_agent.providers import azure_stt as module
        for setting in ("400", ""):
            with self.subTest(setting=setting), patch.dict(os.environ, {
                    "AZURE_SPEECH_KEY": "test", "AZURE_SPEECH_REGION": "uaenorth",
                    "AZURE_STT_SEGMENTATION_SILENCE_MS": setting}), \
                 patch.object(module.speechsdk, "SpeechConfig") as config, \
                 patch.object(module.speechsdk, "SpeechRecognizer"), \
                 patch.object(module.speechsdk.audio, "PushAudioInputStream"), \
                 patch.object(module.speechsdk.audio, "AudioConfig"):
                stt = StreamingAzureSTT(timing=TurnTiming())
                try:
                    await stt._connect()
                    await stt._disconnect()
                    await stt._connect()
                    self.assertEqual(config.call_count, 2)
                    if setting:
                        self.assertEqual(config.return_value.set_property.call_count, 2)
                        config.return_value.set_property.assert_called_with(
                            module.speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "400")
                    else:
                        config.return_value.set_property.assert_not_called()
                finally:
                    await stt.close()

    async def test_provider_offsets_map_to_transport_clock(self):
        from voice_agent.providers import azure_stt as module
        stt = StreamingAzureSTT(timing=TurnTiming())
        self.addAsyncCleanup(stt.close)
        stt._stream = Mock()
        frame = InputAudioRawFrame(bytes(640), 16000, 1)
        frame.metadata["input_audio_end"] = 10.02
        await stt.process_audio_frame(frame, D)
        self.assertAlmostEqual(stt._audio_clock[0][1], 10)
        # Simulate input discarded by a cancelled queue. Azure receives the next
        # 20 ms contiguously, while its source PCM position has advanced 200 ms.
        resumed = InputAudioRawFrame(bytes(640), 16000, 1)
        resumed.metadata["input_audio_end"] = 10.24
        await stt.process_audio_frame(resumed, D)
        delivered = asyncio.Event()

        async def capture(_frame, *_args):
            delivered.set()

        stt.push_frame = AsyncMock(side_effect=capture)
        stt._generation = 1
        stt._worker = asyncio.create_task(stt._consume())
        result = NS(text="five", reason=module.speechsdk.ResultReason.RecognizedSpeech,
                    result_id="one", offset=200_000, duration=200_000)
        await stt._events.put((1, "final", NS(result=result), 1.0))
        await asyncio.wait_for(delivered.wait(), 1)
        recognized = stt.push_frame.call_args.args[0]
        self.assertAlmostEqual(recognized.metadata["stt_audio_end"], 10.24)
        # A final ending at the discontinuity still belongs to the old span.
        delivered.clear()
        result = NS(text="three", reason=module.speechsdk.ResultReason.RecognizedSpeech,
                    result_id="two", offset=0, duration=200_000)
        await stt._events.put((1, "final", NS(result=result), 1.1))
        await asyncio.wait_for(delivered.wait(), 1)
        self.assertAlmostEqual(stt.push_frame.call_args.args[0].metadata["stt_audio_end"], 10.02)

    async def test_vad_stop_carries_last_active_pcm_position(self):
        source = AsteriskInput(NS(peer=NS()), TransportParams())
        self.addAsyncCleanup(source.cleanup)
        with patch.object(BaseInputTransport, "_vad_analyze", AsyncMock(side_effect=[
                VADState.SPEAKING, VADState.STOPPING, VADState.QUIET])), \
             patch.object(BaseInputTransport, "_handle_user_interruption", AsyncMock()):
            for expected in (.02, .04, .06):
                frame = InputAudioRawFrame(bytes(640), 16000, 1)
                await source._vad_analyze(frame)
                self.assertAlmostEqual(frame.metadata["input_audio_end"], expected)
            stopped = UserStoppedSpeakingFrame()
            await source._handle_user_interruption(stopped)
            self.assertAlmostEqual(stopped.metadata["speech_audio_end"], .02)


class SurveyEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pipeline_sends_one_complete_correction_or_pause_to_decider(self):
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask
        from pipecat.pipeline.runner import PipelineRunner
        from tests.unit.test_survey import manager, decision
        from voice_agent.voice.survey import SurveyConversationProcessor

        for tail, proposal, expected_answers, paused in (
            ("actually four", decision(), {"service_rating": 4}, False),
            ("but wait, pause", decision(intent="pause", answer_status="not_answered",
                                         answer_value=None, action="pause"), {}, True),
        ):
            with self.subTest(tail=tail):
                survey = manager()
                timing = TurnTiming()
                gate = UtteranceEndBuffer(timing)
                invoke = AsyncMock(return_value=(proposal, {}))
                processor = SurveyConversationProcessor(survey, invoke, timing)
                processor._speak = AsyncMock()
                task = PipelineTask(Pipeline([gate, processor]))
                running = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))

                async def wait_until(predicate):
                    async with asyncio.timeout(3):
                        while not predicate():
                            await asyncio.sleep(.001)

                def segment(text, end):
                    frame = STTFinalFrame(text, "caller", "segment", "en")
                    frame.metadata["stt_audio_end"] = end
                    return frame

                try:
                    await task.queue_frames([UserStartedSpeakingFrame(), segment("three", 1)])
                    await wait_until(lambda: bool(gate._parts))
                    invoke.assert_not_awaited()
                    stopped = UserStoppedSpeakingFrame()
                    stopped.metadata["speech_audio_end"] = 2
                    await task.queue_frames([stopped])
                    await asyncio.sleep(.02)
                    invoke.assert_not_awaited()
                    await task.queue_frames([segment(tail, 2)])
                    await wait_until(lambda: bool(survey.metrics))
                    invoke.assert_awaited_once()
                    self.assertEqual(invoke.call_args.args[0][-1]["content"], "three " + tail)
                    self.assertEqual(survey.answers, expected_answers)
                    self.assertEqual(survey.paused, paused)
                finally:
                    await task.cancel()
                    await asyncio.wait_for(running, 3)


class OutputActivityTests(unittest.IsolatedAsyncioTestCase):
    def test_silence_counts_exclude_transport_wait_and_retain_no_audio(self):
        activity = OutputAudioActivity(200)
        quiet, loud = bytes(640), b"\xe8\x03" * 320
        self.assertFalse(activity.observe(quiet, 1, 1.02))
        self.assertTrue(activity.observe(loud, 3, 3.02))
        self.assertFalse(activity.observe(quiet, 5, 5.02))
        self.assertAlmostEqual(activity.leading_silence, .02)
        self.assertAlmostEqual(activity.trailing_silence, .02)
        self.assertAlmostEqual(activity.duration, .06)
        self.assertEqual(activity.first_nonsilent_at, 3)
        self.assertEqual(activity.last_nonsilent_end, 3.02)
        self.assertFalse(any(isinstance(v, (bytes, bytearray)) for v in vars(activity).values()))

    async def test_actual_writes_update_nonsilent_tail_and_flush_clears_it(self):
        peer = AsteriskPeer(AsyncMock(), {}, asyncio.Event())
        peer.timing = TurnTiming()
        peer.playback_timing = peer.timing.snapshot()
        await peer.audio(bytes(640), peer.epoch)
        self.assertIsNone(peer.timing.last_nonsilent_audio_end)
        await peer.audio(b"\xe8\x03" * 320, peer.epoch)
        tail = peer.timing.last_nonsilent_audio_end
        await peer.audio(bytes(640), peer.epoch)
        self.assertEqual(peer.timing.last_nonsilent_audio_end, tail)
        self.assertGreater(peer.timing.last_audio_end, tail)
        with patch.object(peer.playback_timing, "metric") as metric:
            peer.report_audio_activity()
            metric.assert_any_call("transport_leading_silence", 0, .02)
            metric.assert_any_call("transport_trailing_silence", 0, .02)
        await peer.flush()
        self.assertIsNone(peer.timing.last_nonsilent_audio_end)
        self.assertEqual(peer.audio_activity.duration, 0)

    async def test_failed_write_is_not_counted_as_heard_or_nonsilent(self):
        wire = AsyncMock()
        wire.send_bytes.side_effect = RuntimeError("failed")
        peer = AsteriskPeer(wire, {}, asyncio.Event())
        peer.timing = TurnTiming()
        peer.playback_timing = peer.timing.snapshot()
        with self.assertRaises(RuntimeError):
            await peer.audio(b"\xe8\x03" * 320, peer.epoch)
        self.assertIsNone(peer.timing.last_nonsilent_audio_end)
        self.assertEqual(peer.audio_activity.duration, 0)
