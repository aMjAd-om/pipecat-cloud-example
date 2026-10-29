"""Speech provider selection and synthesis factory."""
import os
from loguru import logger
from voice_agent.providers.cartesia_tts import StreamingCartesiaTTS


def tts_provider():
    provider = os.getenv("TTS_PROVIDER", "azure").strip().lower()
    if provider not in ("azure", "cartesia"):
        raise ValueError("TTS_PROVIDER must be azure or cartesia")
    return provider


def create_tts(*, timing=None):
    provider = tts_provider()
    logger.info("TTS provider={}", provider)
    if provider == "cartesia":
        return StreamingCartesiaTTS(timing=timing)
    from voice_agent.providers.azure_tts import StreamingAzureTTS
    return StreamingAzureTTS(timing=timing)
