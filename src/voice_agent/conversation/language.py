"""Shared language validation and safe spoken responses."""
import re
import unicodedata


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
