"""Validated survey inputs and immutable turn results."""
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from voice_agent.paths import questionnaire_path


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["rating", "text", "choice", "consent", "conditional_text"]
    choices: tuple[str, ...] = ()
    insufficient_answers: tuple[str, ...] = ()
    minimum: int = 1
    maximum: int = 5
    max_length: int = Field(default=1000, ge=1, le=4000)
    optional: bool = False
    relevance: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.kind in ("choice", "consent") and (
                len(self.choices) < 2 or len(set(self.choices)) != len(self.choices)):
            raise ValueError("choice questions need at least two unique choices")
        if self.kind == "consent" and (self.optional or self.choices != ("نعم", "لا")):
            raise ValueError("consent must be required with choices نعم / لا")
        return self


class Questionnaire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    questions: tuple[Question, ...] = Field(min_length=1, max_length=100)
    authorized_information: str = Field(default="", max_length=8000)
    introduction: str = Field(default="", max_length=4000)
    closing: str = Field(default="", max_length=4000)
    cancellation_response: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("duplicate question IDs")
        if any(q.kind == "consent" for q in self.questions[1:]):
            raise ValueError("consent can only be the first question")
        return self

    @classmethod
    def load(cls, path):
        return cls.model_validate_json(questionnaire_path(path).read_text(encoding="utf-8-sig"))


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["answer", "clarify", "off_topic", "identity", "company",
                    "repeat", "skip", "pause", "resume", "cancel", "correct_previous"]
    answer_status: Literal["valid", "invalid", "not_answered", "needs_clarification"]
    answer_value: int | str | None
    action: Literal["stay", "next_question", "repeat", "skip", "pause", "resume", "finish"]
    spoken_response: str = Field(min_length=1, max_length=600)
    current_question_id: str


@dataclass(frozen=True)
class Snapshot:
    session_id: str
    version: int
    question_id: str
    messages: list


@dataclass(frozen=True)
class Result:
    accepted: bool
    spoken_response: str
    reason: str
    committed: bool = False
