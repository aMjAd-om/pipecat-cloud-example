"""Streaming SDK adapters for gaps in the pinned Pipecat 0.0.72 services.

SDK callbacks run on worker threads: marshal every callback onto the asyncio loop.
Recognition remains active during silence and bot speech; Pipecat VAD owns turns.
"""
import asyncio
import os
import time
from collections import deque

import azure.cognitiveservices.speech as speechsdk
from loguru import logger
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601
from voice_agent.observability.diagnostics import speech_cancellation


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
        logger.info("Azure STT recognizer started, 16000 Hz mono PCM; awaiting service events")

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
                if kind == "canceled":
                    logger.warning("Azure STT canceled: {}; reconnecting",
                                   speech_cancellation(event))
                else:
                    logger.warning("Azure STT session stopped; reconnecting")
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
