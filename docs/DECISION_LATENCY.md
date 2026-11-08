# Compact survey decisions and prompt cleanup

The model now returns five fields: `intent`, `status`, `value`, `action`, `reply`.
The provider expands these into the existing six-field `Decision`, supplying
`current_question_id` from the request's captured state. State-version checks,
answer validation, language checks, question rendering and public response formats
continue to use that existing decision. Extra/missing fields, invalid types,
refusals, incomplete output and stale replies are rejected.

The strict model schema is now the same across active question IDs. The JSON and
tool-call paths use the same compact schema and decoder. Tool permissions and the
reserved decision function name are unchanged.

The request's authoritative state already contains the active question and answer
requirements. The per-turn reminder references that state instead of repeating its
JSON and the full conversation policy. Repeated provider instructions and long
schema descriptions were shortened. The original shared behavioral policy remains,
using the compact field names, with a concise reminder for control/answer distinctions.

The model, sampling settings, token ceiling, number of requests, STT, TTS and VAD
settings are unchanged. There is no bypass for simple answers and speech still
waits for a validated complete decision.

This approach follows the structured-output and prompt-size suggestions in the
[official OpenAI latency guide](https://developers.openai.com/api/docs/guides/latency-optimization).
Provider behavior and actual timings still require measurement.

## Local live comparison, 8 November 2026

The existing synthetic conversation-style evaluator ran against the same configured
Azure deployment before and after the changes. The original code was commit
`14e0e08667f8685ebe2060b854707631bedc7064`. On the 33 cases present in both runs:

| Measurement | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Median decision/validation time | 1,078 ms | 875 ms | 18.8% |
| Mean input tokens per case | 4,977 | 4,314 | 13.3% |
| Mean generated tokens per case | 65.0 | 46.8 | 28.1% |

These are sequential local model-path measurements, excluding telephone, STT and
TTS. Matching uses journey, turn, input and active question; generated histories
can differ. This is a small comparison subject to model and network variation,
not a guaranteed server-side improvement. Intermediate tuning runs are excluded.

Offline verification: 96 unit tests and 83 integration tests ran, with one
platform-specific integration skip (178 passed, 1 skipped).

**The live semantic suite is not fully passing.** The baseline evaluated 33 of 36
planned turns, with 27 correct decisions and eight wording/confirmation failures;
it stops each journey at its first incorrect decision. The optimized run reached
all 36 turns, with 33 correct decisions, seven wording/confirmation failures and
one incorrect saved value on a newly reached self-correction case. On the 33
matching cases, the optimized run had 32 correct decisions and no incorrect saves.
Neither that matched result nor the passing mocked tests prove full semantic
correctness.

A direct comparison with identical prepared state and input reproduced the
self-correction error and an answer-plus-pause error with both original and
optimized code. Endpoint confirmation and skip-offer tracking also remain
unreliable in live results. These are existing model-interpretation limitations;
this change is a latency experiment, not a fix for those behaviors.

Evidence:

- [Before](../evaluations/results/decision_latency.before.json)
- [Final optimized run](../evaluations/results/decision_latency.after.json)
- [Matched comparison](../evaluations/results/decision_latency.comparison.json)
- [Direct checks against original code](../evaluations/results/decision_latency.edges.json)

## Repeat on the server

Use the normal pull/build process during an idle period. No environment changes
are needed. Make a controlled test call with the same survey, TTS provider and
answer patterns as the previous trace. Follow [the capture guide](LATENCY_TRACING.md).

`event=decision_usage` should now include `protocol=compact-v1`. Compare
`decision_model_and_tools`, `prompt_tokens`, `completion_tokens` and
`speech_end_to_transport_audio` against the previous call. The first metric
isolates model requests; the last includes the remaining STT/TTS/output delays.
Also check corrections, pause/resume, endpoint confirmations and skip offers.

To repeat the synthetic model-only evaluation (uses Azure model tokens, places
no telephone call):

```bash
uv run --frozen python -m evaluations.evaluate_conversation_style --live --interval 0.5 --output evaluations/results/decision_latency.retest.json
```

The evaluator returns a failing exit code when decision or wording checks fail;
inspect the report rather than treating faster timing as acceptance.
