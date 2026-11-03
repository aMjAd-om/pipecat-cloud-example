"""Only explicitly registered async functions can be exposed or executed."""
from dataclasses import dataclass
from functools import partial
import inspect
import re
from pydantic import BaseModel
from voice_agent.conversation.models import DECISION_FUNCTION_NAME


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: object

    def schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "strict": True,
            "parameters": self.arguments.model_json_schema(),
        }}


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name, description, arguments, handler):
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
                or name in self._tools or name == DECISION_FUNCTION_NAME):
            raise ValueError("invalid or duplicate tool name")
        if not issubclass(arguments, BaseModel) or not inspect.iscoroutinefunction(handler):
            raise ValueError("tool requires a Pydantic schema and async handler")
        schema = arguments.model_json_schema()
        if (schema.get("additionalProperties") is not False
                or set(schema.get("required", [])) != set(schema.get("properties", {}))):
            raise ValueError("strict tools require all arguments and forbid extra fields")
        parameters = inspect.signature(handler).parameters
        if tuple(k for k, v in parameters.items() if v.default is inspect.Parameter.empty) != ("context", "arguments"):
            raise ValueError("handler must accept context and arguments")
        self._tools[name] = Tool(name, description, arguments, handler)

    def get(self, name):
        return self._tools.get(name)

    @property
    def names(self):
        return frozenset(self._tools)

    def definitions(self, allowed):
        if not set(allowed) <= self.names:
            raise ValueError("unknown configured tool")
        return [self._tools[name].schema() for name in allowed]


def default_tools(repository=None):
    from voice_agent.context.loader import DemoRepository
    from voice_agent.tools.definitions.visits import NAME, DESCRIPTION, VisitArguments
    from voice_agent.tools.handlers.visits import get_customer_visits
    registry = ToolRegistry()
    registry.register(NAME, DESCRIPTION, VisitArguments,
                      partial(get_customer_visits, repository=repository or DemoRepository()))
    return registry
