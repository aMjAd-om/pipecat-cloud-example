"""Environment validation without exposing credentials."""
import os
from urllib.parse import urlsplit
from dotenv import load_dotenv
from voice_agent.paths import PROJECT_ROOT


def load_settings(*, service=None):
    load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")
    required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_OPENAI_API_KEY",
                "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT"]
    if service in ("stt", "tts"):
        required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"]
    elif service == "llm":
        required = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT"]
    if service in (None, "tts"):
        from voice_agent.providers.tts import tts_provider
        from voice_agent.providers.cartesia_tts import cartesia_settings
        if tts_provider() == "cartesia":
            cartesia_settings()
            if service == "tts":
                required = []
            required.append("CARTESIA_API_KEY")
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
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() not in ("true", "false"):
        raise ValueError("SURVEY_CONVERSATION_MANAGER must be true or false")
    if os.getenv("TELEPHONY_ENABLED", "false").lower() not in ("true", "false"):
        raise ValueError("TELEPHONY_ENABLED must be true or false")
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() == "true":
        from voice_agent.conversation import Questionnaire
        Questionnaire.load(os.getenv("SURVEY_QUESTIONNAIRE", "questionnaires/customer-satisfaction.ar.json"))
