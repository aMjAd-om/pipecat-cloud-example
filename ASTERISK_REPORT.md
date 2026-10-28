# Asterisk adapter implementation report

## Scope

Added an optional Asterisk `chan_websocket` media entry point for the existing Pipecat 0.0.72 application. It targets the Asterisk 22.11.0 configuration in [the setup guide](ASTERISK_SETUP.md), including extension `2000`, JSON control messages, and `slin16` mono audio at 16 kHz.

The user's Linphone `1001` call already reached extension `6000` and executed Asterisk's echo application. That establishes dialplan reachability; hearing echo is the separate bidirectional audio check.

## Changes

- `asterisk_transport.py`: mixed binary/audio and JSON/control handling; strict startup format validation; bounded input/output queues; stateful 24-to-16 kHz resampling; 20 ms PCM framing with padded tails; playback pacing and flow control; interruption flushing with stale-writer invalidation; drain acknowledgement for normal pipeline endings.
- `telephony_server.py`: loopback-only listener, WebSocket `media` subprotocol, five-second handshake timeout, duplicate channel/connection rejection, isolated peers, configurable call count/duration, and graceful listener shutdown. It rejects browser-origin requests and is not added to the public browser application routes.
- `bot.py`: shared pipeline accepts the telephone transport; starts its greeting without browser RTVI; preserves the survey feature flag, language lock, Azure services, and model. Cancellation waits for pipeline cleanup before closing resources.
- `server.py`, `Dockerfile`, `.env.example`, `config.py`: opt-in listener lifecycle, container packaging, documented environment settings, and feature-flag validation.
- `tests/test_telephony.py`: protocol and integration regressions using simulated Azure services.

The working SmallWebRTC, STT, and TTS provider implementations, questionnaire, dependency versions, ICE lifecycle guard, and TURN configuration are retained. No Ubuntu deployment was performed. The local private `.env` was not changed to enable telephone access.

## Validation

Run the offline suite with:

```bash
uv run --frozen python -m unittest discover -s tests -v
```

The final suite contains 63 tests: 62 passed and one Linux-specific signal test was skipped on Windows. Eleven telephone tests cover:

- Strict media startup validation and rejection before any Azure request.
- Duplicate call IDs, call count limits, isolated simultaneous peers, and call-duration expiry.
- Private-origin/subprotocol checks and malformed audio cleanup.
- Real Pipecat pipeline startup, automatic Arabic survey greeting, two sequential answers advancing consent and overall rating, then a fresh call greeting again.
- Resampling 24 kHz synthesis into 640-byte Asterisk PCM packets, preserving state across chunks and padding the final frame.
- Queued old output interrupted using Pipecat's interruption frame: `FLUSH_MEDIA` precedes subsequent new speech and old speech does not resume.
- A writer waiting for flow control cannot send stale audio after a flush.
- Playback pacing and playback-drain acknowledgement.
- Actual localhost Uvicorn HTTP/WebSocket transport with mixed text and binary messages, shutdown of an active call, port release, and unchanged process signal handler.
- A conflicting listener port fails startup explicitly.

The existing survey, browser session, Azure adapter, and closed-ICE-transport regressions also pass. The telephone implementation introduces no extra model request per finalized customer transcript. Tests use simulated Azure responses and make no billable Azure calls; they do not measure actual model token usage or real telephone response latency.

## Remaining verification and limits

1. Build the updated agent on Ubuntu, enable `TELEPHONY_ENABLED=true`, verify `http://127.0.0.1:7861/health`, and dial `2000` from the already registered Linphone account. This actual PBX-to-Azure call has not been run in this workspace.
2. Confirm microphone recognition, the Hamed voice, multiple live turns, and audible interruption behavior over G.711. The automated interruption test injects Pipecat's interruption event; it does not establish VAD quality on actual telephone speech or handset echo cancellation.
3. Measure live speech-end-to-audio and interruption-to-silence latency. The adapter paces audio in 20 ms units, but no end-to-end latency number is claimed.
4. Test hangup while Azure is recognizing, inferring, and synthesizing on the deployed server, and confirm the call count returns to zero and repeat calls succeed. Offline service cleanup tests do not prove remote Azure/network behavior.
5. The pilot expects both containers to use Linux host networking on the same Ubuntu machine. It does not enable mobile-data access, public SIP ingress, a carrier trunk, recording, or outbound dialing.
6. The listener is private and trusts local Asterisk. It has no credential authentication for other local processes; keep it off public Nginx/SSH forwarding routes. Per-call media is not persisted by the adapter. External Azure processing and any PBX logging retain their existing policies.
7. The pilot waits for caller hangup after the survey closing, with a configurable maximum call duration. Automatic hangup immediately after the closing is not implemented.
8. The adapter deliberately uses some pinned Pipecat base-transport internals for queue limits, resampling, and interrupted buffer cleanup. A Pipecat upgrade requires rerunning these integration tests. Python remains restricted to 3.11/3.12; the stateful PCM resampler uses `audioop`.

No known failing automated regression remains. Live telephone quality and Ubuntu deployment remain the acceptance gate.
