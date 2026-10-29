"""Metadata and state contracts across surveys; never assert a preferred reply script."""
import json
import unittest

from evaluations.cases.response_style_cases import CASES
from tests.unit.test_survey import decision, model
from voice_agent.conversation import rating_tokens


class ResponseStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_different_surveys_supply_their_actual_answer_requirements(self):
        for case in CASES:
            with self.subTest(case=case.name):
                manager = case.manager()
                requirements = json.loads(manager.snapshot().messages[1]["content"])["response_requirements"]
                self.assertEqual(requirements["topic"], case.question.relevance)
                if case.question.kind == "rating":
                    self.assertEqual(requirements["minimum"], case.question.minimum)
                    self.assertEqual(requirements["maximum"], case.question.maximum)
                elif case.question.kind == "choice":
                    self.assertEqual(requirements["choices"], list(case.question.choices))
                    self.assertNotIn("minimum", requirements)
                    self.assertNotIn("maximum", requirements)
                elif case.question.kind in ("text", "conditional_text"):
                    self.assertEqual(requirements["max_length"], case.question.max_length)
                    self.assertNotIn("choices", requirements)

    async def test_natural_clarification_variants_are_preserved_without_saving(self):
        case = CASES[0]
        replies = (
            "زين، بس لو سمحت ممكن تختار تقييم التجربة من واحد إلى خمسة؟",
            "حلو، وإذا تختار لها رقم بين واحد وخمسة، كم يكون؟",
        )
        for reply in replies:
            manager = case.manager()
            invoke = model(decision(manager.question.id, intent="clarify",
                answer_status="needs_clarification", answer_value=None,
                action="stay", spoken_response=reply))
            result = await manager.turn("feedback", case.customer, invoke)
            self.assertTrue(result.accepted)
            self.assertFalse(result.committed)
            self.assertEqual(manager.answers, {})
            self.assertEqual(result.spoken_response, reply)
            self.assertEqual(result.follow_up, "")
            request = invoke.call_args.args[0]
            self.assertIn("Answer requirements:", request[-2]["content"])

    async def test_non_five_point_scale_uses_current_bounds_and_remembers_context(self):
        case = CASES[2]
        manager = case.manager()
        invoke = model(decision(manager.question.id, intent="clarify",
            answer_status="needs_clarification", answer_value=None, action="stay",
            spoken_response="فهمت إن الزيارة كانت زينة، ممكن تختار لها رقم من واحد لسبعة؟"))
        result = await manager.turn("again", case.customer, invoke)
        self.assertTrue(result.accepted)
        self.assertEqual(manager.answers, {})
        self.assertEqual(set(rating_tokens(result.spoken_response)), {1, 7})
        state = json.loads(invoke.call_args.args[0][1]["content"])
        self.assertEqual(state["response_requirements"]["maximum"], 7)
        self.assertEqual(state["recent_history"][-1]["content"], case.previous_reply)

    async def test_choice_and_text_surveys_do_not_need_rating_scripts(self):
        for case, value in ((CASES[4], CASES[4].expected_value), (CASES[5], CASES[5].customer)):
            with self.subTest(case=case.name):
                manager = case.manager()
                result = await manager.turn("answer", case.customer, model(decision(
                    manager.question.id, answer_value=value, spoken_response="فهمت عليك.")))
                self.assertTrue(result.committed)
                self.assertEqual(manager.answers[case.question.id], value)

    async def test_generalized_guidance_still_rejects_sentiment_as_a_guessed_number(self):
        for case in CASES[:3]:
            with self.subTest(case=case.name):
                manager = case.manager()
                result = await manager.turn("guess", case.customer, model(decision(
                    manager.question.id, answer_value=case.question.maximum,
                    spoken_response="شكراً لك.")))
                self.assertFalse(result.committed)
                self.assertEqual(manager.answers, {})

    async def test_colloquial_range_cannot_turn_yes_into_a_guessed_rating(self):
        case = CASES[2]
        manager = case.manager()
        result = await manager.turn("clarify", case.customer, model(decision(
            manager.question.id, intent="clarify", answer_status="needs_clarification",
            answer_value=None, action="stay",
            spoken_response="ممكن تختار رقم من واحد لسبعة؟")))
        self.assertTrue(result.accepted)
        self.assertEqual(rating_tokens(result.spoken_response), [1, 7])
        result = await manager.turn("yes", "نعم", model(decision(
            manager.question.id, answer_value=1, spoken_response="فهمت عليك.")))
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "rating_not_explicit")
        self.assertEqual(manager.answers, {})
