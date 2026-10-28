"""Safety regressions for concise clarifications and concern-handling turns."""
import unittest

from customer_survey_cases import prepared
from test_survey import decision, model


class ConversationStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_bridge_cannot_duplicate_the_survey_question(self):
        manager = prepared(1)
        manager.paused = True
        result = await manager.turn("resume", "كمل", model(decision(
            manager.question.id, intent="resume", answer_status="not_answered",
            answer_value=None, action="resume",
            spoken_response="حاضر، كم تعطي تقييمك العام للخدمة من واحد إلى خمسة؟")))
        self.assertTrue(result.accepted)
        self.assertEqual(result.spoken_response.count("؟"), manager.question.text.count("؟"))
        self.assertEqual(result.spoken_response, "حاضر. " + manager.question.text)

    async def test_verbal_rating_is_not_saved_even_if_model_guesses(self):
        for index, text, guessed in ((1, "الخدمة جيدة", 4), (3, "بطيئة جدا", 1)):
            manager = prepared(index)
            old_answers = dict(manager.answers)
            result = await manager.turn("guess", text, model(decision(
                manager.question.id, answer_value=guessed, spoken_response="شكراً، تم تسجيل تقييمك.")))
            self.assertFalse(result.committed)
            self.assertEqual(manager.answers, old_answers)
            self.assertEqual(result.reason, "rating_not_explicit")
            self.assertNotIn("تم تسجيل", result.spoken_response)
            self.assertNotIn(manager.question.text, result.spoken_response)

    async def test_endpoint_proposal_requires_explicit_confirmation(self):
        manager = prepared(3)
        initial_answers = dict(manager.answers)
        result = await manager.turn("description", "بطيئة جدا", model(decision(
            manager.question.id, intent="clarify", answer_status="needs_clarification",
            answer_value=None, action="stay", spoken_response="فهمت عليك، تقصد نعتمد واحد؟")))
        self.assertTrue(result.accepted)
        self.assertFalse(result.committed)
        self.assertEqual(manager.answers, initial_answers)
        result = await manager.turn("confirmation", "نعم", model(decision(
            manager.question.id, answer_value=1, spoken_response="وصلت وجهة نظرك.")))
        self.assertTrue(result.committed)
        self.assertEqual(manager.answers["speed_rating"], 1)

    async def test_yes_after_generic_scale_or_company_reply_cannot_supply_rating(self):
        for reply in ("كم تعطيها من واحد إلى خمسة؟", "نحن من شركة تنقيب البيانات."):
            manager = prepared(1)
            await manager.turn("clarify", "الخدمة جيدة", model(decision(
                manager.question.id, intent="clarify", answer_status="needs_clarification",
                answer_value=None, action="stay", spoken_response=reply)))
            result = await manager.turn("yes", "نعم", model(decision(manager.question.id)))
            self.assertFalse(result.committed)
            self.assertNotIn("overall_rating", manager.answers)

    async def test_concerns_preserve_question_without_forcing_consent_suffix(self):
        concerns = [
            ("من أعطاك الإذن للاتصال؟", "ما عندي معلومات عن الإذن بالتواصل. مشاركتك اختيارية."),
            ("من أي جهة؟", "من شركة تنقيب البيانات، وهي شركة عُمانية تجري استطلاعات الرأي."),
            ("أي خدمة؟", "معذرة، ما عندي اسم الخدمة المقصودة، وما أريد أطلب منك تقييم شي مب واضح لك."),
            ("لا أعرف هذي الشركة", "هي شركة عُمانية تجمع البيانات وتجري استطلاعات الرأي، ودورها هنا إجراء الاستبيان."),
        ]
        manager = prepared()
        for i, (text, reply) in enumerate(concerns):
            result = await manager.turn(str(i), text, model(decision("consent", intent="company",
                answer_status="not_answered", answer_value=None, action="stay", spoken_response=reply)))
            self.assertTrue(result.accepted)
            self.assertFalse(result.committed)
            self.assertEqual(result.spoken_response, reply)
            self.assertEqual(manager.question.id, "consent")
            self.assertEqual(manager.answers, {})


if __name__ == "__main__":
    unittest.main()
