"""Application logging configuration with transcript-safe filtering."""
import os
import sys
from loguru import logger
from voice_agent.config import load_settings


def configure_logging():
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        load_settings()
    except ValueError as exc:
        logger.error("Configuration required: {}", exc)
        raise
    logger.remove()
    # Third-party debug messages can contain transcripts or request bodies.
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"),
               filter=lambda r: r["name"] == "__main__" or r["name"].startswith("voice_agent."))
