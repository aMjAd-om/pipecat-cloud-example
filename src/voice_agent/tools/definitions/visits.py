from pydantic import BaseModel, ConfigDict, Field


class VisitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    limit: int = Field(ge=1, le=5, description="Maximum number of additional demo visits to retrieve.")


NAME = "get_customer_visits"
DESCRIPTION = (
    "Retrieve additional authorized demo visit history for the customer bound to this call. "
    "Use only when the customer asks for history/details missing from preloaded context. "
    "Do not call for company identity, consent, ratings, or current visit facts already supplied. "
    "No customer ID parameter is accepted."
)
