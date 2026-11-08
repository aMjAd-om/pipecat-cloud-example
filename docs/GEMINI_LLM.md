# Gemini and Azure language model selection

`LLM_PROVIDER` selects the model used by telephone calls, the separately launched
text survey lab, and all five live model evaluation commands. The default is
`azure`, preserving existing installations. Restart/recreate the process after
changing `.env`; selection is not an in-call switch.

## Select Gemini Flash-Lite

Add these settings to the private server `.env`:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_actual_google_key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_THINKING_LEVEL=minimal
```

Only `GEMINI_API_KEY` is required for the Gemini LLM. The model and thinking level
above are defaults. Azure OpenAI credentials are not required while Gemini is
selected; existing Azure Speech credentials remain required for recognition.
Keep the same `TTS_PROVIDER` and endpoint settings to isolate the model change.
The application does not change or print the private key.

This integration uses the Gemini Developer API's fixed HTTPS
`https://generativelanguage.googleapis.com/v1beta/openai/` endpoint through the
existing OpenAI client. It requires no additional Python SDK. It does not use
Vertex AI credentials or an Azure Gemini deployment.

After pushing/pulling this code, rebuild during an idle period:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml logs --follow --no-color agent
```

Restarting ends active calls. At the next call, the `event=providers` trace should
include `llm_provider=gemini model=gemini-3.5-flash-lite`.

## Decision and streaming behavior

The existing compact JSON decision schema, output validation, tool allowlist,
tool-round limits and stale-turn checks apply to both providers. Gemini tool
calls retain their provider-specific thought signatures when tool results are
sent back. Malformed, refused or truncated decisions do not bypass validation.
There is no automatic cross-provider retry or fallback.

Gemini requests use temperature 1.0 and a 2048-token output ceiling to leave room
for thinking as well as the compact reply. This is a ceiling, not a request for
long speech; the same reply-length validation remains in place. Minimal thinking
is explicitly sent through Google's native `thinking_config` in `extra_body`,
including on the legacy streaming assistant path. Azure request settings remain
unchanged. Both clients keep a 30-second request timeout and no SDK retries.

The Python SDK merges its `extra_body` argument into the request root. Google's
REST format requires an `extra_body.google.thinking_config` field, so the adapter
nests a literal `extra_body` inside that SDK argument. A root-level `google`
field is rejected with HTTP 400.

The compatibility API also receives one combined system instruction containing
the policy, authoritative survey state, agent instructions and turn reminder.
Separate system messages can replace earlier ones. Untrusted runtime context,
customer text and tool results retain their separate user/tool roles.

`GEMINI_THINKING_LEVEL` accepts `minimal`, `low`, `medium` and `high`. Model-specific
support still matters: for example, Gemini 3.8 Flash requires `low` or higher.
Higher levels may increase decision latency. Invalid provider names, empty model
names, invalid thinking levels and missing selected-provider credentials fail
startup validation. An unavailable model or account quota is reported by the API
when a request is made; model access is not guaranteed by local validation.
Telephone traces emit `event=decision_error` with the exception type and HTTP
status on request/parsing failures, and `event=decision_result` with the validated
turn outcome. They do not log API error bodies, prompts or credentials.

## Compare before using on calls

The existing evaluation commands read the same `.env`. They make billed model
requests using synthetic scenarios; they place no telephone calls. For the
conversation/control regression suite, run from the checkout:

```bash
uv run --frozen python -m evaluations.evaluate_conversation_style --live --interval 1 --output evaluations/results/conversation.gemini.json
```

Repeat with `LLM_PROVIDER=azure` and a separate output filename to retain both
reports. Reports identify the provider and model; the conversation suite reports
median and p95 turn latency. Compare corrections, pause/resume, skip confirmation,
false commits, wording failures and evaluated-versus-planned turns, as well as
speed. Journeys stop at their first incorrect decision. Known existing semantic
failures are documented in [the compact-decision experiment](DECISION_LATENCY.md);
passing mocked tests does not establish live model accuracy.

For registered agents with retrieval tools, also run:

```bash
uv run --frozen python -m evaluations.evaluate_platform --live --interval 1 --output evaluations/results/platform.gemini.json
```

Use [the telephone trace guide](LATENCY_TRACING.md) for the final call comparison.
`decision_model_and_tools` measures the complete model/tool stage, including
thinking. First-token speed alone is insufficient for this validated-decision
path. Keep the provider fixed during each call.

To return to Azure, set `LLM_PROVIDER=azure`, retain valid `AZURE_OPENAI_API_KEY`,
`AZURE_OPENAI_BASE_URL` and `AZURE_OPENAI_DEPLOYMENT`, then recreate the agent.
Removing `LLM_PROVIDER` also selects Azure.

Offline verification, without provider calls:

```bash
uv run --frozen python -m unittest discover -s tests -q
```

Official references:

- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [Gemini thinking configuration](https://ai.google.dev/gemini-api/docs/thinking)
- [Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
