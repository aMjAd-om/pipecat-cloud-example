"""Check compact model output through the real provider and state validator."""
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from pydantic import ValidationError

from evaluations.cases.survey_cases import CASES
from tests.unit.test_survey import decision, manager
from voice_agent.conversation import Decision
from voice_agent.providers.decision_protocol import DECISION_FIELDS, expand_decision
from voice_agent.providers.foundry_llm import FoundryDecisionClient


def compact(payload):
    return {short: payload[full] for short, full in DECISION_FIELDS.items()}


def client_returning(payload, *, reason="stop", refusal=None):
    response = NS(choices=[NS(finish_reason=reason, message=NS(
        content=json.dumps(payload), tool_calls=None, refusal=refusal))], usage=None)
    return NS(chat=NS(completions=NS(create=AsyncMock(return_value=response))))


class CompactDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_proposals_preserve_survey_case_outcomes(self):
        for category, text, intent, status, action, value in CASES:
            with self.subTest(category=category):
                reference, optimized = manager(), manager()
                if category.startswith("text_"):
                    for survey in (reference, optimized):
                        survey.index = 1
                        survey.answers["service_rating"] = 4
                payload = decision(reference.question.id, intent=intent,
                                   answer_status=status, action=action, answer_value=value)
                expected = await reference.turn("reference", text, AsyncMock(return_value=(payload, {})))
                client = client_returning(compact(payload))
                actual = await optimized.turn("compact", text, FoundryDecisionClient(client, "mock"))
                self.assertEqual(actual, expected)
                self.assertEqual(optimized.answers, reference.answers)
                self.assertEqual(optimized.index, reference.index)
                self.assertEqual(optimized.paused, reference.paused)
                self.assertEqual(optimized.cancelled, reference.cancelled)
                self.assertEqual(optimized.completed, reference.completed)
                options = client.chat.completions.create.call_args.kwargs
                schema = options["response_format"]["json_schema"]["schema"]
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), set(DECISION_FIELDS))

    def test_correction_target_is_preserved_and_active_id_is_bound_by_python(self):
        payload = decision("active", intent="correct_previous", answer_status="not_answered",
                           action="repeat", answer_value="previous")
        expanded = Decision.model_validate_json(expand_decision(json.dumps(compact(payload)), "active"))
        self.assertEqual(expanded.model_dump(), payload)

    def test_invalid_or_extra_compact_fields_are_rejected(self):
        valid = compact(decision())
        invalid = [None, [], "text", {**valid, "current_question_id": "other"},
                   {k: v for k, v in valid.items() if k != "value"},
                   {**valid, "intent": "unknown"}, {**valid, "value": True},
                   {**valid, "value": 4.0}, {**valid, "reply": ""},
                   {**valid, "reply": "x" * 601}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises((ValueError, ValidationError)):
                expand_decision(json.dumps(payload), "service_rating")
        with self.assertRaises(ValueError):
            expand_decision(json.dumps(valid) + json.dumps(valid), "service_rating")

    async def test_refused_or_truncated_output_cannot_commit_an_answer(self):
        for reason, refusal in (("length", None), ("stop", "refused")):
            survey = manager()
            client = client_returning(compact(decision()), reason=reason, refusal=refusal)
            result = await survey.turn("incomplete", "4", FoundryDecisionClient(client, "mock"))
            self.assertFalse(result.accepted)
            self.assertFalse(survey.answers)

    async def test_stale_compact_reply_cannot_be_rebound_to_a_new_question(self):
        survey = manager()
        client = client_returning(compact(decision()))
        original = client.chat.completions.create.return_value

        async def interrupt(**_options):
            survey.interrupt()
            await asyncio.sleep(0)
            return original

        client.chat.completions.create.side_effect = interrupt
        result = await survey.turn("stale", "4", FoundryDecisionClient(client, "mock"))
        self.assertEqual(result.reason, "stale")
        self.assertFalse(survey.answers)


if __name__ == "__main__":
    unittest.main()
