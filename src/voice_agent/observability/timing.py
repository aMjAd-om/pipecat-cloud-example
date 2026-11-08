"""Call-correlated monotonic timings. Never log audio or transcript content."""
import copy
import time
from dataclasses import dataclass, field
from uuid import uuid4
from loguru import logger
from pipecat.frames.frames import (
    ControlFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame, LLMTextFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, TTSAudioRawFrame,
    TTSStoppedFrame, StartInterruptionFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


@dataclass
class TurnTiming:
    turn: int = 0
    stamps: dict = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: uuid4().hex[:12])
    response: int = 0
    origin: float = field(default_factory=time.monotonic)
    last_audio_end: float | None = None

    def begin(self):
        self.turn += 1
        self.stamps = {}
        self.mark("speech_start")
        if self.last_audio_end is not None:
            self.metric("transport_tail_to_user_start", self.last_audio_end,
                        self.stamps["speech_start"])

    def start_response(self):
        self.response += 1
        # Replace rather than mutate: queued playback keeps its original trace.
        self.stamps = {k: v for k, v in self.stamps.items() if k in (
            "speech_start", "speech_end", "partial", "final", "final_dispatched")}
        self.mark("llm_start")

    def snapshot(self):
        return copy.copy(self)

    def metric(self, name, start, end):
        logger.info("latency call={} turn={} response={} metric={} ms={:.1f}",
                    self.call_id, self.turn, self.response, name, (end - start) * 1000)

    def mark(self, name, *, once=False, at=None):
        if once and name in self.stamps:
            return
        self.stamps[name] = time.monotonic() if at is None else at
        logger.info("latency call={} turn={} response={} event={} at_ms={:.1f}",
                    self.call_id, self.turn, self.response, name,
                    (self.stamps[name] - self.origin) * 1000)
        definitions = {
            "partial": (("speech_start", "stt_first_partial"),),
            "final": (("speech_end", "stt_final_after_vad_end"),),
            "final_dispatched": (("final", "stt_callback_to_dispatch"),),
            "llm_start": (("final_dispatched", "transcript_to_response_start"),),
            "decision_start": (("llm_start", "decision_preparation"),),
            "decision_end": (("decision_start", "decision_model_and_tools"),),
            "llm_first": (("llm_start", "llm_ttft"),),
            "llm_end": (("llm_start", "llm_total"),),
            "language_ready": (("llm_first", "response_buffer_hold"),),
            "tts_start": (("language_ready", "text_to_tts_request"),),
            "tts_first": (("tts_start", "tts_first_audio"),),
            "output": (("speech_end", "speech_end_to_transport_queue"),),
            "transport_first": (("output", "transport_queue_wait"),
                                ("speech_end", "speech_end_to_transport_audio"),
                                ("llm_end", "llm_end_to_transport_audio")),
            "transport_tail": (("llm_end", "llm_end_to_transport_tail"),),
        }
        for start, metric in definitions.get(name, ()):
            if start in self.stamps:
                self.metric(metric, self.stamps[start], self.stamps[name])
        # Azure can finalize before VAD's silence window expires. Keep the sign.
        if name == "speech_end" and "final" in self.stamps:
            self.metric("stt_final_after_vad_end", self.stamps["speech_end"], self.stamps["final"])


@dataclass
class LatencyPlaybackFrame(ControlFrame):
    timing: TurnTiming | None = None


class LatencyProbe(FrameProcessor):
    def __init__(self, timing, stage):
        super().__init__()
        self.timing, self.stage = timing, stage
        self._audio_response = None

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
                    t.mark("interruption")
            elif self.stage == "request" and isinstance(frame, OpenAILLMContextFrame):
                t.start_response()
            elif self.stage == "llm":
                if isinstance(frame, LLMFullResponseStartFrame) and "llm_start" not in t.stamps:
                    t.start_response()
                elif isinstance(frame, LLMTextFrame) and frame.text.strip():
                    t.mark("llm_first", once=True)
                elif isinstance(frame, LLMFullResponseEndFrame):
                    t.mark("llm_end")
            elif self.stage == "language" and isinstance(frame, LLMTextFrame) and frame.text.strip():
                t.mark("language_ready", once=True)
            elif self.stage == "audio":
                if isinstance(frame, TTSAudioRawFrame):
                    t.mark("output", once=True)
                    if self._audio_response != t.response:
                        self._audio_response = t.response
                        await self.push_frame(LatencyPlaybackFrame(timing=t.snapshot()), direction)
                elif isinstance(frame, TTSStoppedFrame):
                    t.mark("tts_end")
        await self.push_frame(frame, direction)
