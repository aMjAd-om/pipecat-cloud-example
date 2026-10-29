"""Authoritative in-memory survey state and turn validation."""
import asyncio
import json
import time
from collections import deque
from uuid import uuid4
from voice_agent.conversation.models import Questionnaire, Decision, Snapshot, Result
from voice_agent.conversation.answers import rating_tokens, normalize_answer
from voice_agent.conversation.prompts import PROMPT


class ConversationManager:
    def __init__(self, questionnaire: Questionnaire, *, session_id=None, language="ar"):
        if language not in ("ar", "en"):
            raise ValueError("language must be ar or en")
        self.questionnaire = questionnaire
        self.session_id = session_id or str(uuid4())
        self._language = language
        self.version = 0
        self.index = 0
        self.answers = {}
        self.skipped = set()
        self.paused = False
        self.completed = False
        self.cancelled = False
        self.pending_clarification = None
        self.pending_confirmation = None
        self.revising_question_id = None
        self.history = deque(maxlen=12)  # Six recent turns; bounded chars below.
        self._seen = set()  # Never evict IDs and accidentally accept an old event.
        self._lock = asyncio.Lock()
        self.metrics = deque(maxlen=100)  # Counts/timing only, no text or answers.

    @property
    def language(self):
        return self._language

    @property
    def question(self):
        return self.questionnaire.questions[self.index]

    def snapshot(self):
        state = dict(session_id=self.session_id, state_version=self.version,
                     output_language=self.language,
                     consent_granted=(self.questionnaire.questions[0].kind != "consent" or
                         self.answers.get(self.questionnaire.questions[0].id) == "نعم"),
                     current_question=self.question.model_dump(),
                     completed_answers=dict(self.answers), skipped=sorted(self.skipped),
                     paused=self.paused, completed=self.completed, cancelled=self.cancelled,
                     pending_clarification=self.pending_clarification,
                     pending_confirmation=(dict(self.pending_confirmation) if self.pending_confirmation else None),
                     revising_question_id=self.revising_question_id,
                     recent_history=list(self.history),
                     answered_question_ids=list(self.answers),
                     answered_questions=[q.model_dump() for q in self.questionnaire.questions
                                         if q.id in self.answers],
                     authorized_information=self.questionnaire.authorized_information)
        return Snapshot(self.session_id, self.version, self.question.id, [
            {"role": "system", "content": PROMPT},
            {"role": "system", "content": json.dumps(state, ensure_ascii=False)},
        ])

    def interrupt(self):
        # Invalidates in-flight/queued decisions without cancelling the survey.
        self.version += 1

    def _say(self, ar, en):
        return en if self.language == "en" else ar

    def opening(self):
        if self.questionnaire.introduction:
            return self.questionnaire.introduction + "\n\n" + self.question.text
        return self._say("مرحباً، أنا مساعد آلي للاستبيان.",
                         "Hello, I'm an automated survey assistant.") + " " + self.question.text

    def _reject(self, reason):
        text = self._say("خلّنا نتأكد من السؤال الحالي.", "Let's check the current question.")
        if reason in ("rating_not_explicit", "rating_constraint") and self.question.kind == "rating":
            # Never acknowledge a guessed rating as saved. Ask concisely instead
            # of replaying a question/scale the caller has already heard.
            return Result(False, self._say(
                f"عشان ما أختار رقم عنك، كم تقيّمها من {self.question.minimum} إلى {self.question.maximum}؟",
                f"So I don't choose for you, what rating would you give from {self.question.minimum} to {self.question.maximum}?"), reason)
        if reason == "required_question":
            text = self._say("هذا السؤال مطلوب. تقدر توقف مؤقتاً أو تلغي الاستبيان.",
                             "This question is required. You can pause or cancel the survey.")
        if self.paused:
            text = self._say("الاستبيان متوقف مؤقتاً. نكمل لما تكون جاهز.",
                             "The survey is paused. We can resume when you're ready.")
        elif self.completed or self.cancelled:
            text = self._say("انتهى الاستبيان.", "The survey has ended.")
        else:
            text += " " + self.question.text
        return Result(False, text, reason)

    def _validate(self, d, utterance):
        if self.cancelled or (self.completed and d.intent != "correct_previous"):
            return "ended"
        if d.current_question_id != self.question.id:
            return "question_mismatch"
        if self.paused and d.action not in ("resume", "finish", "stay", "pause"):
            return "paused"
        if d.intent == "correct_previous":
            if (self.paused or d.action != "repeat" or d.answer_status != "not_answered"
                    or not isinstance(d.answer_value, str) or d.answer_value not in self.answers
                    or any(q.id == d.answer_value and q.kind == "consent"
                           for q in self.questionnaire.questions)):
                return "invalid_correction"
            return None
        if d.action == "next_question":
            if self.paused or d.intent != "answer" or d.answer_status != "valid":
                return "invalid_commit"
            if (self.pending_confirmation and self.pending_confirmation["action"] == "skip"
                    and self.pending_confirmation["question_id"] == self.question.id
                    and normalize_answer(utterance) in {"نعم", "هيه", "ايوه", "أيوه", "yes", "yes please", "تفضل"}):
                return "pending_action_confirmation"
            q, value = self.question, d.answer_value
            consent = self.questionnaire.questions[0]
            if q.kind != "consent" and consent.kind == "consent" and self.answers.get(consent.id) != "نعم":
                return "consent_required"
            if q.kind == "consent":
                if value != "نعم":
                    return "consent_required"
            elif q.kind == "choice":
                if not isinstance(value, str) or value not in q.choices:
                    return "choice_constraint"
            elif q.kind == "rating":
                if type(value) is not int or not q.minimum <= value <= q.maximum:
                    return "rating_constraint"
                # Relevance belongs to the LLM. Require a rating token in this turn
                # as a second guard; confirmation may use a pending clarification.
                numbers = rating_tokens(utterance)
                confirmation = (utterance.strip(" .!؟?،").lower() in
                                {"yes", "yes please", "correct", "نعم", "ايوه", "أيوه", "صح", "هيه"}
                                and self.pending_clarification
                                and set(rating_tokens(self.pending_clarification)) == {value})
                if value not in numbers and not confirmation:
                    return "rating_not_explicit"
            elif not isinstance(value, str) or not value.strip() or len(value) > q.max_length:
                return "text_constraint"
            elif q.kind == "conditional_text":
                insufficient = {normalize_answer(v) for v in q.insufficient_answers}
                if normalize_answer(value) in insufficient or normalize_answer(utterance) in insufficient:
                    return "details_required"
            return None
        if d.answer_value is not None or d.answer_status == "valid":
            return "unexpected_answer"
        if d.intent == "skip" and d.action == "stay" and d.answer_status == "needs_clarification":
            if not self.question.optional:
                return "required_question"
            return "paused" if self.paused else None
        controls = {"repeat": "repeat", "skip": "skip", "pause": "pause",
                    "resume": "resume", "finish": "cancel"}
        if d.action in controls and (d.intent != controls[d.action] or
                                    d.answer_status != "not_answered"):
            return "intent_action_mismatch"
        if d.action == "stay" and d.intent not in ("answer", "clarify", "off_topic", "identity", "company"):
            return "intent_action_mismatch"
        if d.action == "skip" and not self.question.optional:
            return "required_question"
        if d.action == "resume" and not self.paused:
            return "not_paused"
        return None

    def _remember(self, utterance, spoken_response):
        self.history.extend([{"role": "user", "content": utterance[:2000]},
                             {"role": "assistant", "content": spoken_response[:2000]}])
        self.version += 1

    async def apply(self, snapshot, decision, utterance):
        async with self._lock:
            if (snapshot.session_id != self.session_id or snapshot.version != self.version
                    or snapshot.question_id != self.question.id):
                return self._reject("stale")
            reason = self._validate(decision, utterance)
            if reason:
                result = self._reject(reason)
                self.pending_clarification = None
                self.pending_confirmation = None
                self._remember(utterance, result.spoken_response)
                return result
            d = decision
            committed = False
            self.pending_confirmation = None
            text = d.spoken_response.strip()
            if d.action in ("next_question", "repeat", "resume", "skip") and any(
                    mark in text for mark in ("؟", "?")):
                # A transition bridge cannot ask its own question: Python owns
                # the exact next question. Keep a valid answer/action but repair
                # this presentation violation instead of asking twice.
                text = self._say("فهمت عليك." if d.action == "next_question" else "حاضر.",
                                 "Understood." if d.action == "next_question" else "Sure.")
            from voice_agent.conversation.language import speech_matches_language
            if not speech_matches_language(text, self.language):
                text = (self._say("شكراً لك.", "Thank you.") if d.action != "stay" else
                        self._say("خلّنا نكمل بالعربية.", "Let's continue in English.") +
                        " " + self.question.text)
                # Replacing a foreign clarification loses its specific proposal.
                self.pending_clarification = None
            else:
                self.pending_clarification = (text if d.answer_status == "needs_clarification" else None)
                if d.intent == "skip" and d.action == "stay" and d.answer_status == "needs_clarification":
                    self.pending_confirmation = dict(action="skip", question_id=self.question.id, prompt=text)
            if d.intent == "correct_previous":
                self.completed = False
                self.revising_question_id = d.answer_value
                self.index = next(i for i, q in enumerate(self.questionnaire.questions)
                                  if q.id == d.answer_value)
                text += " " + self.question.text
            elif d.action in ("next_question", "skip"):
                if d.action == "next_question":
                    self.answers[self.question.id] = d.answer_value
                    self.skipped.discard(self.question.id)
                    committed = True
                else:
                    self.answers.pop(self.question.id, None)
                    self.skipped.add(self.question.id)
                self.revising_question_id = None
                remaining = [i for i, q in enumerate(self.questionnaire.questions)
                             if q.id not in self.answers and q.id not in self.skipped]
                if remaining:
                    self.index = remaining[0]
                    text += " " + self.question.text
                else:
                    self.completed = True
                    if self.questionnaire.closing:
                        text = self.questionnaire.closing
                    else:
                        text += " " + self._say("اكتمل الاستبيان، شكراً لوقتك.",
                                                 "The survey is complete. Thank you for your time.")
            elif d.action == "pause":
                self.paused = True
            elif d.action == "resume":
                self.paused = False
                text += " " + self.question.text
            elif d.action == "repeat":
                text += " " + self.question.text
            elif d.action == "finish":
                self.cancelled = True
                text = self.questionnaire.cancellation_response or self._say(
                    "تم إلغاء الاستبيان. شكراً لوقتك.",
                    "The survey is cancelled. Thank you for your time.")
            self._remember(utterance, text)
            return Result(True, text, "accepted", committed)

    async def turn(self, event_id, utterance, decide, *, snapshot=None):
        # IDs identify recognition events, never transcript text: repeated 'four'
        # on different questions is legitimate. No persistence outside this session.
        async with self._lock:
            if event_id in self._seen:
                return Result(False, "", "duplicate")
            if len(self._seen) >= 4096:
                return Result(False, self._say("يرجى بدء جلسة جديدة.",
                             "Please start a new session."), "turn_limit")
            self._seen.add(event_id)
            snap = snapshot or self.snapshot()
        if not utterance.strip() or len(utterance) > 2000:
            return self._reject("transcript_bounds")
        if snap.version != self.version:
            return self._reject("stale")
        start = time.monotonic()
        try:
            q = next(q for q in self.questionnaire.questions if q.id == snap.question_id)
            messages = [*snap.messages, {"role": "system", "content":
                "LOCKED output language for the entire call: " + self.language +
                ". Do not switch language or quote words in another language." +
                ". Active question (this overrides any older question in history): " +
                q.model_dump_json() +
                ". Use this question kind only and return the exact action/status contract. "
                "Respond to the latest customer's concern first. For identity/company/"
                "authorization/service-identity questions, answer briefly and WAIT: do not "
                "append consent or the active survey question. For an exact rating endpoint "
                "description, ask to confirm its single numeric value without saving yet. "
                "Do not simply demand the scale again or repeat your preceding script. "
                "Use everyday spoken Arabic when configured ar; no administrative 'تم تسجيل' "
                "confirmations or generic 'كيف يمكنني مساعدتك' endings. An acknowledgement "
                "must be brief and fit the actual feedback. Avoid stock 'وصلت تقييمك', "
                "'وصلني تقييمك', or 'وصلت وجهة نظرك' replies and do not reuse your recent "
                "acknowledgement. A bare rating needs only a minimal transition, without "
                "announcing receipt or restating the rating. Do not rotate a fixed script. "
                "If the customer agrees to a pending skip offer for this question, return "
                "intent=skip, answer_status=not_answered, action=skip, answer_value=null. "
                "Agreement to skipping is not a valid questionnaire answer; do not reconfirm it. "
                "If history already shows the caller agreed to a skip and now asks why you "
                "did not skip, honor it with action=skip immediately, not another offer."},
                {"role": "user", "content": utterance}]
            decision, usage = await decide(messages)
            decision = Decision.model_validate(decision) if isinstance(decision, dict) else Decision.model_validate_json(decision)
            result = await self.apply(snap, decision, utterance)
        except asyncio.CancelledError:
            raise
        except Exception:
            usage = {}
            async with self._lock:
                if snap.version != self.version:
                    result = self._reject("stale")
                else:
                    result = self._reject("model_error")
                    self.pending_clarification = None
                    self.pending_confirmation = None
                    self._remember(utterance, result.spoken_response)
        self.metrics.append(dict(latency_ms=round((time.monotonic() - start) * 1000, 2),
                                 accepted=result.accepted, committed=result.committed,
                                 reason=result.reason, **{k: usage.get(k, 0) for k in
                                 ("prompt_tokens", "completion_tokens", "total_tokens")}))
        return result

    def clear(self):
        self.interrupt()
        self.answers.clear()
        self.skipped.clear()
        self.history.clear()
        self._seen.clear()
        self.pending_clarification = None
        self.pending_confirmation = None
        self.revising_question_id = None
