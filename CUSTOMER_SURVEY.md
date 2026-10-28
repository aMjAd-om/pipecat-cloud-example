# Arabic customer-satisfaction survey

The active questionnaire is `questionnaire.customer-satisfaction.ar.json`. It
contains the user's exact Arabic introduction, permission question, eight survey
questions in order, and closing. Company facts are restricted to the supplied
description of شركة تنقيب البيانات. Unknown prices, contacts, service-provider
identity and privacy policies must not be invented.

## Configuration

The working copy's `.env` and `.env.example` select:

```dotenv
DEFAULT_LANGUAGE=ar
AZURE_TTS_VOICE_AR=ar-SA-HamedNeural
SURVEY_CONVERSATION_MANAGER=true
SURVEY_QUESTIONNAIRE=questionnaire.customer-satisfaction.ar.json
```

Set these same four values in the deployed app's existing `.env`. Keep Azure
credentials and the successful TCP TURN configuration unchanged. The configured
Foundry deployment remains GPT-4.1-mini. STT remains `ar-OM`; the requested TTS voice
uses SSML locale `ar-SA`. Azure lists Hamed as a Saudi Arabic male voice:
[Microsoft voice reference](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support).
The supplied closing retains the user's phrase “المساعدة الذكية” verbatim.

Rebuild/recreate the app on local Ubuntu using the existing Compose file:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml logs -f --tail=50 agent
```

No deployment is performed automatically. The VM's coturn, SSH tunnel, Nginx,
WebRTC implementation and Azure STT integration are not changed by this work.

## Conversation rules

The language is fixed when the session starts. Arabic, English, Omani expressions,
mixed speech and requests to switch can be understood as input, but the response
language always follows `DEFAULT_LANGUAGE`. No language detector chooses the TTS
voice anymore. Arabic configurations reject Latin-script model speech before it
reaches synthesis. If a valid survey decision has an English acknowledgement,
Python replaces only that acknowledgement with Arabic, retaining the validated
answer/action and exact next question. A foreign clarification is replaced with
the active question and is not retained as a specific pending confirmation.

A final frame processor checks the entire response before TTS, including the legacy
streaming assistant. It buffers at most 6000 characters, handles interruption and
uses a configured-language fallback for mixed or foreign output. This prevents
English tokens from being spoken before a later part of the response is checked.
It adds no LLM call. On the legacy path, speech starts after the response is buffered;
on the survey path JSON was already buffered for decision validation.

For `DEFAULT_LANGUAGE=en`, provide an English questionnaire to keep exact scripted
questions in English too. There is no automatic translation of authoritative text.

| Stage | Accepted answer / behavior |
| --- | --- |
| Permission | Explicit consent normalized to `نعم`; refusal cancels; no skip |
| 1. Overall experience | Explicit integer 1–5 |
| 2. Rating reason | Relevant explanation, interpreted against accepted overall rating |
| 3. Service speed | Explicit integer 1–5; waiting duration is not a rating |
| 4. Staff | Exactly `ممتازة`, `جيدة`, `مقبولة`, or `سيئة` |
| 5. Difficulties | Clear denial (`لا`) or a description of a service problem |
| 6. Improvements | Relevant suggestion or explicit no improvements needed |
| 7. Recommendation | Exactly `نعم`, `لا`, or `ربما` |
| 8. Additional comments | Relevant comment or explicit no further comments |

The permission stage precedes all eight survey questions. Python validates consent
and refuses to commit survey answers without it. Consent cannot be reopened as a
normal answer correction; withdrawal uses cancellation. All eight survey questions
permit an explicit skip, matching voluntary participation; answers are never silently
skipped or inferred. The older demo questionnaire's constraints are unchanged.

For question five, a bare “نعم” causes a request for the nature of the problem and
keeps the active question unchanged. “نعم، الموقع تعطل” can answer it in one turn.
If the problem description arrives on a later turn, recent history supplies the
pending clarification. Python rejects configured insufficient values even if the
model proposes advancing on them. Closed-choice answers must match the exact
configured canonical strings; arbitrary labels are rejected.

The seven-field decision schema, one model call per finalized turn, duplicate/stale
guards, pause/resume and bounded in-memory history remain in use. Accepted answers
are session memory, not a durable survey export; teardown clears them as before.

## Verification

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python evaluate_customer_survey.py --live
uv run --frozen python verify_services.py tts
```

The new evaluator uses only synthetic inputs, including Arabic and English-input
journeys that must both receive Arabic speech. It tests consent, company/identity
questions, ambiguity, unrelated numbers, staff/recommendation enums, conditional
problem detail, skip, pause and prompt injection. It records raw decisions separately
from rendered speech, so language fallback cannot inflate model-language correctness.
Free-text correctness still needs human review; the automated value check validates
nonblank text and the expected action, not a separate semantic judge.

Requests are paced three seconds apart in the evaluator only, to avoid quota bursts.
A failed journey stops before later answers can be sent to the wrong question.
No retry or additional LLM call is added to production turns. Raw evaluation files
contain only included synthetic dialogue; application sessions are not logged there.

Final verification, 28 October 2026:

| Check | Result |
| --- | --- |
| Full offline suite | 52 tests run: 51 passed; 1 Linux-only signal test skipped |
| Live Azure synthetic evaluation | 64/64 expected actions and constrained answers; no model errors |
| False commits / false advances in final live suite | 0 / 0 |
| Arabic-only rendered responses | 64/64 |
| Arabic-only raw model spoken responses | 64/64, before Python's language fallback |
| Full stateful survey journeys | Arabic input and English input both completed, including yes → problem detail follow-up |
| Model latency p50 / p95 | 1219 ms / 1453 ms; excludes evaluator pacing |
| Prompt / completion tokens | 203,021 / 2,703 (205,724 total) |
| Live Hamed synthesis | Passed; 103,200 PCM bytes, first audio at 844 ms in the independent TTS check |
| Real Chromium-family browser greeting | Connected and decoded non-silent survey introduction audio |
| Syntax / diff whitespace | Passed |

The real-browser greeting check uses a silent synthetic microphone and Azure TTS;
it verifies decoded audio, not physical speaker playback or a full spoken survey.
Reproduce against an already-running server with `node tests/greeting.mjs`, setting
`TEST_URL` and `BROWSER_PATH` where needed. No new mobile-device test or container
image build was performed in this change. The user's existing working VM relay and
networking configuration were preserved.

The final raw results are in `customer_survey_evaluation.json`. Human inspection of
free-text answers and company replies found relevant feedback and no invented contact
details. Free-text values can be translated or paraphrased rather than preserved
verbatim, sometimes with elaboration; the automated TEXT check does not measure that
fidelity. In `consent_identity`, the response correctly stayed at consent and identified
an automated assistant, but omitted the available company name when asked which
company it represented. These remain response-quality limits. Finite test success does
not prove semantic correctness for every possible utterance or establish a formal
Omani speech quality score.

Exploratory evaluation exposed quota errors when requests were sent in bursts,
unnecessary clarification of English choice labels, and a false acceptance of an
unrelated personal car problem. The evaluator was paced, choice mappings were made
explicit, and the prompt now requires an established link between a problem and
the surveyed service. The three personal-problem regressions (car, phone, family)
all stayed on the active question in the final run. A separate Python guard checks
both proposed value and current utterance against insufficient problem answers,
so a model cannot invent a detailed problem from a bare yes. The first exploratory
evaluator also incorrectly continued failed journeys with later questions' input;
it now stops a journey at its first failed step to avoid misleading commit metrics.
