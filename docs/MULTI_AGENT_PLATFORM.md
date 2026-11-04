# Configurable voice-agent platform

One voice pipeline serves independent, configurable agents. This is a refactor of
the existing application, not a second orchestration framework. Each call has its
own manager, selected agent, context, decision client and permitted tool executor.
No agent can select another customer's data through an LLM argument.

## Code structure

```text
src/voice_agent/
├── agents/
│   ├── models.py                  # AgentConfig, voice preferences, SessionSelection
│   ├── registry.py                # Discover, validate and preload trusted configurations
│   ├── runtime.py                 # Shared per-call factory and safe selection catalog
│   ├── bank_feedback/
│   │   ├── agent.json
│   │   └── prompt.md
│   └── municipality_complaints/
│       ├── agent.json
│       └── prompt.md
├── context/
│   ├── models.py                  # Customer, interaction and call context
│   ├── loader.py                  # Deterministic preload and scoped demo repository
│   └── demo_records.json          # Explicitly synthetic, opt-in test records
├── tools/
│   ├── registry.py                # Explicit schema-to-handler registration
│   ├── executor.py                # Permissions, validation, trusted context, timeout
│   ├── definitions/visits.py      # Strict model-visible function schema
│   └── handlers/visits.py         # Read-only async visit lookup
├── conversation/
│   ├── composer.py                # Policy + agent + task + runtime context layers
│   ├── rendering.py               # Direct replies without duplicated question reminders
│   ├── manager.py                 # Existing authoritative survey state machine
│   ├── models.py                  # Existing question and six-field decision contracts
│   ├── prompts.py                 # Shared conversation and survey decision policy
│   ├── guidance.py                # Current-question answer requirements
│   ├── answers.py
│   └── language.py
├── api/                           # Existing browser, telephone and text entry points
├── voice/                         # One Pipecat pipeline, pickup, playback, language guard
├── providers/                     # Existing Foundry/STT/TTS adapters
├── transports/                    # Existing WebRTC and Asterisk adapters
└── observability/                 # Existing timing and sanitized diagnostics
questionnaires/
├── bank-nizwa.ar.json
├── municipality-follow-up.ar.json
└── existing survey examples
```

All new packages also include `__init__.py`. No empty integration layer or
duplicate workflow manager was added. The supported workflow is currently
`survey`, including the municipality follow-up example. Other workflow types
are rejected until their authoritative state transitions are implemented.
Hospital appointment and government information integrations are not implemented.

## Configuration and backward compatibility

Without `AGENT_ID` or a session selection, the original `SURVEY_QUESTIONNAIRE`,
`DEFAULT_LANGUAGE`, `TTS_PROVIDER` and voice settings continue to apply. Existing
questionnaire IDs, custom trusted filesystem paths, API routes and empty session
creation bodies remain supported. `SURVEY_CONVERSATION_MANAGER=false` still
selects the legacy assistant unless a registered agent is explicitly selected.

An operator can select a default registered agent:

```dotenv
AGENT_ID=bank_feedback
```

The packaged Bank Nizwa configuration is:

```json
{
  "agent_id": "bank_feedback",
  "name": "Bank customer feedback (demo)",
  "description": "Short Bank Nizwa visit feedback demonstration, conducted by Data Mining.",
  "prompt_file": "prompt.md",
  "workflow_type": "survey",
  "survey_id": "bank-nizwa.ar",
  "language": "ar-OM",
  "tools": ["get_customer_visits"],
  "context": ["customer_profile", "recent_interaction", "call"]
}
```

Optional `survey_ids` adds explicitly allowed alternative surveys. A client cannot
select an arbitrary file or a survey belonging only to another agent. Optional
`voice` accepts `provider` (`azure`, `cartesia` or `munsit`) and `voice_id`. Preferences and
language are passed to each service instance without modifying process environment
variables. Arabic agent locale `ar-OM` maps to the manager's fixed `ar` output
language; STT keeps the locale. TTS uses the selected provider's configured voice.

The registry discovers `agents/*/agent.json`, validates strict immutable models,
requires the directory to match `agent_id`, resolves the prompt inside that
directory, validates workflow/surveys/tools, and preloads questionnaires and prompts.
Unknown IDs and invalid references fail before voice services are allocated.
Restart after editing registered agent configurations, prompts or questionnaires.
Legacy text-lab questionnaire files still reload on a new conversation.

## Prompt composition and deterministic state

1. Shared policy: existing `conversation/prompts.py`, preserving its established
   response-style, consent, question-format and output-contract sections.
2. Agent instructions: the selected `agents/<id>/prompt.md`.
3. Task definition/state: the validated questionnaire, current question, accepted
   answers, history, pending clarifications and allowed actions.
4. Runtime context: factual customer/interaction data, provided as explicitly
   untrusted data in a user-role envelope. Tool results use tool-role messages;
   bounded previously retrieved facts remain untrusted on later turns.

The composer preserves the legacy policy/state message positions used by existing
evaluators. An agent prompt supplements the global policy; it does not replace the
decision schema. Customer text, history and dynamic context never grant permission
to change instructions, call tools, or write state.

The LLM interprets meaning and proposes one structured decision. Python validates
and commits only the current question's answer. Multiple answers in one utterance
cannot silently populate future questions. Existing correction, duplicate/stale
turn guards, interruption, skip confirmation, pause/resume and language checks remain.
Registered agents close voice calls after completion/refusal playback by default
(`end_call_on_finish=true`). Legacy questionnaire behavior is preserved. Disputed
visits use the configured wrong-recipient apology, flag `discrepancy`, and end the
call without insisting the synthetic record is correct.

The final-decision schema pins `current_question_id` to the active question, even
for corrections; the correction target remains in `answer_value`. Field descriptions
state the intent/status/action combinations. These constraints supplement rather
than replace the manager's authoritative validation.

For informational company/identity/off-topic turns, the response renderer removes
exact question replays (including an added `هل`) and question sentences. The manager
only schedules a separate timed reminder for unrelated non-answer turns. Caller
identity, purpose, company and interaction questions get one direct reply, followed
by waiting for the customer with the active question unchanged. Genuine missing-answer clarifications retain their
questions. Live reports record raw model question violations separately, so rendering
repairs are visible rather than counted as correct raw model wording.

Answers, discrepancy flags and tool facts are session memory only. There is no
durable result store. The text lab supports an explicit trace export; a real
repository/persistence integration remains future work.

## Customer context and isolation

`ContextLoader` selects an authorized demo record for the chosen agent before
the opening. No selection means metadata is unavailable; no records are invented.
Example context for `bank_feedback` + `demo-ahmed`:

```json
{
  "customer": {"id": "DEMO-BANK-001", "name": "أحمد", "language": "ar-OM"},
  "interaction": {
    "organization": "بنك نزوى", "branch": "الخوير",
    "visit_date": "2026-11-02", "visit_time": "10:30",
    "service": "فتح حساب", "visit_reference": "DEMO-VIS-1025"
  },
  "call": {
    "call_id": "backend-generated-session-id", "agent_id": "bank_feedback",
    "caller_organization": "شركة داتامايننج",
    "purpose": "استبيان تجريبي عن زيارة بنك نزوى",
    "authorized_by": "Demo test operator; no bank authorization implied"
  },
  "demo": true,
  "source": "synthetic demo fixtures"
}
```

These are fabricated testing records, not customer data or evidence of bank
authorization. Phone numbers and financial information are omitted entirely.
Customer lookup IDs and call IDs are removed from model-facing context. The model
can answer `أي زيارة؟` from the already loaded branch/date without a tool request.
The actual spoken dates/wording are generated from those facts, not hardcoded.

The backend binds the selected record to the generated browser session ID or
trusted Asterisk channel ID. A tool receives this immutable context; its schema
has no customer/agent/session ID argument. The demo repository checks both the
bound customer and agent IDs. Municipality fixtures and tools are isolated from
bank calls. The loader/repository boundary is where a future authorized database
or API lookup should be added, never inside model-supplied code.

## Tools and Foundry execution

`tools/definitions/visits.py` defines the strict `get_customer_visits(limit)` schema.
`limit` is an integer from 1 to 5; extra fields, including a customer ID, are rejected.
The async handler in `tools/handlers/visits.py` receives backend-injected context and
the repository. `ToolRegistry` verifies names, unique registration, async handler
signatures and strict argument schemas. Only tools listed in the agent configuration
are sent to Foundry. The municipality example has none.

`ToolExecutor` checks registration, permissions and agent/context binding, validates
arguments with Pydantic, and executes with a three-second timeout. It bounds result
size and logs only registered tool name, static outcome and duration. Exceptions,
arguments, customer IDs and records are not logged. Cancellation propagates.

The existing `FoundryDecisionClient` uses the existing Chat Completions interface:

```text
customer turn → model with permitted function schemas
             → optional function request → validated executor
             → tool result → model's final structured Decision
             → existing ConversationManager validation and state transition
```

Ordinary replies still need one model request. Without optional tools, the provider
uses the existing strict `response_format`. With optional tools, it returns the
same six-field Decision through a reserved strict function schema,
`propose_survey_decision`. This is an output channel, with no registered executable
handler or side effects; Python still validates the proposal. It is not counted
as a retrieval invocation. The reserved name cannot be registered as a backend tool.
This avoids mixing free-text structured output and function calls, which the
configured deployment was observed to handle unreliably.

A requested retrieval adds a model round trip; at most two retrieval rounds are
executed, one call per round, followed by a request forced to use the decision
output schema. Provider requests have a 30-second timeout. Stale turns
cannot start another tool or commit their final decision. Tool failures return a
short configured-language fallback without advancing state or adding a second
uncontrolled conversation loop. Up to two successful results are retained in the
session-bound client as untrusted factual context for follow-up questions.

Example successful invocation (also covered by offline provider round-trip tests):

```json
{"name": "get_customer_visits", "arguments": {"limit": 2}}
```

With backend context bound to `demo-ahmed`, the executor returns `ok: true`,
`demo: true`, and only that customer's synthetic الخوير and نزوى visits. It never
uses a model-generated customer identifier. Survey consent and the active question
remain unchanged while the model explains the retrieved history.

Model-call counts, token totals, tool-call counts, tool latency and safe execution
events are included in existing per-turn metrics. The local text inspector shows
tool names/outcomes/durations, not private prompts. No extra LLM calls are used to
load profiles, validate/save answers, advance questions or end calls.

Protocol reference: [official OpenAI function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling).
This implementation keeps Azure's configured `/openai/v1/` endpoint and deployment;
no model migration, LangChain or LangGraph was introduced.

## Browser and telephone testing

The standalone loopback-only text lab always enables demo selectors:

```bash
uv run --frozen python -m voice_agent.api.survey_chat
# http://127.0.0.1:7862
```

Select an agent, a permitted survey and a synthetic customer, then click **New
conversation**. The active agent/task and demo label are shown separately from the
next-session selectors. Start Bank Nizwa with Ahmed and try `أي زيارة؟`,
`من أي شركة تتصلون؟`, then a request for older visit history. Switch to the
municipality agent and confirm the bank customer options and previous answers
are absent. Export chat for a synthetic trace including decisions and tool timing.

For browser voice testing, enable `DEMO_TESTING_ENABLED=true` in the server's
environment and restart. This exposes `/api/agents` and selection controls on
the existing voice page at port 7860. Leave it false on public deployments.
Selection requests are rejected while disabled; the legacy empty request works.
No full prompt, credentials or customer record is returned by the catalog.

Both existing session endpoints now accept the optional JSON body:

```json
{"agent_id": "bank_feedback", "survey_id": "bank-nizwa.ar", "mock_customer_id": "demo-ahmed"}
```

The voice endpoint retains its existing cookie ownership and origin checks. The
text endpoint retains its loopback, host/origin and mutation-token checks.

For telephone calls, set `TELEPHONY_AGENT_ID=bank_feedback` to choose a default
telephone agent (otherwise `AGENT_ID`, then legacy survey settings apply). For
per-call context, set `TELEPHONY_CALL_CONFIG` to an operator-owned JSON file:

```json
{
  "actual-asterisk-channel-id": {
    "agent_id": "bank_feedback",
    "mock_customer_id": "demo-ahmed"
  }
}
```

Enable `DEMO_TESTING_ENABLED=true` when using synthetic customer routes. The file
is read before starting the call's pipeline; its key is the actual channel ID,
not the shared `connection_id` (`pipecat`). A call absent from the file gets no
customer metadata. Agent/customer fields sent in media payloads are ignored.
Use the existing Asterisk setup/call procedure. No PBX integration, deployment,
public routing or container configuration was changed. If using Docker, the
operator must make the optional routing file available inside the container.

On Ubuntu, rebuild and restart the standalone chat process after applying code:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml exec -d agent python -m voice_agent.api.survey_chat
```

## Adding an agent, tool, or survey

**Agent:** create `agents/<id>/agent.json` and `prompt.md`. Use a unique directory/ID,
the supported `survey` workflow, an existing survey ID and only approved tool names.
Add an agent-scoped demo fixture only if needed. Restart; the registry validates
and includes it in the test catalog. No pipeline or global prompt changes are needed.

**Tool:** define a strict Pydantic argument model in `tools/definitions/`; implement
an async `handler(context, arguments)` in `tools/handlers/`; register its explicit
callable in `default_tools()`; add its name only to authorized agent configurations.
Keep customer identity in backend context, use I/O timeouts, sanitize outputs/logs,
and add permission/failure/isolation tests. There is no dynamic function import or
arbitrary Python execution path. External side effects would need their own backend
authorization rules; none are implemented here.

**Survey:** create a questionnaire JSON under `questionnaires/` using the existing
schema. Put organization facts in `authorized_information`, wording in
`introduction`/`questions`/closings, answer requirements in question metadata. Set
the agent's `survey_id` or add the ID to `survey_ids`, or use legacy
`SURVEY_QUESTIONNAIRE`. No shared prompt edits are needed.

## Verification and limits

```bash
uv run --frozen python -m unittest discover -s tests -t . -q
npm --prefix frontend/voice run build
node tests/browser/survey_chat_browser.mjs
uv run --frozen python -m evaluations.evaluate_platform
# Explicit opt-in: synthetic Azure calls and read-only demo tool execution
uv run --frozen python -m evaluations.evaluate_platform --live
```

Without `--live`, the evaluator only displays its synthetic scenario catalog.
Live reports include action/value checks, context keywords, tool selection and
unnecessary-tool checks, Arabic output, response word counts, token usage, tool
latency and text/model turn latency. Scenarios cover preloaded visit facts, older
history, different/missing customers, company identity, refusal, disputed visits,
multiple answers, corrections, prompt injection and a complete Bank Nizwa journey.
Natural Omani Arabic, nuanced relevance and conversational conciseness require
human review; automated keyword/word-count checks are not a semantic judge.

Live model tests do not validate real microphone quality, STT accuracy, TTS
pronunciation, actual telephone hangup timing, or WebRTC network latency. Those
need an audio call against the deployment. Offline tests simulate provider I/O.
Results for this implementation are recorded in `PLATFORM_VALIDATION.md`.

## Created and modified files

Created: the `agents/`, `context/`, and `tools/` files shown above (plus package
initializers); `conversation/{composer,rendering}.py`; `questionnaires/municipality-follow-up.ar.json`;
`tests/unit/test_agent_registry.py`; `tests/unit/test_tools.py`;
`tests/unit/test_rendering.py`;
`tests/integration/test_platform.py`; `evaluations/cases/platform_cases.py`;
`evaluations/evaluate_platform.py`; this guide and `PLATFORM_VALIDATION.md`.

Modified: `api/{sessions,signaling,survey_chat,telephony}.py`;
`conversation/{manager,models,prompts}.py`; `voice/{pipeline,survey}.py`;
`providers/{foundry_llm,azure_stt,azure_tts,cartesia_tts,munsit_tts,tts}.py`; `config.py`;
both frontends' HTML/JavaScript/CSS; `tests/browser/survey_chat_browser.mjs`;
`.env.example`; `pyproject.toml`; `README.md`; `docs/ARCHITECTURE.md`.
No Docker, transport implementation or real customer records were modified.
