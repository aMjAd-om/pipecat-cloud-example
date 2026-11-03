"""Context values are data, never model instructions."""
from pydantic import BaseModel, ConfigDict


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Customer(ContextModel):
    id: str
    name: str
    language: str = "ar-OM"


class Interaction(ContextModel):
    organization: str
    branch: str | None = None
    visit_date: str | None = None
    visit_time: str | None = None
    service: str | None = None
    visit_reference: str | None = None


class CallMetadata(ContextModel):
    call_id: str
    agent_id: str | None = None
    caller_organization: str | None = None
    purpose: str | None = None
    authorized_by: str | None = None


class CallContext(ContextModel):
    customer: Customer | None = None
    interaction: Interaction | None = None
    call: CallMetadata
    demo: bool = False
    source: str = "unavailable"

    def for_model(self):
        # No phone, lookup key, or cross-session ID is needed in model context.
        return self.model_dump(exclude={"customer": {"id"}, "call": {"call_id"}})
