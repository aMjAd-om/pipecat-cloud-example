"""Bank Nizwa terminal state and playback checks; model decisions are mocked."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import LLMTextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from tests.unit.test_survey import decision, model
from voice_agent.conversation import ConversationManager, Decision, Questionnaire
from voice_agent.observability.timing import TurnTiming
from voice_agent.voice.playback import PlaybackMarkerFrame
from voice_agent.voice.survey import SurveyConversationProcessor


def manager():
    return ConversationManager(Questionnaire.load('questionnaires/bank-nizwa.ar.json'))


def wrong_recipient(m, **changes):
    return decision(m.question.id, **{
        'intent': 'wrong_recipient', 'answer_status': 'not_answered',
        'answer_value': None, 'action': 'finish',
        'spoken_response': 'السموحة منك.', **changes,
    })


class BankNizwaTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_reply_then_wrong_recipient_ends_without_reminder(self):
        m = manager()
        state = json.loads(m.snapshot().messages[1]['content'])
        self.assertIn('الجهة المتصلة والمنفذة للاستبيان هي شركة داتامايننج',
                      state['authorized_information'])
        reply = 'نتصل من شركة داتامايننج.'
        result = await m.turn('company', 'من أي شركة تتصلون؟', model(decision(
            'consent', intent='company', answer_status='not_answered',
            answer_value=None, action='stay', spoken_response=reply)))
        self.assertEqual(result.spoken_response, reply)
        self.assertFalse(m.answers)
        result = await m.turn('wrong', 'ما زرت البنك', model(wrong_recipient(m)))
        self.assertTrue(result.accepted)
        self.assertTrue(result.end_call)
        self.assertTrue(m.cancelled)
        self.assertFalse(m.completed)
        self.assertFalse(m.answers)
        self.assertFalse(result.follow_up)
        self.assertEqual(result.spoken_response, m.questionnaire.wrong_recipient_response)

    async def test_wrong_recipient_also_ends_after_consent_and_while_paused(self):
        for paused in (False, True):
            m = manager()
            m.answers['consent'] = 'نعم'
            m.index = 1
            m.paused = paused
            result = await m.turn('wrong', 'الرقم غلط', model(wrong_recipient(m)))
            self.assertTrue(result.end_call)
            self.assertEqual(m.answers, {'consent': 'نعم'})

    async def test_wrong_recipient_requires_configured_response_and_finish_contract(self):
        m = ConversationManager(Questionnaire.load('questionnaires/example.json'))
        result = await m.turn('wrong', 'wrong number', model(wrong_recipient(m)))
        self.assertFalse(result.accepted)
        self.assertFalse(m.cancelled)
        m = manager()
        for changes in ({'action': 'stay'}, {'answer_value': 'لا'},
                        {'answer_status': 'valid'}):
            result = await m.apply(m.snapshot(),
                Decision(**wrong_recipient(m, **changes)), 'ما زرت البنك')
            self.assertFalse(result.accepted)
            self.assertFalse(result.end_call)
            self.assertFalse(m.cancelled)

    def processor(self):
        m = manager()
        stop = asyncio.Event()
        processor = SurveyConversationProcessor(m, model(wrong_recipient(m)),
                                                TurnTiming(), stop_event=stop)
        processor.push_frame = AsyncMock()
        self.addAsyncCleanup(processor.cleanup)
        return processor, stop

    async def test_call_waits_for_apology_playback_then_stops(self):
        processor, stop = self.processor()
        await processor.process_frame(TranscriptionFrame('ما زرت البنك', 'caller', '1'),
                                      FrameDirection.DOWNSTREAM)
        frames = [c.args[0] for c in processor.push_frame.call_args_list]
        apology = next(f for f in frames if isinstance(f, LLMTextFrame))
        marker = next(f for f in frames if isinstance(f, PlaybackMarkerFrame))
        self.assertEqual(apology.text, processor.manager.questionnaire.wrong_recipient_response)
        self.assertFalse(stop.is_set())
        self.assertIsNone(processor._follow_up_task)
        # Queued transcripts cannot restart the survey during the closing speech.
        await processor.process_frame(TranscriptionFrame('نعم', 'caller', '2'),
                                      FrameDirection.DOWNSTREAM)
        processor.decide.assert_awaited_once()
        marker.finished.set()
        await asyncio.wait_for(processor._end_call_task, 1)
        self.assertTrue(stop.is_set())

    async def test_playback_timeout_still_releases_call(self):
        processor, stop = self.processor()
        async def timeout(awaitable, seconds):
            awaitable.close()
            raise asyncio.TimeoutError
        with patch('voice_agent.voice.survey.asyncio.wait_for', side_effect=timeout):
            await processor._end_call(PlaybackMarkerFrame())
        self.assertTrue(stop.is_set())
