# Project architecture

The repository runs one Python application with shared conversation logic and
separate browser, telephone, and text entry points.

| Folder | Responsibility |
|---|---|
| `src/voice_agent/api/` | FastAPI lifecycle, browser signaling and sessions, telephony listener, text lab |
| `src/voice_agent/conversation/` | Survey models, prompt, authoritative state, answer parsing, shared language checks |
| `src/voice_agent/voice/` | Pipecat pipeline assembly and survey/language frame processors |
| `src/voice_agent/providers/` | Azure STT/TTS, Foundry streaming and decision clients, Cartesia TTS, provider selection |
| `src/voice_agent/transports/` | Asterisk media transport and WebRTC ICE lifecycle guard |
| `src/voice_agent/observability/` | Logging configuration, sanitized diagnostics, latency measurement |
| `frontend/voice/` | Voice browser application, build tools, vendored transport |
| `frontend/survey-chat/` | Text conversation lab assets |
| `questionnaires/` | Editable questionnaire JSON files |
| `evaluations/cases/` | Synthetic scenarios shared by tests and live evaluations |
| `evaluations/results/` | Evaluation reports |
| `scripts/` | Provider verification commands |
| `tests/unit/` | Survey decisions and validation |
| `tests/integration/` | API, pipeline, provider adapters, and transport checks |
| `tests/browser/` | Browser regressions and the deterministic text-lab server |
| `docs/` | Setup guides, architecture, and implementation reports |
| `deploy/` | Nginx and TURN configuration examples |

`api/app.py` owns middleware and startup/shutdown. `api/signaling.py` owns
browser endpoints and request schemas; `api/sessions.py` owns active WebRTC
connections. `voice/pipeline.py` assembles services and processors.

Conversation rules and state live in `conversation/`, so the voice agent and text
lab apply the same decisions. That layer uses no Pipecat frame processors or
provider SDKs. Provider integrations and transport details stay in their own folders.

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
npm --prefix frontend/voice ci --ignore-scripts
npm --prefix frontend/voice run build
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

# Deterministic browser regression (no Azure calls)
node tests/browser/survey_chat_browser.mjs

# Provider diagnostics; see --help for service choices
uv run --frozen python -m scripts.verify_services --help

# Live evaluation: sends synthetic inputs to the configured Azure model
uv run --frozen python -m evaluations.evaluate_survey --live
```

Evaluation defaults write under `evaluations/results/`. Pass `--output` to preserve
a separate report. Browser voice checks use `tests/browser/browser.mjs` against an
already running server and require configured voice providers.

Docker Compose commands remain at the repository root. The Dockerfile builds
`frontend/voice/`, installs the backend package, and copies questionnaire and text
lab assets into the runtime image.
