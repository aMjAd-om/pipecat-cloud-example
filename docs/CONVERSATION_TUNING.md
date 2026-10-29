# Conversational response tuning

## Contextual wording across questionnaire types

Normal replies are generated from general conversation rules, recent dialogue,
and the current question's `response_requirements`. The prompt contains no complete
reply scripts or memorized company names. Guidance comes from the question's kind,
relevance, numeric bounds, choices, optionality, text length, and insufficient-answer
rules. A different scale or survey topic does not require another response template.

For relevant qualitative feedback on a rating question, the model acknowledges the
meaning when useful and politely invites the customer's own numeric choice. It
should adapt repeated clarifications to what remains unclear, without choosing a
score, treating praise as an invalid answer, or attaching the same courtesy phrase
to every request. Choice and text questions use their own answer formats.

Python still owns validation and state. Fixed questionnaire text, pickup greetings,
and defensive fallbacks for invalid proposals/provider failures are separate from
normal model-generated conversation. The one-second return-to-question behavior
continues for accepted non-answer turns; it does not duplicate a pending clarification.

`evaluations/cases/response_style_cases.py` includes synthetic scenarios for several
rating scales, choices, free text, conditional details, and consent. Offline tests
check metadata and state boundaries without demanding a preferred reply sentence.
Live checks assess decision contracts, repetition, and optional semantic style:

```bash
uv run --frozen python -m evaluations.evaluate_response_style --live --judge-style
```

This command sends synthetic cases to the configured model and makes additional
model calls for the style assessment. Results go to
`evaluations/results/response_style_evaluation.json`. The semantic assessment is
advisory and cannot guarantee the quality of every future reply; inspect the saved
responses as well as the pass counts. No live customer dialogue is used.


Company and identity intents classify a turn; they do not choose a canned reply.
The model answers the specific question using questionnaire facts. A company-name
reminder needs only the company name, without repeating the assistant introduction
or company biography. The prompt uses the name in the current questionnaire.

For accepted non-answer turns that stay on the active question, the application
returns a separate `follow_up` with a one-second `pause_seconds`. Voice waits for
the direct reply to finish playing, pauses, then returns to the exact current
question. The chat displays the same reminder after one second. New caller speech,
a new chat submission, interruption, reset, or a changed survey state cancels a
pending reminder. Paused surveys and pending answer clarifications do not receive
this reminder. The direct reply remains in `spoken_response`; the model must not
append its own duplicate survey question.

The historical measurements below predate this separate reminder behavior.

This change addresses the caller examples supplied from the text lab: repeated introductions/consent requests, failure to answer who is calling or why, unsupported service attribution, demanding the numeric scale after clear verbal feedback, and repeating questions when the caller expresses frustration.

## Behavior changes

- Identity, company, purpose, call authorization, number source, and service-identity questions are separate concerns. The LLM answers the current concern briefly and waits, rather than appending the consent question or a rating request after every reply.
- The reference information now explicitly distinguishes the survey executor, شركة تنقيب البيانات, from the unidentified service provider. It does not supply an authorization or number source, so the agent must not invent one.
- Arabic replies are guided toward everyday polite Omani/Gulf speech, without administrative confirmations, repeated disclaimers, or a fixed “شكراً” on every turn. Contextual transition bridges are allowed; Python still supplies the exact questionnaire question.
- Relevant verbal rating descriptions are handled as clarification, not unrelated speech. “الخدمة جيدة” prompts a short request for the customer's chosen number, without guessing a rating.
- An exact scale-endpoint description can prompt confirmation of its one associated number. “بطيئة جداً” on the speed question may elicit “واضح إن السرعة ما ناسبتك، تقصد نعتمد واحد؟”. Nothing is saved until the customer supplies a number or explicitly confirms that specific pending number. Vague praise is not mapped to a guessed score.
- Frustration is acknowledged using the actual history; the model is instructed to stop restarting the full scale explanation.
- If a model proposes an unspoken/guessed rating anyway, the existing validator rejects the answer. Its numeric fallback now asks concisely for the customer's chosen rating instead of replaying the full question.
- A model-written question in a transition bridge is replaced before Python appends the next question. This prevents a valid resume/advance from asking two questions in the same reply.

The seven-field decision schema, action/intent enums, one model request per finalized turn, authoritative state validation, consent requirements, language lock, and exact questionnaire text are preserved. This is prompt/context and rendering work; the model itself is not retrained. Browser/telephone transports and Azure STT/TTS implementations are unchanged.

## Validation and reproducibility

Offline checks:

```bash
uv run --frozen python -m unittest discover -s tests -t . -v
```

`tests/unit/test_conversation_style.py` adds regressions for rejecting guessed numeric answers, accepting a specifically confirmed endpoint, preventing a vague yes from inventing a rating, preserving state while answering concerns, and removing duplicate transition questions. The full suite includes the existing survey benchmark/state-contract cases.

Live synthetic evaluation:

```bash
uv run --frozen python -m evaluations.evaluate_conversation_style --live
```

This explicitly makes billable calls to the configured Foundry GPT-4.1-mini deployment. The original style suite exercises 27 turns: caller concerns, rating clarifications, repeated frustration, slow/fast endpoint confirmation, irrelevant numbers, ambiguous numbers, prompt injection, pause/resume, explicit correction, and normal consent/answer progression. Nine skip-confirmation turns have since been added (36 total when running every journey). Requests are paced by default. No customer audio or phone numbers are sent by this evaluator.

Results, actual spoken text, proposed JSON, validation outcomes, latency and token counts are in [conversation_style_evaluation.json](../evaluations/results/conversation_style_evaluation.json). The `--journey` option can run a selected scenario without repeating the whole evaluation.

Initial style-tuning results: 76 offline tests ran (75 passed, one platform-specific skip). All 27 live synthetic turns matched their expected actions/commit outcomes, with zero false commits, zero unexpected answer changes, and zero failures of the targeted wording checks. Median model-decision latency was 1,187 ms; usage was 125,342 prompt tokens and 1,425 completion tokens for that run. Earlier iterative trials consumed additional tokens and are not included in those totals. Latency excludes STT/TTS and is not an end-to-end voice latency measurement.

## Follow-up: confirming a skip

The caller reported a loop after answering “نعم” to “هل تفضل تخطي السؤال؟” on the optional employee question. The old contract had no explicit pending action offer: an answer-shaped decision for “نعم” could be rejected as an invalid employee choice and replay the question. The caller's exported decision JSON was not supplied, so the precise failed proposal was not observable; the contract gap was reproducible and is addressed here.

An offer to skip now has a defined non-mutating decision: `intent=skip`, `answer_status=needs_clarification`, `action=stay`, `answer_value=null`. After accepting that proposal, Python stores `pending_confirmation` with action, current question ID, and the actually spoken offer. This metadata goes into the next model context and the text lab inspector.

Agreement is interpreted by the LLM as `skip/not_answered/skip/null`, not a choice answer. Python still enforces optional-question constraints and version checks. It rejects a bare affirmative being committed as a questionnaire answer when a skip confirmation is pending—even for a question where “نعم” itself is a valid choice. Refusal, a new unrelated concern, transitions, rejection fallback, and session cleanup clear pending metadata as appropriate. Pending confirmation never follows the cursor into a later question.

No new decision fields or enum values, keyword-based automatic skip execution, extra LLM requests, or transport changes were introduced. A targeted history example also teaches recovery when the caller already confirmed and asks why the skip was not performed.

Five new offline tests cover the offer/confirmation flow, duplicate and stale transcripts, refusal/new concerns, required questions, unsafe answer-shaped confirmations, invalid offers, and cleanup. The current full suite runs 81 tests (80 pass, one platform-specific skip). The real-browser chat regression also passes a complete path through the employee question, pending offer display, “نعم”, and the next difficulties question without recording an employee answer.

The targeted live results are in [skip_confirmation_evaluation.json](../evaluations/results/skip_confirmation_evaluation.json). They cover nine new skip turns plus four consent/rating regression turns. The first trial passed the new offer/yes flow but reconfirmed once when recovering from an old erroneous history; a more explicit recovery example then passed the isolated repair case ([trace](../evaluations/results/skip_confirmation_repair_evaluation.json)), followed by a repeat of the targeted suite.

Final targeted run: 13/13 turns passed, with zero false commits, zero unexpected answer changes, and zero targeted wording failures. Median model-decision latency was 1,219 ms; the final run used 71,755 prompt tokens and 608 completion tokens. Those totals exclude the earlier trial and isolated repair check. No deployed chat or phone process was automatically restarted.

Quality checks cover specific defects: pushing consent/current question during concern handling, failing to confirm a single endpoint, administrative acknowledgement phrases, and duplicate transition questions. These checks are limited heuristics, not an objective measure of natural conversation; human review of the recorded replies is still needed. Earlier tuning passes exposed continued consent pushing, formal acknowledgements, and one repeated-scale response. Those findings drove the final prompt and rendering changes.

## Try it in the chat lab

After updating the files, stop and restart the standalone lab:

```bash
uv run --frozen python -m voice_agent.api.survey_chat
```

Open http://127.0.0.1:7862 and click **New conversation**. Restarting reloads the Python prompt; a fresh conversation removes the previous robotic replies from recent history.

For a Docker lab on Ubuntu, stop the old lab process/container, rebuild the image, and launch it again from the application checkout:

```bash
docker compose -f docker-compose.ubuntu.yml build agent
docker compose -f docker-compose.ubuntu.yml run --rm --no-deps agent python -m voice_agent.api.survey_chat
```

This does not recreate the running phone agent. Once the wording is satisfactory, rebuild/recreate that agent to apply the same changes to voice calls:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
```

No automatic deployment or lab restart was performed by this change.

## Repeated rating acknowledgements

Removed the affirmative example that anchored replies to “وصلت وجهة نظرك”. The
prompt now explicitly discourages “وصلت تقييمك” and similar receipt announcements,
asks the model to consult recent assistant replies, and distinguishes minimal
rating transitions from brief responses to substantive feedback. The turn reminder
reinforces this guidance, and the live style evaluator flags these stock phrases.
This is a prompt change; answer validation, state transitions, and exact questionnaire
wording are unchanged. No live model evaluation was run for this adjustment, so
variation in generated replies still needs review in the chat lab.

The five existing conversation-style regression tests pass for this adjustment.

## Remaining limits

The actual service name and provider are still unspecified. The agent can acknowledge that honestly but cannot identify the service until the questionnaire's authorized information is populated with that fact. It also cannot establish who authorized contact or where a number originated without verified information supplied by the operator.

The model may reuse examples or occasionally produce awkward wording. Naturalness is not guaranteed by a finite test suite, and these results do not measure phone pronunciation, STT errors, VAD, or audible interruption quality. The next iteration should use exported chat traces showing actual/desired replies. Existing answer guards remain necessary because semantic relevance still depends on the LLM.
