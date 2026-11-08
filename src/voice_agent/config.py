"""Environment validation without exposing credentials."""
import os
from urllib.parse import urlsplit
from dotenv import load_dotenv
from voice_agent.paths import PROJECT_ROOT


def llm_provider():
    provider = os.getenv("LLM_PROVIDER", "azure").strip().lower()
    if provider not in ("azure", "gemini"):
        raise ValueError("LLM_PROVIDER must be azure or gemini")
    return provider


def llm_model():
    name, default = (("GEMINI_MODEL", "gemini-3.5-flash-lite") if llm_provider() == "gemini"
                     else ("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    model = os.getenv(name, default).strip()
    if not model:
        raise ValueError(f"{name} cannot be blank")
    return model


def gemini_thinking_level():
    level = os.getenv("GEMINI_THINKING_LEVEL", "minimal").strip().lower()
    if level not in ("minimal", "low", "medium", "high"):
        raise ValueError("GEMINI_THINKING_LEVEL must be minimal, low, medium or high")
    return level


def llm_connection_options():
    if llm_provider() == "gemini":
        return dict(api_key=os.environ["GEMINI_API_KEY"],
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    return dict(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                base_url=os.environ["AZURE_OPENAI_BASE_URL"])


def bounded_integer(name, default, minimum, maximum, *, allow_blank=False):
    raw = os.getenv(name, str(default)).strip()
    if allow_blank and not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def speech_endpoint_settings():
    return dict(
        vad_stop_ms=bounded_integer("TELEPHONY_VAD_STOP_MS", 500, 200, 2000),
        segmentation_ms=bounded_integer("AZURE_STT_SEGMENTATION_SILENCE_MS", 400, 100, 5000,
                                        allow_blank=True),
    )


def output_nonsilent_threshold():
    return bounded_integer("TELEPHONY_NONSILENT_RMS_MIN", 200, 1, 32767)


def load_settings(*, service=None):
    load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")
    if service in (None, "stt"):
        speech_endpoint_settings()
    if service is None:
        output_nonsilent_threshold()
    llm_required = []
    if service not in ("stt", "tts"):
        if llm_provider() == "gemini":
            llm_required = ["GEMINI_API_KEY"]
            gemini_thinking_level()
        else:
            llm_required = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT"]
        llm_model()
    required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", *llm_required]
    if service in ("stt", "tts"):
        required = ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"]
    elif service == "llm":
        required = llm_required
    if service in (None, "tts"):
        from voice_agent.providers.tts import tts_provider
        from voice_agent.providers.cartesia_tts import cartesia_settings
        if tts_provider() == "cartesia":
            cartesia_settings()
            if service == "tts":
                required = []
            required.append("CARTESIA_API_KEY")
        elif tts_provider() == "munsit":
            from voice_agent.providers.munsit_tts import munsit_settings
            munsit_settings()
            if service == "tts":
                required = []
            required.append("MUNSIT_API_KEY")
    missing = [key for key in required if not os.getenv(key) or
               os.environ[key].startswith("your_")]
    if missing:
        raise ValueError("Set configuration in .env: " + ", ".join(missing))
    if service not in ("stt", "tts") and llm_provider() == "azure":
        url = urlsplit(os.environ["AZURE_OPENAI_BASE_URL"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip("/") != "/openai/v1"):
            raise ValueError("AZURE_OPENAI_BASE_URL must be an HTTPS /openai/v1/ endpoint")
    if os.getenv("DEFAULT_LANGUAGE", "ar") not in ("ar", "en"):
        raise ValueError("DEFAULT_LANGUAGE must be ar or en")
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() not in ("true", "false"):
        raise ValueError("SURVEY_CONVERSATION_MANAGER must be true or false")
    if os.getenv("DEMO_TESTING_ENABLED", "false").lower() not in ("true", "false"):
        raise ValueError("DEMO_TESTING_ENABLED must be true or false")
    if os.getenv("AGENT_ID"):
        from voice_agent.agents.runtime import registry
        registry().get(os.environ["AGENT_ID"])
    if os.getenv("TELEPHONY_ENABLED", "false").lower() not in ("true", "false"):
        raise ValueError("TELEPHONY_ENABLED must be true or false")
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() == "true":
        from voice_agent.conversation import Questionnaire
        Questionnaire.load(os.getenv("SURVEY_QUESTIONNAIRE", "questionnaires/customer-satisfaction.ar.json"))
