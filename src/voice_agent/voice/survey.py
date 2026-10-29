"""Survey-only Pipecat adapter; the existing speech/transport adapters are unchanged."""
import asyncio
from dataclasses import dataclass

from pipecat.frames.frames import (
    CancelFrame, ControlFrame, EndFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, LLMTextFrame, StartInterruptionFrame, TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from voice_agent.voice.playback import PlaybackMarkerFrame


@dataclass
class SurveyOpeningFrame(ControlFrame):
    pass


class SurveyConversationProcessor(FrameProcessor):
    def __init__(self, manager, decide, timing):
        super().__init__()
        self.manager, self.decide, self.timing = manager, decide, timing
        self._follow_up_task = None

    async def queue_frame(self, frame, direction=FrameDirection.DOWNSTREAM, callback=None):
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            # Capture on arrival, before the processor's queue waits for an earlier
            # request. Two finals heard under one question cannot answer two questions.
            frame.metadata.setdefault("survey_snapshot", self.manager.snapshot())
        await super().queue_frame(frame, direction, callback)

    async def _speak(self, text):
        if text:
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(text))
            await self.push_frame(LLMFullResponseEndFrame())

    async def process_frame(self, frame, direction):
        if isinstance(frame, (StartInterruptionFrame, UserStartedSpeakingFrame,
                              TranscriptionFrame, CancelFrame, EndFrame)):
            await self._cancel_follow_up()
        if isinstance(frame, (StartInterruptionFrame, CancelFrame, EndFrame)):
            self.manager.interrupt()
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
        elif isinstance(frame, SurveyOpeningFrame):
            await self._speak(self.manager.opening())
        elif isinstance(frame, TranscriptionFrame):
            self.timing.mark("llm_start")
            for key in ("llm_first", "llm_end", "tts_start", "tts_first", "output"):
                self.timing.stamps.pop(key, None)
            identity = (frame.user_id, frame.timestamp) if frame.timestamp else (frame.id,)
            result = await self.manager.turn(identity, frame.text, self.decide,
                                            snapshot=frame.metadata.get("survey_snapshot"))
            # An interrupted request must not speak after a newer user turn.
            if result.reason != "stale":
                await self._speak(result.spoken_response)
                if result.follow_up:
                    marker = PlaybackMarkerFrame()
                    await self.push_frame(marker)
                    self._follow_up_task = asyncio.create_task(self._follow_up(
                        result, marker, self.manager.version, self.manager.question.id))
        else:
            await self.push_frame(frame, direction)

    async def _follow_up(self, result, marker, version, question_id):
        try:
            await asyncio.wait_for(marker.finished.wait(), 30)
        except asyncio.TimeoutError:
            return
        await asyncio.sleep(result.pause_seconds)
        if (self.manager.version == version and self.manager.question.id == question_id
                and not self.manager.paused and not self.manager.completed
                and not self.manager.cancelled):
            await self._speak(result.follow_up)

    async def _cancel_follow_up(self):
        if self._follow_up_task and not self._follow_up_task.done():
            self._follow_up_task.cancel()
            await asyncio.gather(self._follow_up_task, return_exceptions=True)
        self._follow_up_task = None

    async def cleanup(self):
        await self._cancel_follow_up()
        self.manager.clear()
        await super().cleanup()
