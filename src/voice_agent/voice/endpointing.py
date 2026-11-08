"""Join Azure segments into one caller utterance, released after VAD stops."""
import asyncio
import time
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame, EndFrame, ErrorFrame, StartInterruptionFrame, TranscriptionFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame, SystemFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class STTFinalFrame(SystemFrame):
    """Recognition events survive cancellation of an older response's data queue."""
    text: str
    user_id: str
    timestamp: str
    language: str


class UtteranceEndBuffer(FrameProcessor):
    def __init__(self, timing, *, settle_seconds=.1, final_timeout=5.0):
        super().__init__()
        self.timing = timing
        self.settle_seconds, self.final_timeout = settle_seconds, final_timeout
        self._parts = []
        self._characters = 0
        self._speaking = self._closed = self._expired = False
        self._target = self._deadline = None
        self._revision = 0
        self._release_task = self._timeout_task = None

    def _cancel_release(self):
        self._revision += 1
        if self._release_task:
            self._release_task.cancel()
            self._release_task = None

    def _cancel_timeout(self):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None

    def _ready(self):
        if self._closed or self._expired or self._speaking or not self._parts:
            return False
        end = self._parts[-1].metadata.get("stt_audio_end")
        # VAD and recognition have different acoustic boundaries. Allow 150 ms
        # for that difference, while rejecting finals for earlier speech segments.
        return self._target is None or end is None or end >= self._target - .15

    def _schedule(self):
        self._cancel_release()
        if self._ready():
            self._release_task = asyncio.create_task(self._release(self._revision))
        if not self._speaking and self._parts and not self._timeout_task:
            self._deadline = self._deadline or time.monotonic() + self.final_timeout
            self._timeout_task = asyncio.create_task(self._expire())

    async def _release(self, revision):
        last = self._parts[-1]
        # Real telephone frames carry a shared PCM timeline. Without it (e.g.
        # custom transports), allow VAD/late finals to arrive before releasing.
        delay = 0 if self._target is not None and "stt_audio_end" in last.metadata else self.settle_seconds
        await asyncio.sleep(delay)
        if revision != self._revision or not self._ready():
            return
        count, last = len(self._parts), self._parts[-1]
        frame = TranscriptionFrame(" ".join(p.text.strip() for p in self._parts),
                                   last.user_id, last.timestamp, last.language)
        frame.metadata.update(last.metadata)
        frame.metadata["utterance_segments"] = count
        self._parts = []
        self._characters = 0
        self._target = self._deadline = None
        self._release_task = None
        self._cancel_timeout()
        self.timing.mark("utterance_ready")
        logger.info("latency call={} turn={} response={} event=utterance_released segments={} chars={}",
                    self.timing.call_id, self.timing.turn, self.timing.response, count, len(frame.text))
        await self.push_frame(frame)

    async def _expire(self):
        await asyncio.sleep(max(0, self._deadline - time.monotonic()))
        self._timeout_task = None
        if self._speaking or self._closed:
            return
        self._expired = True
        self._cancel_release()
        self._parts = []
        self._characters = 0
        self.timing.mark("utterance_incomplete")
        await self.push_error(ErrorFrame("Speech recognition did not finalize the complete utterance; please repeat"))

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self._shutdown()
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._speaking, self._expired = True, False
            self._deadline = None
            self._cancel_release()
            self._cancel_timeout()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._speaking = False
            self._target = frame.metadata.get("speech_audio_end")
            self._deadline = time.monotonic() + self.final_timeout
            self._schedule()
        elif isinstance(frame, StartInterruptionFrame):
            # UserStartedSpeaking precedes the interruption. Retain unfinished
            # transcript segments if the caller resumes during the release wait.
            self._cancel_release()
        elif isinstance(frame, (STTFinalFrame, TranscriptionFrame)):
            if self._closed or self._expired or not frame.text.strip():
                return
            self._characters += len(frame.text) + bool(self._parts)
            if self._characters > 2000 or len(self._parts) >= 32:
                self._parts = []
                self._expired = True
                self._cancel_release()
                self._cancel_timeout()
                await self.push_error(ErrorFrame("Caller utterance exceeds recognition bounds; please repeat briefly"))
                return
            self._parts.append(frame)
            self._schedule()
            return
        await self.push_frame(frame, direction)

    async def _shutdown(self):
        self._closed = True
        jobs = [job for job in (self._release_task, self._timeout_task) if job]
        self._cancel_release()
        self._cancel_timeout()
        self._parts = []
        self._characters = 0
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def cleanup(self):
        await self._shutdown()
        await super().cleanup()
