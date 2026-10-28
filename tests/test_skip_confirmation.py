"""Action confirmations must not become questionnaire answers."""
import json
import unittest

from customer_survey_cases import prepared
from test_survey import decision, model


def offer(manager):
    return model(decision(manager.question.id, intent="skip", answer_status="needs_clarification",
                          answer_value=None, action="stay", spoken_response="نتخطى هالسؤال؟"))


def skip(manager):
    return model(decision(manager.question.id, intent="skip", answer_status="not_answered",
                          answer_value=None, action="skip", spoken_response="حاضر."))


class SkipConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_offer_yes_skips_once_and_never_saves_yes_as_staff_answer(self):
        manager = prepared(4)
        result = await manager.turn("offer", "ما تعاملت مع الموظفين", offer(manager))
        self.assertTrue(result.accepted)
        self.assertFalse(result.committed)
        self.assertEqual(manager.question.id, "staff_experience")
        state = json.loads(manager.snapshot().messages[1]["content"])
        self.assertEqual(state["pending_confirmation"]["action"], "skip")
        self.assertEqual(state["pending_confirmation"]["question_id"], "staff_experience")
        snapshot = manager.snapshot()
        invoke = skip(manager)
        result = await manager.turn("yes", "نعم", invoke)
        self.assertTrue(result.accepted)
        self.assertFalse(result.committed)
        self.assertIn("staff_experience", manager.skipped)
        self.assertNotIn("staff_experience", manager.answers)
        self.assertEqual(manager.question.id, "difficulties")
        self.assertIsNone(manager.pending_confirmation)
        self.assertTrue(result.spoken_response.endswith(manager.question.text))
        self.assertEqual((await manager.turn("yes", "نعم", invoke)).reason, "duplicate")
        self.assertEqual((await manager.turn("late-yes", "نعم", invoke, snapshot=snapshot)).reason, "stale")
        self.assertNotIn("difficulties", manager.answers)

    async def test_decline_or_new_concern_clears_offer_without_advancing(self):
        for intent, text, spoken in (
                ("clarify", "لا، لا تتخطى", "حاضر، نخلّي السؤال الحالي."),
                ("company", "من أي جهة؟", "من شركة تنقيب البيانات.")):
            manager = prepared(4)
            await manager.turn("offer", "ما تعاملت مع الموظفين", offer(manager))
            result = await manager.turn("reply", text, model(decision(manager.question.id,
                intent=intent, answer_status="not_answered", answer_value=None,
                action="stay", spoken_response=spoken)))
            self.assertTrue(result.accepted)
            self.assertEqual(manager.question.id, "staff_experience")
            self.assertFalse(manager.skipped)
            self.assertIsNone(manager.pending_confirmation)

    async def test_required_question_cannot_be_offered_or_skipped(self):
        manager = prepared()
        result = await manager.turn("offer", "ما أعرف", offer(manager))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "required_question")
        self.assertIsNone(manager.pending_confirmation)
        self.assertFalse((await manager.turn("yes", "نعم", skip(manager))).accepted)
        self.assertFalse(manager.answers)

    async def test_yes_to_skip_cannot_be_saved_even_when_yes_is_a_permitted_choice(self):
        manager = prepared(7)
        await manager.turn("offer", "ما أعرف إذا أنصح", offer(manager))
        result = await manager.turn("bad-answer", "نعم", model(decision(
            manager.question.id, answer_value="نعم")))
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "pending_action_confirmation")
        self.assertNotIn("recommendation", manager.answers)

    async def test_offer_with_an_answer_value_is_invalid_and_clear_removes_metadata(self):
        manager = prepared(4)
        result = await manager.turn("bad-offer", "ما تعاملت", model(decision(
            manager.question.id, intent="skip", answer_status="needs_clarification",
            answer_value="نعم", action="stay")))
        self.assertFalse(result.accepted)
        self.assertIsNone(manager.pending_confirmation)
        await manager.turn("offer", "ما تعاملت", offer(manager))
        manager.clear()
        self.assertIsNone(manager.pending_confirmation)


if __name__ == "__main__":
    unittest.main()
