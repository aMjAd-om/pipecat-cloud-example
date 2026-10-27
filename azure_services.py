"""Streaming SDK adapters for gaps in the pinned Pipecat 0.0.72 services.

SDK callbacks run on worker threads: marshal every callback onto the asyncio loop.
Recognition remains active during silence and bot speech; Pipecat VAD owns turns.
"""
import asyncio
import os
import re
import time
from collections import deque
from xml.sax.saxutils import escape

import azure.cognitiveservices.speech as speechsdk
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame, TranscriptionFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame,
)
from pipecat.services.stt_service import STTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.azure.tts import AzureBaseTTSService, sample_rate_to_output_format
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class FoundryLLMService(OpenAILLMService):
    """Native OpenAI-compatible streaming; bound network failures per turn."""
    def create_client(self, **kwargs):
        client = super().create_client(**kwargs)
        return client.with_options(timeout=30, max_retries=0)

    async def _process_context(self, context):
        try:
            await super()._process_context(context)
        except Exception as exc:
            logger.error("Foundry LLM request failed ({})", type(exc).__name__)
            await self.push_error(ErrorFrame("Foundry LLM request failed; retry on the next turn"))


class StreamingAzureSTT(STTService):
    def __init__(self, *, timing):
        super().__init__(sample_rate=16000, audio_passthrough=False)
        self.timing = timing
        self._recognizer = self._stream = self._worker = None
        self._generation = 0
        self._closed = True
        self._seen = deque(maxlen=256)
        self._events = asyncio.Queue(maxsize=256)
        self._reconnect_lock = asyncio.Lock()

    def _event(self, generation, kind, event):
        at = time.monotonic()
        def enqueue():
            if not self._closed and generation == self._generation:
                if self._events.full():
                    # Prefer recognition finals/errors over stale partials.
                    self._events.get_nowait()
                self._events.put_nowait((generation, kind, event, at))
        self.get_event_loop().call_soon_threadsafe(enqueue)

    async def start(self, frame):
        await super().start(frame)
        self._closed = False
        try:
            await self._connect()
        except Exception:
            await self._recover()
        self._worker = asyncio.create_task(self._consume())

    async def _connect(self):
        self._generation += 1
        generation = self._generation
        config = speechsdk.SpeechConfig(
            subscription=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
        )
        config.speech_recognition_language = os.getenv("AZURE_STT_LANGUAGE", "ar-OM")
        stream = speechsdk.audio.PushAudioInputStream(speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000, bits_per_sample=16, channels=1,
        ))
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=config, audio_config=speechsdk.audio.AudioConfig(stream=stream))
        self._stream, self._recognizer = stream, recognizer
        for signal, kind in ((recognizer.recognizing, "partial"),
                             (recognizer.recognized, "final"),
                             (recognizer.canceled, "canceled"),
                             (recognizer.session_stopped, "stopped")):
            signal.connect(lambda event, k=kind: self._event(generation, k, event))
        await asyncio.wait_for(asyncio.to_thread(
            lambda: recognizer.start_continuous_recognition_async().get()), 15)
        logger.info("Azure STT continuous recognition connected, 16000 Hz mono PCM")

    async def _disconnect(self):
        self._generation += 1  # Invalidate callbacks before stopping the old connection.
        recognizer, stream = self._recognizer, self._stream
        self._recognizer = self._stream = None
        try:
            if recognizer:
                await asyncio.wait_for(asyncio.to_thread(
                    lambda: recognizer.stop_continuous_recognition_async().get()), 5)
        except Exception:
            logger.warning("Azure STT stop timed out/failed")
        finally:
            if stream:
                stream.close()

    async def _recover(self):
        generation = self._generation
        async with self._reconnect_lock:
            if generation != self._generation or self._closed:
                return
            await self._retry_connect()

    async def _retry_connect(self):
        for delay in (0.5, 1, 2):
            if self._closed:
                return
            await self._disconnect()
            await asyncio.sleep(delay)
            try:
                await self._connect()
                return
            except Exception:
                logger.warning("Azure STT reconnect failed")
        await self.push_error(ErrorFrame("Azure STT unavailable after reconnect attempts", fatal=True))

    async def _consume(self):
        while True:
            generation, kind, event, at = await self._events.get()
            if generation != self._generation:
                continue
            if kind in ("canceled", "stopped"):
                logger.warning("Azure STT recognition connection ended; reconnecting")
                await self._recover()
                continue
            result = event.result
            if not result.text:
                continue  # Silence/NoMatch never becomes an LLM turn.
            if kind == "partial":
                self.timing.mark("partial", once=True, at=at)
                logger.info("STT partial event chars={}", len(result.text))
                continue  # Never send partials to the context aggregator.
            if result.reason != speechsdk.ResultReason.RecognizedSpeech:
                continue
            identity = (generation, result.result_id)
            if identity in self._seen:
                continue
            self._seen.append(identity)
            self.timing.mark("final", at=at)
            logger.info("STT final event chars={}", len(result.text))
            await self.push_frame(TranscriptionFrame(
                result.text, "", time_now_iso8601(),
                os.getenv("AZURE_STT_LANGUAGE", "ar-OM")))

    async def run_stt(self, audio):
        if self._stream:
            try:
                self._stream.write(audio)
            except Exception:
                logger.warning("Azure STT audio write failed")
                await self._recover()
        yield None

    async def close(self):
        self._closed = True
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        await self._disconnect()

    async def stop(self, frame):
        await self.close()
        await super().stop(frame)

    async def cancel(self, frame):
        await self.close()
        await super().cancel(frame)


def text_language(text, fallback="ar"):
    if re.search(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]", text):
        return "ar"
    if re.search(r"[a-zA-Z]", text):
        return "en"
    return fallback


class StreamingAzureTTS(AzureBaseTTSService):
    """Use native sentence aggregation/SSML, with isolated streaming SDK requests.

    A fresh synthesizer per sentence prevents old callbacks reaching a new turn.
    Finally stops synthesis on cancellation, including Pipecat barge-in.
    """
    def __init__(self, *, timing):
        super().__init__(api_key=os.environ["AZURE_SPEECH_KEY"],
                         region=os.environ["AZURE_SPEECH_REGION"], sample_rate=24000,
                         params=self.InputParams(language=Language.AR))
        self.timing = timing
        self._active = set()
        self._language = os.getenv("DEFAULT_LANGUAGE", "ar")

    async def run_tts(self, text):
        self._language = text_language(text, self._language)
        self._voice_id = os.getenv("AZURE_TTS_VOICE_AR", "ar-OM-AyshaNeural") \
            if self._language == "ar" else os.getenv("AZURE_TTS_VOICE_EN", "en-US-JennyNeural")
        self._settings["language"] = "ar-OM" if self._language == "ar" else "en-US"
        config = speechsdk.SpeechConfig(subscription=self._api_key, region=self._region)
        config.set_speech_synthesis_output_format(sample_rate_to_output_format(self.sample_rate))
        synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        self._active.add(synth)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        accepting = True

        def emit(kind, data=None):
            def enqueue():
                if accepting:
                    queue.put_nowait((kind, data))
            loop.call_soon_threadsafe(enqueue)

        synth.synthesizing.connect(lambda e: emit("audio", bytes(e.result.audio_data))
                                  if e.result.audio_data else None)
        synth.synthesis_completed.connect(lambda e: emit("done"))
        synth.synthesis_canceled.connect(lambda e: emit("error"))
        self.timing.mark("tts_start", once=True)
        try:
            yield TTSStartedFrame()
            synth.speak_ssml_async(self._construct_ssml(escape(text)))
            while True:
                kind, data = await asyncio.wait_for(queue.get(), 20)
                if kind == "done":
                    break
                if kind == "error":
                    yield ErrorFrame("Azure TTS request canceled or failed")
                    break
                self.timing.mark("tts_first", once=True)
                yield TTSAudioRawFrame(data, self.sample_rate, 1)
        except asyncio.TimeoutError:
            yield ErrorFrame("Azure TTS audio timeout")
        except Exception:
            yield ErrorFrame("Azure TTS synthesis failed")
        finally:
            accepting = False
            await self._stop_synth(synth)
            self._active.discard(synth)
        yield TTSStoppedFrame()

    async def _stop_synth(self, synth):
        try:
            await asyncio.wait_for(asyncio.to_thread(
                lambda: synth.stop_speaking_async().get()), 5)
        except Exception:
            logger.warning("Azure TTS stop timed out/failed")
        finally:
            for signal in (synth.synthesizing, synth.synthesis_completed, synth.synthesis_canceled):
                signal.disconnect_all()

    async def close(self):
        await asyncio.gather(*(self._stop_synth(s) for s in list(self._active)))
        self._active.clear()

    async def stop(self, frame):
        await self.close()
        await super().stop(frame)

    async def cancel(self, frame):
        await self.close()
        await super().cancel(frame)
