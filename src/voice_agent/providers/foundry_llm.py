"""Foundry streaming responses and structured survey decisions."""
from loguru import logger
from pipecat.frames.frames import ErrorFrame
from pipecat.services.openai.llm import OpenAILLMService
from voice_agent.conversation.models import Decision


class FoundryLLMService(OpenAILLMService):
    """Native OpenAI-compatible streaming; bound network failures per turn."""
    def create_client(self, **kwargs):
        client = super().create_client(**kwargs)
        return client.with_options(timeout=30, max_retries=0)

    async def _process_context(self, context):
        try:
            await super()._process_context(context)
        except Exception as exc:
            logger.error("Foundry LLM request failed ({})", type(exc).__name__)
            await self.push_error(ErrorFrame("Foundry LLM request failed; retry on the next turn"))


class FoundryDecisionClient:
    def __init__(self, client, model):
        self.client, self.model = client, model

    async def __call__(self, messages):
        response = await self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.2, max_tokens=500,
            stream=False,
            response_format={"type": "json_schema", "json_schema": {
                "name": "survey_decision", "strict": True,
                "schema": Decision.model_json_schema(),
            }},
        )
        if response.choices[0].finish_reason != "stop":
            raise ValueError("Incomplete or refused decision")
        return response.choices[0].message.content, (
            response.usage.model_dump() if response.usage else {})
