"""Repository assets shared by native and Docker entry points."""
import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("VOICE_AGENT_ROOT", Path(__file__).resolve().parents[2]))
VOICE_ASSETS = PROJECT_ROOT / "frontend" / "voice" / "dist"
CHAT_ASSETS = PROJECT_ROOT / "frontend" / "survey-chat"
QUESTIONNAIRES = PROJECT_ROOT / "questionnaires"


def questionnaire_path(value):
    path = Path(value)
    if path.is_absolute() or path.is_file():
        return path
    legacy = {
        "questionnaire.example.json": "example.json",
        "questionnaire.customer-satisfaction.ar.json": "customer-satisfaction.ar.json",
    }
    if str(path) in legacy:
        return QUESTIONNAIRES / legacy[str(path)]
    return PROJECT_ROOT / path
