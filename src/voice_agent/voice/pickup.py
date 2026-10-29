"""Telephone pickup handshake before asking for survey consent."""
import asyncio
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame, ControlFrame,
    EndFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame,
    StartInterruptionFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from voice_agent.voice.playback import PlaybackMarkerFrame


@dataclass
class PickupOpeningFrame(ControlFrame):
    pass


class PickupGreetingProcessor(FrameProcessor):
    """Wait for a caller before introducing the survey; keep consent separate.

    A marker follows all synthesized audio through the paced output queue.
    Synthesis completion alone does not mean the caller has heard a greeting.
    """

    def __init__(self, manager, stop_event, *, pickup_pause=1.0, response_pause=3.0,
                 playback_timeout=30.0):
        super().__init__()
        self.manager = manager
        self.stop_event = stop_event
        self.pickup_pause = pickup_pause
        self.response_pause = response_pause
        self.playback_timeout = playback_timeout
        self._response = asyncio.Event()
        self._response_finished = asyncio.Event()
        self._playback_finished = asyncio.Event()
        self._job = None
        self._ready = False
        self._closed = False
        self._introducing = False

    async def _speak(self, text):
        marker = PlaybackMarkerFrame()
        self._playback_finished = marker.finished
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(text))
        await self.push_frame(LLMFullResponseEndFrame())
        await self.push_frame(marker)

    async def _wait_response(self, timeout):
        try:
            await asyncio.wait_for(self._response.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return self._response.is_set()

    async def _wait_playback(self, *, allow_response):
        playback = asyncio.create_task(self._playback_finished.wait())
        response = asyncio.create_task(self._response.wait()) if allow_response else None
        waits = [playback] + ([response] if response else [])
        try:
            done, _ = await asyncio.wait(waits, timeout=self.playback_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                logger.warning("Telephone opening playback timed out")
                self.stop_event.set()
                return False
            return True
        finally:
            for wait in waits:
                wait.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def _run(self):
        responded = await self._wait_response(self.pickup_pause)
        greetings = (("السلام عليكم", "السلام عليكم", "مرحبا")
                     if self.manager.language == "ar" else ("Hello", "Hello", "Hi"))
        for attempt, greeting in enumerate(greetings, 1):
            if responded or self._response.is_set():
                break
            logger.info("Telephone pickup greeting attempt={}", attempt)
            await self._speak(greeting)
            if not await self._wait_playback(allow_response=True):
                return
            responded = await self._wait_response(self.response_pause)
        if not self._response.is_set():
            logger.info("Telephone pickup ended: no caller response after three greetings")
            self.stop_event.set()
            return

        # VAD stops the retry timer as soon as speech begins. Let the caller
        # finish before introducing the survey, even if STT is delayed.
        await self._response_finished.wait()
        self._introducing = True
        logger.info("Telephone caller responded; introducing survey")
        await self._speak(self.manager.opening())
        if await self._wait_playback(allow_response=False):
            self._ready = True
            logger.info("Telephone survey consent question ready")

    async def process_frame(self, frame, direction):
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self._stop()
        await super().process_frame(frame, direction)
        if direction == FrameDirection.UPSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, PickupOpeningFrame):
            if self._job is None and not self._closed:
                self._job = asyncio.create_task(self._run_guarded())
            return
        if not self._ready and not self._closed:
            if isinstance(frame, StartInterruptionFrame) and self._introducing:
                # Output interruption discards queued markers. A fresh caller
                # reply may interrupt the introduction without timing out.
                self._playback_finished.set()
            if isinstance(frame, UserStartedSpeakingFrame) and not self._introducing:
                self._response.set()
                self._response_finished.clear()
            elif isinstance(frame, UserStoppedSpeakingFrame) and self._response.is_set():
                self._response_finished.set()
            elif isinstance(frame, TranscriptionFrame):
                if frame.text.strip() and not self._introducing:
                    self._response.set()
                    self._response_finished.set()
                # A greeting (including "yes") is never consent to a question
                # the caller has not heard yet. Ignore late opening transcripts.
                return
        await self.push_frame(frame, direction)

    async def _run_guarded(self):
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Telephone opening failed ({})", type(exc).__name__)
            self.stop_event.set()

    async def _stop(self):
        self._closed = True
        if self._job and not self._job.done():
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)

    async def cleanup(self):
        await self._stop()
        await super().cleanup()
