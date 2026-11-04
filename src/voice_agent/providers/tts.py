"""Speech provider selection and synthesis factory."""
import os
from loguru import logger
from voice_agent.providers.cartesia_tts import StreamingCartesiaTTS


def tts_provider():
    provider = os.getenv("TTS_PROVIDER", "azure").strip().lower()
    if provider not in ("azure", "cartesia", "munsit"):
        raise ValueError("TTS_PROVIDER must be azure, cartesia or munsit")
    return provider


def create_tts(*, timing=None, language=None, preferences=None):
    provider = (preferences.provider if preferences else None) or tts_provider()
    options = {}
    if language:
        options["language"] = language
    if preferences and preferences.voice_id:
        options["voice_id"] = preferences.voice_id
    logger.info("TTS provider={}", provider)
    if provider == "cartesia":
        return StreamingCartesiaTTS(timing=timing, **options)
    if provider == "munsit":
        from voice_agent.providers.munsit_tts import StreamingMunsitTTS
        return StreamingMunsitTTS(timing=timing, **options)
    from voice_agent.providers.azure_tts import StreamingAzureTTS
    return StreamingAzureTTS(timing=timing, **options)
