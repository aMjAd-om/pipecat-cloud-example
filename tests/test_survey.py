"""Offline state/contract regressions. Mock decisions do not test LLM relevance."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from survey import ConversationManager, Decision, Questionnaire
from survey_cases import CASES


def manager():
    return ConversationManager(Questionnaire.load("questionnaire.example.json"))


def decision(q="service_rating", **changes):
    return {**dict(intent="answer", answer_status="valid", answer_value=4,
                action="next_question", spoken_response="شكراً.",
                current_question_id=q), **changes}


def model(payload):
    return AsyncMock(return_value=(payload, {"total_tokens": 123}))


class SurveyTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_proposals_obey_state_contract(self):
        for i, (category, text, intent, status, action, value) in enumerate(CASES):
            with self.subTest(category=category, text=text):
                m = manager()
                if category.startswith("text_"):
                    m.index = 1
                    m.answers["service_rating"] = 4
                before = dict(m.answers)
                d = decision(m.question.id, intent=intent, answer_status=status,
                             action=action, answer_value=value)
                invoke = model(d)
                result = await m.turn(str(i), text, invoke)
                invoke.assert_awaited_once()
                self.assertEqual(result.committed, status == "valid")
                if status != "valid":
                    self.assertEqual(m.answers, before)
                if category == "skip_required":
                    self.assertFalse(result.accepted)
                    self.assertEqual(m.index, 0)
                if action == "stay":
                    self.assertEqual(m.question.id, d["current_question_id"])

    async def test_exact_next_question_and_completion(self):
        m = manager()
        first = await m.turn("1", "4", model(decision()))
        self.assertTrue(first.spoken_response.endswith(m.question.text))
        last = await m.turn("2", "Improve waiting time", model(decision(
            m.question.id, answer_value="Improve waiting time")))
        self.assertTrue(last.committed)
        self.assertTrue(m.completed)
        self.assertFalse(m.cancelled)
        self.assertFalse((await m.turn("3", "4", model(decision(m.question.id)))).accepted)

    async def test_duplicate_identity_not_text(self):
        m = manager()
        raw = m.questionnaire.model_dump(mode="json")
        raw["questions"][1]["kind"] = "rating"
        m.questionnaire = Questionnaire.model_validate_json(json.dumps(raw))
        invoke = model(decision())
        await m.turn("event", "4", invoke)
        self.assertEqual((await m.turn("event", "4", invoke)).reason, "duplicate")
        self.assertEqual(invoke.await_count, 1)
        other = model(decision(m.question.id, answer_value=4))
        self.assertTrue((await m.turn("new-event", "4", other)).committed)

    async def test_simultaneous_and_stale_actions_commit_once(self):
        m = manager()
        snap = m.snapshot()
        results = await asyncio.gather(*[
            m.apply(snap, Decision(**decision()), "4") for _ in range(10)])
        self.assertEqual(sum(r.committed for r in results), 1)
        self.assertEqual(sum(r.reason == "stale" for r in results), 9)
        self.assertEqual(m.answers, {"service_rating": 4})
        self.assertFalse((await manager().apply(snap, Decision(**decision()), "4")).accepted)

    async def test_overlapping_llm_calls_have_one_commit(self):
        m = manager()
        both = asyncio.Event()
        calls = 0
        async def decide(messages):
            nonlocal calls
            calls += 1
            if calls == 2:
                both.set()
            await both.wait()
            return decision(), {}
        results = await asyncio.gather(m.turn("a", "4", decide), m.turn("b", "4", decide))
        self.assertEqual(sum(r.committed for r in results), 1)

    async def test_interruption_invalidates_pending_request(self):
        m = manager()
        started, release = asyncio.Event(), asyncio.Event()
        async def decide(messages):
            started.set()
            await release.wait()
            return decision(), {}
        task = asyncio.create_task(m.turn("1", "4", decide))
        await started.wait()
        m.interrupt()
        release.set()
        self.assertEqual((await task).reason, "stale")
        self.assertEqual(m.answers, {})

    async def test_pause_resume_preserves_question(self):
        m = manager()
        await m.turn("1", "pause", model(decision(intent="pause", answer_status="not_answered",
                     action="pause", answer_value=None)))
        self.assertTrue(m.paused)
        self.assertFalse((await m.turn("2", "4", model(decision()))).committed)
        r = await m.turn("3", "كمل", model(decision(intent="resume", answer_status="not_answered",
                        action="resume", answer_value=None)))
        self.assertFalse(m.paused)
        self.assertTrue(r.spoken_response.endswith(m.question.text))

    async def test_reopen_previous_and_require_new_answer(self):
        m = manager()
        await m.turn("1", "4", model(decision()))
        correction = decision(m.question.id, intent="correct_previous", answer_status="not_answered",
                              action="repeat", answer_value="service_rating")
        result = await m.turn("2", "Change my previous rating", model(correction))
        self.assertTrue(result.accepted)
        self.assertEqual(m.answers["service_rating"], 4)
        self.assertEqual(m.question.id, "service_rating")
        await m.turn("3", "three", model(decision(answer_value=3)))
        self.assertEqual(m.answers["service_rating"], 3)
        self.assertEqual(m.question.id, "service_feedback")

    async def test_rejected_acknowledgement_never_spoken(self):
        for changes in [dict(answer_value=9), dict(answer_value=True),
                        dict(current_question_id="wrong"), dict(intent="off_topic"),
                        dict(answer_status="invalid"), dict(action="finish")]:
            m = manager()
            r = await m.turn("a", "4", model(decision(**changes,
                spoken_response="Successfully submitted!")))
            self.assertFalse(r.accepted)
            self.assertNotIn("submitted", r.spoken_response)
            self.assertEqual(m.answers, {})

    async def test_explicit_rating_and_text_constraints(self):
        from survey import rating_tokens
        self.assertEqual(rating_tokens("ثلاثة، لا، أقصد أَرْبَعَة!"), [3, 4])
        for value, text in [(4, "excellent"), ("4", "4"), (4.0, "4"), (-1, "-1")]:
            m = manager()
            self.assertFalse((await m.turn("1", text, model(decision(answer_value=value)))).committed)
        for value in ["", " " * 3, "x" * 1001, 4]:
            m = manager()
            m.index = 1
            self.assertFalse((await m.turn("1", "feedback", model(decision(m.question.id,
                              answer_value=value)))).committed)

    async def test_off_topic_then_return_and_history(self):
        m = manager()
        await m.turn("1", "Who are you?", model(decision(intent="identity", answer_status="not_answered",
                     action="stay", answer_value=None, spoken_response="I'm an automated survey assistant.")))
        self.assertEqual(m.answers, {})
        invoke = model(decision())
        await m.turn("2", "4", invoke)
        context = json.loads(invoke.call_args.args[0][1]["content"])
        self.assertEqual(context["recent_history"][0]["content"], "Who are you?")

    async def test_clarification_then_explicit_confirmation(self):
        m = manager()
        await m.turn("1", "three, maybe four", model(decision(intent="clarify",
            answer_status="needs_clarification", action="stay", answer_value=None,
            spoken_response="هل تقصد تقييماً قدره أربعة؟")))
        self.assertIsNotNone(m.pending_clarification)
        r = await m.turn("2", "Yes, four", model(decision()))
        self.assertTrue(r.committed)
        self.assertIsNone(m.pending_clarification)

    async def test_clarification_does_not_bypass_explicit_rating_guard(self):
        for pending, utterance in [("Could you clarify?", "yes"),
                                   ("Do you mean three or four?", "yes"),
                                   ("Do you mean four?", "hello")]:
            m = manager()
            m.pending_clarification = pending
            r = await m.turn("1", utterance, model(decision()))
            self.assertFalse(r.committed)
        m = manager()
        m.pending_clarification = "Do you mean four?"
        self.assertTrue((await m.turn("1", "yes", model(decision()))).committed)

    async def test_schema_errors_and_model_failures_fail_closed(self):
        for payload in ["not JSON", {**decision(), "extra": "no"},
                        {**decision(), "action": "advance"}, {"intent": "answer"}]:
            m = manager()
            r = await m.turn("1", "4", model(payload))
            self.assertEqual(r.reason, "model_error")
            self.assertEqual(m.answers, {})
        m = manager()
        r = await m.turn("1", "4", AsyncMock(side_effect=RuntimeError("secret")))
        self.assertNotIn("secret", r.spoken_response)
        self.assertEqual(m.history[0]["content"], "4")
        self.assertEqual(m.history[1]["content"], r.spoken_response)

    async def test_history_bounds_clear_and_session_isolation(self):
        m, other = manager(), manager()
        for i in range(20):
            await m.turn(str(i), "who?", model(decision(intent="identity", action="stay",
                         answer_status="not_answered", answer_value=None)))
        self.assertEqual(len(m.history), 12)
        self.assertEqual(len(other.history), 0)
        self.assertNotIn("who?", str(list(m.metrics)))
        m.clear()
        self.assertFalse(m.history)
        self.assertFalse(m.answers)

    async def test_duplicate_ids_not_evicted(self):
        m = manager()
        m._seen.update(str(i) for i in range(4096))
        invoke = model(decision())
        self.assertEqual((await m.turn("0", "4", invoke)).reason, "duplicate")
        self.assertEqual((await m.turn("new", "4", invoke)).reason, "turn_limit")
        invoke.assert_not_awaited()

    async def test_reopened_optional_skip_removes_old_answer(self):
        m = manager()
        m.index = 1
        m.answers = {"service_rating": 4, "service_feedback": "less waiting"}
        r = await m.turn("1", "skip", model(decision(m.question.id, intent="skip",
            answer_status="not_answered", answer_value=None, action="skip")))
        self.assertTrue(r.accepted)
        self.assertNotIn("service_feedback", m.answers)
        self.assertIn("service_feedback", m.skipped)

    async def test_real_processor_queue_captures_question_before_waiting(self):
        from survey_voice import SurveyConversationProcessor
        from instrumentation import TurnTiming
        from pipecat.frames.frames import TranscriptionFrame, EndFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask
        from pipecat.pipeline.runner import PipelineRunner
        m = manager()
        entered, release = asyncio.Event(), asyncio.Event()
        async def decide(messages):
            entered.set()
            await release.wait()
            return decision(), {}
        invoke = AsyncMock(side_effect=decide)
        p = SurveyConversationProcessor(m, invoke, TurnTiming())
        p._speak = AsyncMock()
        task = PipelineTask(Pipeline([p]))
        runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
        try:
            await task.queue_frames([TranscriptionFrame("4", "", "a")])
            await asyncio.wait_for(entered.wait(), 5)
            second = TranscriptionFrame("4", "", "b")
            await p.queue_frame(second)
            self.assertEqual(second.metadata["survey_snapshot"].version, 0)
            release.set()
            await task.queue_frames([EndFrame()])
            await asyncio.wait_for(runner, 5)
            self.assertEqual(invoke.await_count, 1)
            self.assertEqual(sum(x["committed"] for x in m.metrics), 1)
            p._speak.assert_awaited_once()
        finally:
            if not runner.done():
                await task.cancel()
                await asyncio.wait_for(runner, 5)

    async def test_pinned_voice_adapter_speaks_validated_text_only(self):
        from survey_voice import SurveyConversationProcessor, FoundryDecisionClient
        from pipecat.frames.frames import TranscriptionFrame, LLMTextFrame
        from pipecat.processors.frame_processor import FrameDirection
        from instrumentation import TurnTiming
        m = manager()
        client = AsyncMock()
        from types import SimpleNamespace as NS
        client.chat.completions.create.return_value = NS(choices=[NS(finish_reason="stop",
            message=NS(content=json.dumps(decision())))], usage=None)
        p = SurveyConversationProcessor(m, FoundryDecisionClient(client, "gpt-4.1-mini"), TurnTiming())
        p.push_frame = AsyncMock()
        await p.process_frame(TranscriptionFrame("4", "", "event"), FrameDirection.DOWNSTREAM)
        client.chat.completions.create.assert_awaited_once()
        spoken = [c.args[0].text for c in p.push_frame.call_args_list if isinstance(c.args[0], LLMTextFrame)]
        self.assertEqual(spoken, ["شكراً. " + m.question.text])
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "gpt-4.1-mini")

    async def test_enabled_pipeline_constructs(self):
        import os
        import bot
        from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection
        connection = SmallWebRTCConnection(ice_servers=[])
        try:
            with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "test",
                "AZURE_OPENAI_BASE_URL": "https://example.com/openai/v1/",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini", "AZURE_SPEECH_KEY": "test",
                "AZURE_SPEECH_REGION": "uaenorth", "SURVEY_CONVERSATION_MANAGER": "true"}), \
                patch.object(bot.PipelineRunner, "run", AsyncMock()):
                await bot.main(connection)
        finally:
            await connection.disconnect()

    def test_questionnaire_rejects_duplicate_ids(self):
        q = Questionnaire.load("questionnaire.example.json").model_dump(mode="json")
        q["questions"].append(q["questions"][0])
        with self.assertRaises(ValidationError):
            Questionnaire.model_validate_json(json.dumps(q))
