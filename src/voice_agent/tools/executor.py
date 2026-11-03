"""Validate permissions and inputs, inject trusted context, and bound execution."""
import asyncio
from collections import deque
import json
import time
from loguru import logger
from pydantic import ValidationError


class ToolExecutor:
    def __init__(self, registry, *, agent_id, allowed, context, timeout=3.0):
        self.registry = registry
        self.agent_id = agent_id
        self.allowed = frozenset(allowed)
        self.context = context
        self.timeout = timeout
        self.events = deque(maxlen=50)
        self.definitions = registry.definitions(allowed)

    async def execute(self, name, raw_arguments):
        started = time.monotonic()
        result = {"ok": False, "error": "tool_unavailable"}
        tool = self.registry.get(name)
        if tool is None or name not in self.allowed:
            result["error"] = "tool_not_allowed"
        elif self.context is None or self.context.call.agent_id != self.agent_id:
            result["error"] = "context_unavailable"
        else:
            try:
                if not isinstance(raw_arguments, str) or len(raw_arguments) > 2000:
                    raise ValueError("argument size")
                arguments = tool.arguments.model_validate_json(raw_arguments)
                data = await asyncio.wait_for(tool.handler(self.context, arguments), self.timeout)
                if len(json.dumps(data, ensure_ascii=False)) > 12000:
                    raise ValueError("tool result size")
                result = {"ok": True, "data": data}
            except ValidationError:
                result["error"] = "invalid_arguments"
            except asyncio.TimeoutError:
                result["error"] = "tool_timeout"
            except Exception:
                result["error"] = "tool_failed"
        event = dict(name=tool.name if tool and name in self.allowed else "unavailable",
                     outcome="ok" if result["ok"] else result["error"],
                     elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        self.events.append(event)
        # Names are registry-controlled, never log arguments, records, caller IDs, or exceptions.
        logger.info("tool={} outcome={} elapsed_ms={}", event["name"], event["outcome"], event["elapsed_ms"])
        return result
