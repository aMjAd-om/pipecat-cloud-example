# Telephone voice agent

Enter a customer's phone number in the web call desk, press **Call customer**, and
follow the agent/customer conversation in the live transcript. Asterisk places
the call through Omantel and connects the configured speech agent after answer.
The page also shows call status and duration, ends calls, and downloads transcripts.

See **[web dialer deployment and operation](docs/WEB_DIALER.md)** for the existing
Ubuntu server. It includes the private Asterisk Manager Interface (AMI) setup,
operator login, deployment commands, first-call checks and rollback.

The old `frontend/voice` microphone client, vendored browser SDK, WebRTC signaling,
ICE/TURN helpers and Node frontend build have been removed. The new page in
`frontend/dialer` uses plain HTML, CSS and JavaScript and needs no npm build.
The Python dependency lock no longer includes aiortc, PyAV or their WebRTC stack.

## Run

Keep your existing private `.env`. For a new checkout, copy `.env.example` and set
the Azure credentials. Configure `DIALER_PASSWORD` (at least 16 random characters)
and the AMI credentials as described in the deployment guide. The browser uses
an operator login; use HTTPS or an SSH tunnel. Outbound calls are disabled until
`OUTBOUND_ENABLED=true` and the PBX setup is installed.

```bash
uv sync --frozen
uv run --frozen python -m voice_agent
# Open http://127.0.0.1:7860
```

Ubuntu Docker, with Asterisk running on the same host:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
```

Use one application worker. Ubuntu host networking reaches Asterisk's private
`127.0.0.1:5038` AMI interface and the agent's `127.0.0.1:7861` media listener.
The default bridge Compose file can preview the page but cannot reach host
loopback Asterisk interfaces. HTTP remains on loopback for a proxy or SSH tunnel.
The agent Compose service has `restart: "no"`; start it explicitly after reboot.

## Calls and transcripts

The call desk accepts eight-digit Oman numbers, optionally with `+968` or `00968`.
Calls use `21130800` as the caller ID and the primary Omantel trunk. Our own
`211308XX` block is rejected to prevent calling back into this PBX.
`OUTBOUND_ALLOWED_NUMBERS` can restrict destinations for the first test; blank
permits any validated destination. Automatic trunk failover remains disabled.

`POST /api/calls` places a call, `GET /api/calls/{id}` supplies live status and
transcript messages, and `DELETE /api/calls/{id}` hangs up. Requests are
idempotent per browser-generated request ID. Authentication, origin validation,
number validation, duplicate destination checks and telephone capacity limits
apply before a call is originated. No Docker socket is mounted in the agent.

Customer messages are final Azure STT results, including pickup greetings.
Agent messages are synthesized text passing through the output pipeline, rather
than private prompts or model reasoning. An interruption can cut a sentence short;
the transcript is not a word-perfect proof of audio heard. Transcripts stay in
memory, expire after an hour and disappear on restart. Download them from the page
if needed; no transcript or audio is written to application logs.

## Agent and recovery references

The existing Pipecat 0.0.72 telephone media adapter, Azure STT, Foundry LLM,
configurable surveys, interruption handling and Azure/Cartesia/Munsit TTS remain.
The configured agent comes from `TELEPHONY_AGENT_ID`, `AGENT_ID` or the existing
questionnaire settings. Provider keys and AMI secrets stay on the server.

- [Web dialer setup](docs/WEB_DIALER.md)
- [Complete server backup and recovery](docs/VOICE_SERVER_RECOVERY.md)
- [Asterisk reconstruction and dialplan](deploy/asterisk/README.md)
- [Omantel trunk, network and rollback record](docs/OMANTEL_SIP_TRUNK.md)
- [Configurable agent platform](docs/MULTI_AGENT_PLATFORM.md)
- [Cartesia TTS](docs/CARTESIA_TTS.md), [Munsit TTS](docs/MUNSIT_TTS.md)
- [Customer survey](docs/CUSTOMER_SURVEY.md)

The optional text-only survey lab remains separate from the call desk:

```bash
uv run --frozen python -m voice_agent.api.survey_chat
# http://127.0.0.1:7862
```

Earlier browser/ICE deployment reports describe historical implementations and
are not instructions for the current telephone-only application.

## Offline verification

```bash
uv run --frozen python -m unittest discover -s tests/unit -v
uv run --frozen python -m unittest tests.integration.test_dialer -v
npm --prefix tests/browser ci --ignore-scripts
node tests/browser/node_modules/playwright/cli.js install chromium --only-shell
npm --prefix tests/browser run test:dialer
```

Playwright is only a test dependency, outside the application frontend. The dialer
browser test uses a simulated PBX and never places a real call or uses Azure.
