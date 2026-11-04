# Project architecture

The repository runs one Python application with shared conversation logic and
an operator web dialer, telephone media and a separate text lab. See
[web dialer deployment](WEB_DIALER.md) for the current call flow and setup.

The engine also supports declarative agents: `agents/` loads agent identity,
instructions, permitted tools, language/voice preferences and survey selection;
`context/` preloads session-bound synthetic customer records; `tools/` validates
and executes optional read-only model requests. These all reuse the existing
`ConversationManager`, Foundry client, and Pipecat pipeline. See the
[platform implementation and extension guide](MULTI_AGENT_PLATFORM.md) for the
updated tree, configuration examples, boundaries, testing, and file inventory.

| Folder | Responsibility |
|---|---|
| `src/voice_agent/api/` | FastAPI lifecycle, authenticated dialer/call state, telephony listener, text lab |
| `src/voice_agent/agents/` | Validated agent registry, packaged prompts/configuration, per-call runtime factory |
| `src/voice_agent/context/` | Customer/interaction models, deterministic loader and synthetic repository |
| `src/voice_agent/tools/` | Function schemas, explicit async handlers, registry, permissions/timeouts |
| `src/voice_agent/conversation/` | Survey models, prompt, authoritative state, answer parsing, shared language checks |
| `src/voice_agent/voice/` | Pipecat pipeline assembly and survey/language frame processors |
| `src/voice_agent/providers/` | Azure STT/TTS, Foundry streaming and decision clients, Cartesia TTS, provider selection |
| `src/voice_agent/transports/` | Asterisk media transport and asynchronous AMI control client |
| `src/voice_agent/observability/` | Logging configuration, sanitized diagnostics, latency measurement |
| `frontend/dialer/` | Phone-number page and live transcript; no build or runtime npm dependencies |
| `frontend/survey-chat/` | Text conversation lab assets |
| `questionnaires/` | Editable questionnaire JSON files |
| `evaluations/cases/` | Synthetic scenarios shared by tests and live evaluations |
| `evaluations/results/` | Evaluation reports |
| `scripts/` | Provider verification, Omantel staging and private voice recovery commands |
| `tests/unit/` | Survey decisions and validation |
| `tests/integration/` | API, pipeline, provider adapters, and transport checks |
| `tests/browser/` | Browser regressions and the deterministic text-lab server |
| `docs/` | Setup guides, architecture, and implementation reports |
| `deploy/` | Nginx example, Omantel fragments, AMI/dialplan settings and Asterisk reconstruction reference |

`api/app.py` owns operator authentication, middleware and startup/shutdown.
`api/dialer.py` owns outbound call state and bounded live transcripts.
`transports/ami.py` controls Asterisk on loopback; `api/telephony.py` receives
telephone media and correlates each outbound call. `voice/pipeline.py` assembles
services and transcript observers. Browser WebRTC code and dependencies are removed.

Conversation rules and state live in `conversation/`, so the voice agent and text
lab apply the same decisions. That layer uses no Pipecat frame processors or
provider SDKs. Provider integrations and transport details stay in their own folders.
`conversation/composer.py` layers the existing shared policy, agent instructions,
questionnaire state and untrusted runtime context. Survey progression remains in
the existing manager; there is no second manager or separate agent pipeline.

`paths.py` locates repository assets relative to the source checkout, including
the editable installation used in Docker. `VOICE_AGENT_ROOT` can explicitly select
another asset root. Absolute custom questionnaire paths and existing files relative
to the current directory are supported. The old built-in questionnaire filenames
in existing `.env` files resolve to their new locations. Settings load the repository
`.env`; existing process environment values take precedence.

## Development commands

Run these commands from the repository root:

```bash
uv sync --frozen
uv run --frozen python -m voice_agent
```

The installed `voice-agent` command starts the same service. Application imports
use the `voice_agent` package; the former flat Python module paths have moved.
Docker starts `python -m voice_agent` inside the container automatically; there is
no separate host startup command required when using Docker.

```bash
# Standalone text lab
uv run --frozen python -m voice_agent.api.survey_chat

# Offline tests (provider calls are simulated)
uv run --frozen python -m unittest discover -s tests -t . -v

# Deterministic browser regressions (no Azure or telephone calls)
npm --prefix tests/browser ci --ignore-scripts
node tests/browser/node_modules/playwright/cli.js install chromium --only-shell
npm --prefix tests/browser run test:dialer
npm --prefix tests/browser run test:chat

# Provider diagnostics; see --help for service choices
uv run --frozen python -m scripts.verify_services --help

# Live evaluation: sends synthetic inputs to the configured Azure model
uv run --frozen python -m evaluations.evaluate_survey --live
```

Evaluation defaults write under `evaluations/results/`. Pass `--output` to preserve
a separate report. Browser tests use a local simulated call fixture or the
deterministic text lab; they require no provider credentials.

Docker Compose commands remain at the repository root. The Dockerfile installs
the backend package and copies the static dialer, questionnaires and text lab into
the runtime image. There is no Node build stage. Ubuntu host networking reaches
Asterisk's private AMI and telephone media interfaces.
