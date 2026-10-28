"""Fixed session speech language, checked before any model text reaches TTS."""
import re
import unicodedata

from pipecat.frames.frames import (
    CancelFrame, EndFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame,
    LLMTextFrame, StartInterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def speech_matches_language(text, language):
    if language == "ar":
        return not any(c.isalpha() and "LATIN" in unicodedata.name(c, "") for c in text)
    return not re.search(r"[\u0620-\u064a\u066e-\u06d3\u0750-\u077f\u08a0-\u08c9]", text)


def safe_spoken_text(text, language, *, overflow=False):
    """Shared final speech boundary for voice calls and the local chat preview."""
    if not overflow and speech_matches_language(text, language):
        return text
    return ("خلّنا نكمل بالعربية. ممكن تعيد إجابتك؟" if language == "ar" else
            "Let's continue in English. Could you repeat your answer?")


class LanguageLockedResponse(FrameProcessor):
    """Buffer a response so a later English token cannot follow spoken Arabic.

    The survey renderer normally handles the language already. This final boundary
    also covers the legacy streaming assistant and configured questionnaire text.
    """
    def __init__(self, language):
        super().__init__()
        self.language = language
        self._parts = None
        self._size = 0
        self._overflow = False

    def _safe(self, text):
        return safe_spoken_text(text, self.language, overflow=self._overflow)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (StartInterruptionFrame, CancelFrame, EndFrame)):
            self._parts = None
            self._size = 0
            self._overflow = False
        if isinstance(frame, LLMFullResponseStartFrame):
            self._parts = []
            self._size = 0
            self._overflow = False
        elif isinstance(frame, LLMTextFrame):
            if self._parts is not None:
                self._size += len(frame.text)
                if self._size <= 6000:
                    self._parts.append(frame.text)
                else:
                    self._overflow = True
                return
            await self.push_frame(LLMTextFrame(self._safe(frame.text)), direction)
            return
        elif isinstance(frame, LLMFullResponseEndFrame) and self._parts is not None:
            text = self._safe("".join(self._parts))
            self._parts = None
            self._overflow = False
            await self.push_frame(LLMTextFrame(text), direction)
        await self.push_frame(frame, direction)
