"""Gemini through its OpenAI-compatible endpoint and the shared survey validator."""
from voice_agent.config import gemini_thinking_level
from voice_agent.providers.foundry_llm import FoundryDecisionClient, FoundryLLMService


def gemini_request_options():
    # The pinned OpenAI SDK predates the 'minimal' reasoning_effort enum.
    # Google's documented extra_body forwards the native thinking configuration.
    return dict(temperature=1.0, max_tokens=2048, extra_body={"google": {
        "thinking_config": {"thinking_level": gemini_thinking_level()}}})


class GeminiLLMService(FoundryLLMService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        options = gemini_request_options()
        self._settings["temperature"] = options["temperature"]
        self._settings["max_tokens"] = options["max_tokens"]
        self._settings["extra"] = {**self._settings["extra"], "extra_body": options["extra_body"]}


class GeminiDecisionClient(FoundryDecisionClient):
    def _request_options(self, options):
        return {**options, **gemini_request_options()}

    def _tool_call_message(self, call):
        # Gemini's extra_content.google.thought_signature must survive retrieval
        # round trips. OpenAI SDK models retain these provider-specific fields.
        return call.model_dump(exclude_none=True)
