"""Deterministic preload; replace the demo repository with an authorized backend later."""
import json
from pathlib import Path
from voice_agent.context.models import CallContext, CallMetadata, Customer, Interaction


class DemoRepository:
    def __init__(self, path=None):
        self._records = json.loads(Path(path or Path(__file__).with_name("demo_records.json"))
                                   .read_text(encoding="utf-8-sig"))

    def choices(self, agent_id):
        return [dict(id=r["mock_id"], label=r["label"], demo=True)
                for r in self._records if r["agent_id"] == agent_id]

    def select(self, agent_id, mock_id):
        for record in self._records:
            if record["agent_id"] == agent_id and record["mock_id"] == mock_id:
                return json.loads(json.dumps(record))  # Never share mutable records between calls.
        raise ValueError("mock customer is not authorized for this agent")

    async def recent_visits(self, context, limit):
        if not context.demo or context.customer is None:
            return []
        # Both IDs come from backend-created context, never model arguments.
        for record in self._records:
            if (record["agent_id"] == context.call.agent_id
                    and record["customer"]["id"] == context.customer.id):
                return [Interaction.model_validate(v).model_dump()
                        for v in record["visits"][:limit]]
        return []


class ContextLoader:
    def __init__(self, repository=None):
        self.repository = repository or DemoRepository()

    def load(self, config, session_id, mock_id=None):
        call = CallMetadata(call_id=session_id, agent_id=config.agent_id)
        if mock_id is None:
            return CallContext(call=call)
        record = self.repository.select(config.agent_id, mock_id)
        requested = set(config.context)
        return CallContext(
            call=call.model_copy(update={
                "caller_organization": record["caller_organization"],
                "purpose": record["purpose"],
                "authorized_by": "Demo test operator; no bank authorization implied",
            }) if "call" in requested else call,
            customer=Customer.model_validate(record["customer"]) if "customer_profile" in requested else None,
            interaction=(Interaction.model_validate(record["visits"][0])
                         if "recent_interaction" in requested and record["visits"] else None),
            demo=True, source="synthetic demo fixtures",
        )
