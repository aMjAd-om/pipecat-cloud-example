"""Bounded provider diagnostics for backend logs and command-line checks."""
import os
import re


def safe_diagnostic(value):
    text = str(value)
    for name in ("AZURE_SPEECH_KEY", "AZURE_OPENAI_API_KEY", "TURN_SECRET"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    # SDK connection errors may contain endpoint URLs with credential query strings.
    text = re.sub(r"(?:https?|wss?)://[^\s<>\"']+", "[endpoint]", text)
    return " ".join(text.split())[:2000]


def speech_cancellation(details):
    return safe_diagnostic(
        f"reason={getattr(details, 'reason', 'unknown')} "
        f"code={getattr(details, 'error_code', 'unknown')} "
        f"details={getattr(details, 'error_details', '')}"
    )
