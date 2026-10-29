"""Monotonic, per-turn measurements. No audio or transcript content is logged."""
import time
from dataclasses import dataclass, field
from loguru import logger
from pipecat.frames.frames import (
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame, LLMTextFrame,
    LLMFullResponseEndFrame, TTSAudioRawFrame, StartInterruptionFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


@dataclass
class TurnTiming:
    turn: int = 0
    stamps: dict = field(default_factory=dict)

    def begin(self):
        self.turn += 1
        self.stamps = {"speech_start": time.monotonic()}

    def mark(self, name, *, once=False, at=None):
        if once and name in self.stamps:
            return
        self.stamps[name] = time.monotonic() if at is None else at
        if name == "speech_end" and "final" in self.stamps:
            self.mark("final", at=self.stamps["final"])
        definitions = {
            "partial": ("speech_start", "stt_first_partial"),
            "final": ("speech_end", "stt_final_after_vad_end"),
            "llm_first": ("llm_start", "llm_ttft"),
            "llm_end": ("llm_start", "llm_total"),
            "tts_first": ("tts_start", "tts_first_audio"),
            "output": ("speech_end", "speech_end_to_transport_audio"),
        }
        if name in definitions:
            start, metric = definitions[name]
            if start in self.stamps:
                logger.info("latency turn={} metric={} ms={:.1f}", self.turn, metric,
                            (self.stamps[name] - self.stamps[start]) * 1000)


class LatencyProbe(FrameProcessor):
    def __init__(self, timing, stage):
        super().__init__()
        self.timing, self.stage = timing, stage

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            t = self.timing
            if self.stage == "input":
                if isinstance(frame, UserStartedSpeakingFrame):
                    t.begin()
                elif isinstance(frame, UserStoppedSpeakingFrame):
                    t.mark("speech_end")
                elif isinstance(frame, StartInterruptionFrame):
                    logger.info("Playback interrupted")
            elif self.stage == "request" and isinstance(frame, OpenAILLMContextFrame):
                t.mark("llm_start")
                for key in ("llm_first", "llm_end", "tts_start", "tts_first", "output"):
                    t.stamps.pop(key, None)
            elif self.stage == "llm":
                if isinstance(frame, LLMTextFrame) and frame.text.strip():
                    t.mark("llm_first", once=True)
                elif isinstance(frame, LLMFullResponseEndFrame):
                    t.mark("llm_end")
            elif self.stage == "audio" and isinstance(frame, TTSAudioRawFrame):
                t.mark("output", once=True)
        await self.push_frame(frame, direction)
