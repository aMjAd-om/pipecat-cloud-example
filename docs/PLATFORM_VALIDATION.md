# Platform validation — 3 November 2026

The three implementation phases are integrated in the working tree. The final
offline suite, browser regression and live synthetic functional evaluation pass.
Real audio validation and human review of Arabic wording remain separate checks.

| Check | Result |
|---|---|
| Phase 1 configuration/context + legacy regression subset | 50 tests passed |
| Phase 2 tools/provider + legacy regression subset | 36 tests passed |
| Final full offline suite | 145 tests run: 144 passed, 1 existing Linux-only signal test skipped on Windows |
| Real browser text-lab regression | Passed, including agent/survey/customer selection and isolation |
| Voice frontend build and text-lab JavaScript syntax | Passed |
| Offline wheel build | Passed; both agents' JSON/prompts and synthetic context included |
| Diff whitespace checks | Passed |
| Initial live synthetic Azure evaluation | 5/16 checks passed; complete Bank Nizwa journey did not finish |
| Final live synthetic Azure evaluation | 24/24 functional scenarios passed, including distinct identity/purpose/company replies in registered and legacy modes; complete Bank Nizwa journey finished |
| Final live text/model latency | Median 1328 ms, maximum 3094 ms; excludes STT/TTS and the real audio/network path |
| Final live optional retrieval | Approved visit handler invoked once for additional history; two model requests, 2359 ms total |
| Final live common turns | One model request each; no unnecessary retrieval requests |
| Raw vs rendered informational replies | Four raw replies appended questions; production rendering removed them. All fourteen rendered informational replies passed the no-extra-question and no-automatic-reminder checks |

Offline coverage includes registry validation (missing prompt/survey, unknown
agent/tool/workflow, invalid configuration), context isolation, tool registration,
authorization, argument validation, handler failure, timeout, wrong agent/customer
binding, strict structured-output round trips, bounded tool iterations, retained
retrieval context, interruption/stale results, per-session language/voice isolation,
trusted telephone routing, HTTP selection gating, and all prior survey/API/audio
adapter regressions. Added regressions ensure the final output function has no
executable handler, cannot bypass consent validation, cannot collide with a tool
registration, and does not accept multiple/free-text decisions. Renderer tests cover
exact question copies, interrogative prefixes, paraphrased reminders, intact facts,
and unchanged genuine answer clarifications.

An intermediate full-suite run exposed a reminder cancellation race: if playback
finished while caller speech cancelled a task waiting through `asyncio.wait_for`,
the reminder could survive cancellation. The playback wait now uses
`asyncio.timeout`, and both the targeted regression and final full suite passed.

The latest live report is `evaluations/results/caller_questions_evaluation.json`.
It adds the reported `من معي؟` → `ليش تتصل؟` sequence and a company-name question
in both registered and legacy Bank Nizwa modes. The former 16-case report remains
at `evaluations/results/platform_evaluation.json`. Earlier
reports are retained as `.initial.json` (5/16), `.protocol.json` (11/16), and
`.semantic.json` (13/16), documenting the fixes rather than hiding failed runs.
Reports contain only synthetic inputs/outputs and safe metrics, not credentials
or full private prompts.

Observed live failures included unparseable/incomplete decisions, invalid
intent/status combinations after retrieval, and consent requests in place of
answers to visit questions. A controlled diagnostic showed that a tool-enabled
request returned two separate JSON objects while the same request without tools
returned one valid decision. This was observed on the configured Azure
`gpt-4.1-mini` deployment, not inferred solely from a parser exception.

The tool-enabled provider now uses a reserved strict `propose_survey_decision`
function as its output format. That output has no backend handler, performs no
state changes, and returns the same six-field Decision to Python. Retrieval tools
still execute only through the permission-checked executor. The no-tool path keeps
the existing strict `response_format`; no model migration or extra request for
ordinary replies was introduced. The schema pins the active question ID, and
field descriptions clarify refusal, informational replies and correction targets.

Shared guidance now separates pre-consent explanations from answer collection,
clarifies that declining an out-of-scope request is not the customer refusing the
survey, and specifies the correction contract. The bank prompt distinguishes the
current preloaded visit from additional history available through its approved
tool. Direct-response rendering removes model-generated question reminders. Identity,
purpose, company and visit explanations now wait for a new customer turn with the
current question/consent unchanged; they never schedule another consent prompt.
Remaining unrelated-turn reminders repeat the question without the robotic
`نرجع لسؤالنا` prefix. The evaluator records raw model violations alongside
the final rendered speech; passing functional output does not imply perfect raw
wording. A failed journey now stops before sending answers to the wrong question.

Automatic approval review initially rejected further live diagnostics because they
send composed instructions and synthetic context to Azure. The user subsequently
explicitly approved that scope, and diagnostics resumed with demo records only.

Natural Omani Arabic, semantic relevance and appropriate conciseness require human
review beyond the automated keyword, language and word-count diagnostics. Some raw
replies still over-explain or formally announce a rating; these stylistic limitations
are not scored as perfect Omani Arabic by the functional evaluator. Real
Azure STT, Azure/Cartesia TTS, microphone playback, deployed Asterisk calls, actual
hangup timing, and end-to-end WebRTC/audio latency were not newly tested. Existing
offline adapter tests passed. No deployment, model change, real customer access or
production system write was performed.
