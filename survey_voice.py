"""Survey-only Pipecat adapter; the existing speech/transport adapters are unchanged."""
from dataclasses import dataclass

from pipecat.frames.frames import (
    CancelFrame, ControlFrame, EndFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, LLMTextFrame, StartInterruptionFrame, TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from survey import Decision


@dataclass
class SurveyOpeningFrame(ControlFrame):
    pass


class FoundryDecisionClient:
    def __init__(self, client, model):
        self.client, self.model = client, model

    async def __call__(self, messages):
        response = await self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.2, max_tokens=500,
            stream=False,
            response_format={"type": "json_schema", "json_schema": {
                "name": "survey_decision", "strict": True,
                "schema": Decision.model_json_schema(),
            }},
        )
        if response.choices[0].finish_reason != "stop":
            raise ValueError("Incomplete or refused decision")
        return response.choices[0].message.content, (
            response.usage.model_dump() if response.usage else {})


class SurveyConversationProcessor(FrameProcessor):
    def __init__(self, manager, decide, timing):
        super().__init__()
        self.manager, self.decide, self.timing = manager, decide, timing

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
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self):
        self.manager.clear()
        await super().cleanup()
