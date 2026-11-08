"""Shared provider selection for voice, text preview, and model evaluations."""
from voice_agent.config import llm_connection_options, llm_provider


def create_llm_client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(**llm_connection_options(), timeout=30, max_retries=0)


def create_decision_client(client, model, **kwargs):
    if llm_provider() == "gemini":
        from voice_agent.providers.gemini_llm import GeminiDecisionClient
        return GeminiDecisionClient(client, model, **kwargs)
    from voice_agent.providers.foundry_llm import FoundryDecisionClient
    return FoundryDecisionClient(client, model, **kwargs)
