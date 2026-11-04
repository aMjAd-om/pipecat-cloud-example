"""Validated, immutable agent configuration and session selection."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class VoicePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: Literal["azure", "cartesia", "munsit"] | None = None
    voice_id: str | None = Field(default=None, min_length=1, max_length=200)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    prompt_file: str = Field(min_length=1, max_length=200)
    workflow_type: Literal["survey"] = "survey"
    survey_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    survey_ids: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    context: tuple[Literal["customer_profile", "recent_interaction", "call"], ...] = ()
    language: Literal["ar", "ar-OM", "en", "en-US"] = "ar-OM"
    voice: VoicePreferences | None = None
    end_call_on_finish: bool = True

    @model_validator(mode="after")
    def survey_required(self):
        if self.workflow_type == "survey" and not self.survey_id:
            raise ValueError("survey workflow requires survey_id")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("duplicate agent tools")
        return self

    @property
    def output_language(self):
        return self.language.split("-")[0]

    @property
    def allowed_surveys(self):
        return tuple(dict.fromkeys((self.survey_id, *self.survey_ids)))


class SessionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    agent_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    survey_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    mock_customer_id: str | None = Field(default=None, pattern=r"^demo-[a-z0-9-]{1,70}$")
