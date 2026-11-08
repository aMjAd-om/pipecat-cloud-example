"""Join Azure segments into one caller utterance, released after VAD stops."""
import asyncio
import time
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame, ControlFrame, EndFrame, ErrorFrame, StartInterruptionFrame, TranscriptionFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame, SystemFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class STTFinalFrame(SystemFrame):
    """Recognition events survive cancellation of an older response's data queue."""
    text: str
    user_id: str
    timestamp: str
    language: str


@dataclass
class STTProgressFrame(SystemFrame):
    """Recognition activity only; partial text never leaves the STT adapter."""
    generation: int = 0
    start: float | None = None


@dataclass
class UtteranceRetryFrame(ControlFrame):
    turn: int = 0
    reason: str = "recognition_timeout"


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
        self._pending_recognition = False
        self._pending_start = self._recognized_until = None
        self._generation = None
        self._recognition_gap = False
        self._retry_pending = None

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
        return bool(self._parts and not (self._closed or self._expired or self._speaking
                    or self._pending_recognition or self._recognition_gap))

    def _track_generation(self, generation):
        if generation is None or generation == self._generation:
            return
        if self._generation is not None:
            self._recognition_gap = bool(self._speaking or self._parts or self._pending_recognition)
            self._parts = []
            self._characters = 0
            self._pending_recognition = False
            self._pending_start = self._recognized_until = None
        self._generation = generation

    def _log_endpoint(self):
        end = self._parts[-1].metadata.get("stt_audio_end") if self._parts else None
        gap = (self._target - end) * 1000 if self._target is not None and end is not None else None
        logger.info("latency call={} turn={} response={} event=utterance_endpoint_check "
                    "vad_minus_final_ms={} pending_recognition={} recognition_gap={}",
                    self.timing.call_id, self.timing.turn, self.timing.response,
                    round(gap, 1) if gap is not None else "unknown",
                    self._pending_recognition, self._recognition_gap)

    def _schedule(self):
        self._cancel_release()
        if self._ready():
            self._release_task = asyncio.create_task(self._release(self._revision))
        if (not self._speaking and not self._expired and not self._timeout_task
                and (self._parts or self._pending_recognition or self._deadline is not None)):
            self._deadline = self._deadline or time.monotonic() + self.final_timeout
            self._timeout_task = asyncio.create_task(self._expire())

    async def _release(self, revision):
        # Give a late partial/final or resumed speech a short opportunity to
        # cancel release. Azure word endpoints need not match the VAD endpoint.
        await asyncio.sleep(self.settle_seconds)
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
        self._pending_start = None
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
        await self._fail("recognition_timeout")

    async def _fail(self, reason):
        if self._expired or self._closed:
            return
        turn = self.timing.turn
        self._expired = True
        self._cancel_release()
        self._cancel_timeout()
        self._parts = []
        self._characters = 0
        self._pending_recognition = self._recognition_gap = False
        self._pending_start = None
        self.timing.mark("utterance_incomplete")
        await self.push_error(ErrorFrame("Speech recognition incomplete; requesting a repeat"))
        if self._speaking:
            self._retry_pending = (turn, reason)
        else:
            await self.push_frame(UtteranceRetryFrame(turn, reason))

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self._shutdown()
        elif isinstance(frame, UserStartedSpeakingFrame):
            if self._expired:
                self._pending_recognition = self._recognition_gap = False
                self._pending_start = None
            self._retry_pending = None
            self._speaking, self._expired = True, False
            self._deadline = None
            self._cancel_release()
            self._cancel_timeout()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._speaking = False
            self._target = frame.metadata.get("speech_audio_end")
            self._deadline = time.monotonic() + self.final_timeout
            self._log_endpoint()
            await self.push_frame(frame, direction)
            if self._retry_pending:
                await self.push_frame(UtteranceRetryFrame(*self._retry_pending))
                self._retry_pending = None
            self._schedule()
            return
        elif isinstance(frame, StartInterruptionFrame):
            # UserStartedSpeaking precedes the interruption. Retain unfinished
            # transcript segments if the caller resumes during the release wait.
            self._cancel_release()
        elif isinstance(frame, STTProgressFrame):
            if self._closed or self._expired:
                return
            self._track_generation(frame.generation)
            # Late hypotheses for a phrase already finalized must not reopen it.
            if (frame.start is not None and self._recognized_until is not None
                    and frame.start < self._recognized_until - 1e-6):
                return
            self._pending_recognition = True
            self._pending_start = frame.start
            self._schedule()
            return
        elif isinstance(frame, (STTFinalFrame, TranscriptionFrame)):
            if self._closed or self._expired or not frame.text.strip():
                return
            self._track_generation(frame.metadata.get("stt_generation"))
            end = frame.metadata.get("stt_recognition_end")
            if (end is not None and self._recognized_until is not None
                    and end <= self._recognized_until + 1e-6):
                return
            if end is not None:
                self._recognized_until = end
            # Compare phrase activity inside Azure's own clock. A later phrase's
            # partial cannot be cleared by a final from an earlier phrase.
            if self._pending_start is None or end is None or end >= self._pending_start - .05:
                self._pending_recognition = False
                self._pending_start = None
            self._characters += len(frame.text) + bool(self._parts)
            if self._characters > 2000 or len(self._parts) >= 32:
                await self._fail("utterance_bounds")
                return
            self._parts.append(frame)
            self._log_endpoint()
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


class UtteranceRecoveryProcessor(FrameProcessor):
    """Speak a retry through the normal TTS path without creating an LLM turn."""
    def __init__(self, timing, language):
        super().__init__()
        self.timing, self.language = timing, language
        self._speaking = self._closed = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, UserStartedSpeakingFrame):
                self._speaking = True
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._speaking = False
            elif isinstance(frame, (CancelFrame, EndFrame)):
                self._closed = True
            elif isinstance(frame, UtteranceRetryFrame):
                if self._closed or self._speaking or frame.turn != self.timing.turn:
                    return
                self.timing.start_response()
                self.timing.mark("recognition_retry")
                text = ("Sorry, I didn't catch that. Could you repeat your answer?" if self.language == "en"
                        else "السموحة، ما وضحت لي إجابتك. ممكن تعيدها؟")
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame(text))
                await self.push_frame(LLMFullResponseEndFrame())
                return
        await self.push_frame(frame, direction)
