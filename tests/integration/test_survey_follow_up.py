"""Separate contextual answers from timed, interruptible survey reminders."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from pipecat.frames.frames import LLMTextFrame, TranscriptionFrame, UserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from evaluations.cases.customer_survey_cases import prepared
from tests.unit.test_survey import decision, model
from voice_agent.observability.timing import TurnTiming
from voice_agent.voice.playback import PlaybackMarkerFrame
from voice_agent.voice.survey import SurveyConversationProcessor


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.005)
        await asyncio.wait_for(wait(), 2)

    def proposal(self, manager, **changes):
        return decision(manager.question.id, intent="company", answer_status="not_answered",
                        answer_value=None, action="stay", **changes)

    async def test_same_intent_keeps_distinct_contextual_answers_and_question(self):
        manager = prepared(1)
        saved = dict(manager.answers)
        question = manager.question.id
        for index, reply in enumerate(("من شركة داتامايننج.", "هي شركة عُمانية تجري استطلاعات الرأي.")):
            result = await manager.turn(str(index), "من أي شركة؟", model(self.proposal(
                manager, spoken_response=reply)))
            self.assertEqual(result.spoken_response, reply)
            self.assertEqual(result.follow_up, "نرجع لسؤالنا، " + manager.question.text)
            self.assertEqual(result.pause_seconds, 1.0)
            self.assertFalse(result.committed)
            self.assertEqual(manager.question.id, question)
            self.assertEqual(manager.answers, saved)

    async def test_consent_stays_unanswered_after_company_question(self):
        manager = prepared()
        result = await manager.turn("company", "من أي شركة قلت لي؟", model(self.proposal(
            manager, spoken_response="من شركة داتامايننج.")))
        self.assertIn(manager.question.text, result.follow_up)
        self.assertEqual(manager.question.id, "consent")
        self.assertEqual(manager.answers, {})

    async def test_exact_question_copy_is_removed_from_direct_answer_before_timed_return(self):
        manager = prepared()
        reply = "من شركة داتامايننج."
        result = await manager.turn("company", "من أي شركة؟", model(self.proposal(
            manager, spoken_response=reply + " " + manager.question.text)))
        self.assertEqual(result.spoken_response, reply)
        self.assertEqual(result.follow_up.count(manager.question.text), 1)
        self.assertEqual(result.pause_seconds, 1.0)
        self.assertEqual(manager.answers, {})

    async def test_paused_survey_and_pending_clarification_have_no_reminder(self):
        manager = prepared(1)
        manager.paused = True
        result = await manager.turn("company", "من أي شركة؟", model(self.proposal(
            manager, spoken_response="من شركة داتامايننج.")))
        self.assertEqual(result.follow_up, "")
        manager.paused = False
        result = await manager.turn("clarify", "الخدمة جيدة", model(decision(
            manager.question.id, intent="clarify", answer_status="needs_clarification",
            answer_value=None, action="stay", spoken_response="وكم تعطيها من خمسة؟")))
        self.assertEqual(result.follow_up, "")
        self.assertTrue(manager.pending_clarification)

    def voice_processor(self):
        manager = prepared(1)
        decide = model(self.proposal(manager, spoken_response="من شركة داتامايننج."))
        processor = SurveyConversationProcessor(manager, decide, TurnTiming())
        processor.push_frame = AsyncMock()
        self.addAsyncCleanup(processor.cleanup)
        return processor

    async def ask_company(self, processor):
        await processor.process_frame(TranscriptionFrame("من أي شركة؟", "caller", "company"),
                                      FrameDirection.DOWNSTREAM)
        return next(call.args[0] for call in processor.push_frame.call_args_list
                    if isinstance(call.args[0], PlaybackMarkerFrame))

    def spoken(self, processor):
        return [call.args[0].text for call in processor.push_frame.call_args_list
                if isinstance(call.args[0], LLMTextFrame)]

    async def test_voice_waits_for_playback_then_one_second_before_reminder(self):
        processor = self.voice_processor()
        marker = await self.ask_company(processor)
        await asyncio.sleep(.04)
        self.assertEqual(self.spoken(processor), ["من شركة داتامايننج."])
        started = time.monotonic()
        marker.finished.set()
        await self.wait_until(lambda: len(self.spoken(processor)) == 2)
        self.assertGreaterEqual(time.monotonic() - started, .95)
        self.assertEqual(self.spoken(processor)[1], "نرجع لسؤالنا، " + processor.manager.question.text)

    async def test_caller_speech_cancels_pending_reminder(self):
        processor = self.voice_processor()
        marker = await self.ask_company(processor)
        pending = processor._follow_up_task
        marker.finished.set()
        await asyncio.sleep(.02)
        await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertTrue(pending.cancelled())
        self.assertEqual(self.spoken(processor), ["من شركة داتامايننج."])

    async def test_stale_state_suppresses_reminder(self):
        processor = self.voice_processor()
        marker = await self.ask_company(processor)
        processor.manager.interrupt()
        marker.finished.set()
        await asyncio.wait_for(processor._follow_up_task, 2)
        self.assertEqual(self.spoken(processor), ["من شركة داتامايننج."])
