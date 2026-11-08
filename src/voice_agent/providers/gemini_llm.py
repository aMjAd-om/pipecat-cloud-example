"""Gemini through its OpenAI-compatible endpoint and the shared survey validator."""
from voice_agent.config import gemini_thinking_level
from voice_agent.providers.foundry_llm import FoundryDecisionClient, FoundryLLMService


def gemini_request_options():
    # The pinned OpenAI SDK predates the 'minimal' reasoning_effort enum.
    # The SDK merges extra_body into the request's root. Google's wire format
    # needs a literal 'extra_body' field, so the Python SDK argument must nest it.
    return dict(temperature=1.0, max_tokens=2048, extra_body={"extra_body": {"google": {
        "thinking_config": {"thinking_level": gemini_thinking_level()}}}})


def gemini_messages(messages):
    # Gemini has one system instruction. Keep policy, authoritative state and
    # the final reminder together; separate system messages can replace each
    # other in the compatibility API. Untrusted runtime/tool data stays separate.
    instructions = [m["content"] for m in messages if m["role"] == "system"]
    remaining = [m for m in messages if m["role"] != "system"]
    return ([{"role": "system", "content": "\n\n".join(instructions)}] if instructions else []) + remaining


class GeminiLLMService(FoundryLLMService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        options = gemini_request_options()
        self._settings["temperature"] = options["temperature"]
        self._settings["max_tokens"] = options["max_tokens"]
        self._settings["extra"] = {**self._settings["extra"], "extra_body": options["extra_body"]}

    async def get_chat_completions(self, context, messages):
        return await super().get_chat_completions(context, gemini_messages(messages))


class GeminiDecisionClient(FoundryDecisionClient):
    def _request_options(self, options):
        return {**options, **gemini_request_options(), "messages": gemini_messages(options["messages"])}

    def _tool_call_message(self, call):
        # Gemini's extra_content.google.thought_signature must survive retrieval
        # round trips. OpenAI SDK models retain these provider-specific fields.
        return call.model_dump(exclude_none=True)
