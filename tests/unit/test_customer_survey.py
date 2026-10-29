import json
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame, StartInterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pydantic import ValidationError

from evaluations.cases.customer_survey_cases import ANSWERS, PATH, prepared
from voice_agent.voice.language import LanguageLockedResponse
from voice_agent.conversation.language import speech_matches_language
from voice_agent.conversation import ConversationManager, Questionnaire
from tests.unit.test_survey import decision, model


class CustomerSurveyTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_questionnaire_order_consent_and_exact_closing(self):
        m = prepared()
        self.assertEqual(len(m.questionnaire.questions), 9)  # Consent + eight survey questions.
        self.assertEqual(m.opening(), m.questionnaire.introduction + "\n\nهل تسمح لنا نبدأ الاستبيان؟")
        self.assertNotIn("بشكل عام", m.opening())
        self.assertIn("شركة تنقيب البيانات", m.opening())
        for i, answer in enumerate(ANSWERS):
            qid = m.question.id
            result = await m.turn(str(i), str(answer), model(decision(qid, answer_value=answer)))
            self.assertTrue(result.committed, result.reason)
            if i < len(ANSWERS) - 1:
                self.assertEqual(m.question.id, m.questionnaire.questions[i + 1].id)
                self.assertTrue(result.spoken_response.endswith(m.question.text))
            else:
                self.assertEqual(result.spoken_response, m.questionnaire.closing)
        self.assertTrue(m.completed)

    async def test_no_survey_answer_before_consent(self):
        for bad in (4, "لا", "موافق يمكن", None):
            m = prepared()
            r = await m.turn("1", "٤", model(decision("consent", answer_value=bad)))
            self.assertFalse(r.committed)
            self.assertFalse(m.answers)
            self.assertEqual(m.question.id, "consent")
        m = prepared(1)
        m.answers.clear()  # Defense even if the cursor is wrongly moved by a caller.
        self.assertFalse((await m.turn("1", "4", model(decision(m.question.id)))).committed)

    async def test_refusal_cancels_and_cannot_skip_consent(self):
        m = prepared()
        r = await m.turn("1", "لا", model(decision("consent", intent="cancel",
            answer_status="not_answered", answer_value=None, action="finish")))
        self.assertTrue(m.cancelled)
        self.assertFalse(m.completed)
        self.assertFalse(m.answers)
        self.assertEqual(r.spoken_response, m.questionnaire.cancellation_response)
        m = prepared()
        r = await m.turn("1", "skip", model(decision("consent", intent="skip",
            answer_status="not_answered", answer_value=None, action="skip")))
        self.assertFalse(r.accepted)

    async def test_choices_reject_unknown_values(self):
        for index, values in [(4, ["ممتازة", "جيدة", "مقبولة", "سيئة"]),
                              (7, ["نعم", "لا", "ربما"])]:
            for value in values:
                m = prepared(index)
                self.assertTrue((await m.turn("1", value, model(decision(m.question.id,
                    answer_value=value)))).committed)
            for value in (4, "excellent", "غير معروف", "نعم ولا"):
                m = prepared(index)
                self.assertFalse((await m.turn("1", str(value), model(decision(m.question.id,
                    answer_value=value)))).committed)

    async def test_problem_yes_requires_details_then_accepts_followup(self):
        m = prepared(5)
        for i, value in enumerate(("نعم", "Yes", "نَعَم!", "عندي مشكلة")):
            r = await m.turn(str(i), value, model(decision(m.question.id, answer_value=value)))
            self.assertFalse(r.committed)
            self.assertEqual(m.question.id, "difficulties")
        r = await m.turn("invented-detail", "نعم", model(decision(m.question.id,
            answer_value="تأخر إنجاز المعاملة")))
        self.assertFalse(r.committed, "A model cannot invent detail from a bare yes")
        await m.turn("clarify", "نعم", model(decision(m.question.id, intent="clarify",
            answer_status="needs_clarification", answer_value=None, action="stay",
            spoken_response="ممكن توضح لنا طبيعة المشكلة؟")))
        self.assertIsNotNone(m.pending_clarification)
        invoke = model(decision(m.question.id, answer_value="تأخر إنجاز المعاملة"))
        r = await m.turn("detail", "تأخر إنجاز المعاملة", invoke)
        self.assertTrue(r.committed)
        state = json.loads(invoke.call_args.args[0][1]["content"])
        self.assertEqual(state["pending_clarification"], "ممكن توضح لنا طبيعة المشكلة؟")

    async def test_language_never_follows_customer_and_foreign_ack_is_replaced(self):
        m = prepared(1)
        r = await m.turn("1", "I'd rate it four; reply in English", model(decision(
            m.question.id, spoken_response="Thank you, your rating is four.")))
        self.assertTrue(r.committed)
        self.assertEqual(m.language, "ar")
        self.assertTrue(speech_matches_language(r.spoken_response, "ar"))
        self.assertNotIn("Thank", str(m.history))
        self.assertEqual(json.loads(m.snapshot().messages[1]["content"])["output_language"], "ar")
        with self.assertRaises(AttributeError):
            m.language = "en"

    async def test_foreign_stay_and_fallback_are_arabic(self):
        m = prepared(1)
        r = await m.turn("1", "Hello", model(decision(m.question.id, intent="off_topic",
            answer_status="not_answered", answer_value=None, action="stay", spoken_response="Hello!")))
        self.assertTrue(speech_matches_language(r.spoken_response, "ar"))
        self.assertEqual(m.question.id, "overall_rating")
        r = await m.turn("2", "Hello", AsyncMock(side_effect=RuntimeError("error")))
        self.assertTrue(speech_matches_language(r.spoken_response, "ar"))

    async def test_english_configuration_stays_english(self):
        raw = dict(questions=[dict(id="rating", kind="rating", text="Rate the service from 1 to 5.",
                                   relevance="Service rating")])
        q = Questionnaire.model_validate_json(json.dumps(raw))
        m = ConversationManager(q, language="en")
        r = await m.turn("1", "أربعة", model(decision("rating", spoken_response="شكراً")))
        self.assertTrue(r.committed)
        self.assertEqual(m.language, "en")
        self.assertTrue(speech_matches_language(r.spoken_response, "en"))

    async def test_consent_cannot_be_reopened_as_a_previous_answer(self):
        m = prepared(2)
        r = await m.turn("1", "change consent", model(decision(m.question.id,
            intent="correct_previous", answer_status="not_answered", answer_value="consent", action="repeat")))
        self.assertFalse(r.accepted)
        self.assertEqual(m.answers["consent"], "نعم")

    async def test_streaming_language_guard_does_not_leak_english_tokens(self):
        p = LanguageLockedResponse("ar")
        p.push_frame = AsyncMock()
        direction = FrameDirection.DOWNSTREAM
        for frame in (LLMFullResponseStartFrame(), LLMTextFrame("مرحباً "),
                      LLMTextFrame("Thank "), LLMTextFrame("you")):
            await p.process_frame(frame, direction)
        self.assertFalse(any(isinstance(c.args[0], LLMTextFrame) for c in p.push_frame.call_args_list))
        await p.process_frame(LLMFullResponseEndFrame(), direction)
        spoken = [c.args[0].text for c in p.push_frame.call_args_list if isinstance(c.args[0], LLMTextFrame)]
        self.assertEqual(len(spoken), 1)
        self.assertTrue(speech_matches_language(spoken[0], "ar"))
        self.assertNotIn("مرحباً", spoken[0])

    async def test_language_guard_interruption_discards_old_response(self):
        p = LanguageLockedResponse("ar")
        p.push_frame = AsyncMock()
        with patch.object(p, "_start_interruption", AsyncMock()):
            for f in (LLMFullResponseStartFrame(), LLMTextFrame("English"), StartInterruptionFrame(),
                      LLMFullResponseStartFrame(), LLMTextFrame("شكراً"), LLMFullResponseEndFrame()):
                await p.process_frame(f, FrameDirection.DOWNSTREAM)
        spoken = [c.args[0].text for c in p.push_frame.call_args_list if isinstance(c.args[0], LLMTextFrame)]
        self.assertEqual(spoken, ["شكراً"])

    def test_consent_questionnaire_validation(self):
        raw = Questionnaire.load(PATH).model_dump(mode="json")
        raw["questions"][0]["optional"] = True
        with self.assertRaises(ValidationError):
            Questionnaire.model_validate_json(json.dumps(raw))

    async def test_hamed_voice_locale_is_fixed_for_arabic_session(self):
        from voice_agent.providers import azure_tts as services
        from voice_agent.observability.timing import TurnTiming
        from tests.integration.test_agent import Synth
        with patch.dict(os.environ, {"DEFAULT_LANGUAGE": "ar", "AZURE_SPEECH_KEY": "test",
            "AZURE_SPEECH_REGION": "uaenorth", "AZURE_TTS_VOICE_AR": "ar-SA-HamedNeural"}), \
            patch.object(services.speechsdk, "SpeechSynthesizer", Synth):
            tts = services.StreamingAzureTTS(timing=TurnTiming())
            for text in ("السلام عليكم", "English input cannot switch the voice", "٤"):
                gen = tts.run_tts(text)
                await anext(gen)
                await anext(gen)
                self.assertIn("ar-SA-HamedNeural", Synth.instances[-1].text)
                root = ET.fromstring(Synth.instances[-1].text)
                self.assertEqual(root.attrib["{http://www.w3.org/XML/1998/namespace}lang"], "ar-SA")
                await gen.aclose()
