"""Environment validation without exposing credentials."""
import os
from urllib.parse import urlsplit
from dotenv import load_dotenv


def load_settings(*, service=None):
    load_dotenv(override=False, encoding="utf-8-sig")
    required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_OPENAI_API_KEY",
                "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT"]
    if service in ("stt", "tts"):
        required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"]
    elif service == "llm":
        required = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT"]
    missing = [key for key in required if not os.getenv(key) or
               os.environ[key].startswith("your_")]
    if missing:
        raise ValueError("Set configuration in .env: " + ", ".join(missing))
    if service not in ("stt", "tts"):
        url = urlsplit(os.environ["AZURE_OPENAI_BASE_URL"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip("/") != "/openai/v1"):
            raise ValueError("AZURE_OPENAI_BASE_URL must be an HTTPS /openai/v1/ endpoint")
    if os.getenv("DEFAULT_LANGUAGE", "ar") not in ("ar", "en"):
        raise ValueError("DEFAULT_LANGUAGE must be ar or en")
