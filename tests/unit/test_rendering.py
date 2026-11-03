import unittest
from voice_agent.conversation import ConversationManager, Questionnaire
from tests.unit.test_survey import decision, model


class DirectReplyTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        return ConversationManager(Questionnaire.load('questionnaires/bank-nizwa.ar.json'))

    async def reply(self, text):
        manager = self.manager()
        result = await manager.turn('company', 'أي زيارة؟', model(decision('consent',
            intent='company', answer_status='not_answered', answer_value=None,
            action='stay', spoken_response=text)))
        self.assertEqual(manager.answers, {})
        self.assertEqual(manager.question.id, 'consent')
        self.assertEqual(result.follow_up, '')
        return result.spoken_response

    async def test_exact_question_with_interrogative_prefix_does_not_leave_fragment(self):
        answer = 'حسب بيانات التجربة، زيارة فرع الخوير بتاريخ 2 نوفمبر.'
        response = await self.reply(answer + ' هل ' + self.manager().question.text)
        self.assertEqual(response, answer)

    async def test_paraphrased_reminder_does_not_duplicate_question(self):
        answer = 'نتصل من شركة داتامايننج.'
        self.assertEqual(await self.reply(answer + ' هل تسمح نبدأ الاستبيان الحين؟'), answer)

    async def test_dates_times_and_multiple_answer_sentences_are_preserved(self):
        answer = 'حسب بيانات التجربة، زرت فرع الخوير يوم 2 نوفمبر الساعة 10:30. وكانت الخدمة فتح حساب.'
        self.assertEqual(await self.reply(answer), answer)

    async def test_actual_answer_clarifications_keep_their_question(self):
        manager = self.manager()
        text = 'تقصد يناسبك نبدأ الحين، أو في وقت ثاني؟'
        result = await manager.turn('clarify', 'يمكن بعدين', model(decision('consent',
            intent='clarify', answer_status='needs_clarification', answer_value=None,
            action='stay', spoken_response=text)))
        self.assertEqual(result.spoken_response, text)
        self.assertFalse(result.follow_up)
