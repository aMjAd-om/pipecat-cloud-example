# Asterisk adapter implementation report

## Scope

Added an optional Asterisk `chan_websocket` media entry point for the existing Pipecat 0.0.72 application. It targets the Asterisk 22.11.0 configuration in [the setup guide](ASTERISK_SETUP.md), including extension `2000`, JSON control messages, and `slin16` mono audio at 16 kHz.

The user's Linphone `1001` call already reached extension `6000` and executed Asterisk's echo application. That establishes dialplan reachability; hearing echo is the separate bidirectional audio check.

## Changes

- `src/voice_agent/transports/asterisk.py`: mixed binary/audio and JSON/control handling; strict startup format validation; bounded input/output queues; stateful 24-to-16 kHz resampling; 20 ms PCM framing with padded tails; playback pacing and flow control; interruption flushing with stale-writer invalidation; drain acknowledgement for normal pipeline endings.
- `src/voice_agent/api/telephony.py`: loopback-only listener, WebSocket `media` subprotocol, five-second handshake timeout, duplicate channel rejection, isolated peers, configurable call count/duration, and graceful listener shutdown. Outbound calls may share a configured connection name. It rejects browser-origin requests and is not added to the public browser application routes.
- `src/voice_agent/voice/pipeline.py`: shared pipeline accepts the telephone transport; starts its greeting without browser RTVI; preserves the survey feature flag, language lock, Azure services, and model. Cancellation waits for pipeline cleanup before closing resources.
- `src/voice_agent/api/app.py`, `Dockerfile`, `.env.example`, `src/voice_agent/config.py`: opt-in listener lifecycle, container packaging, documented environment settings, and feature-flag validation.
- `tests/integration/test_telephony.py`: protocol and integration regressions using simulated Azure services.

The working SmallWebRTC, STT, and TTS provider implementations, questionnaire, dependency versions, ICE lifecycle guard, and TURN configuration are retained. No Ubuntu deployment was performed. The local private `.env` was not changed to enable telephone access.

## Validation

Run the offline suite with:

```bash
uv run --frozen python -m unittest discover -s tests -t . -v
```

After the outbound connection-name correction and audio diagnostics, the suite contains 65 tests: 64 passed and one Linux-specific signal test was skipped on Windows. Thirteen telephone tests cover:

- Strict media startup validation and rejection before any Azure request.
- Both outbound client-name IDs (`pipecat`) and incoming UUID IDs, matching Asterisk 22.11.0's source. Simultaneous outbound calls use the same connection name and distinct channel IDs.
- Duplicate call IDs, call count limits, isolated simultaneous peers, and call-duration expiry.
- Private-origin/subprotocol checks and malformed audio cleanup.
- Real Pipecat pipeline startup, automatic Arabic survey greeting, two sequential answers advancing consent and overall rating, then a fresh call greeting again.
- Resampling 24 kHz synthesis into 640-byte Asterisk PCM packets, preserving state across chunks and padding the final frame.
- Queued old output interrupted using Pipecat's interruption frame: `FLUSH_MEDIA` precedes subsequent new speech and old speech does not resume.
- A writer waiting for flow control cannot send stale audio after a flush.
- Playback pacing and playback-drain acknowledgement.
- Actual localhost Uvicorn HTTP/WebSocket transport with mixed text and binary messages, shutdown of an active call, port release, and unchanged process signal handler.
- A conflicting listener port fails startup explicitly.
- Audio diagnostics retain only frame counters and level summaries, clear window levels between reports, and retain no PCM buffers.

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

## First live handshake failure and correction

The first deployed call reached the media WebSocket but was rejected before pipeline startup. Investigation found an incorrect UUID-only assumption in our adapter: for outbound `Dial(WebSocket/pipecat/...)`, Asterisk 22.11.0 sets `connection_id` to the configured name `pipecat`. Only incoming-mode channels generate a UUID at that step. This is verified in upstream [`websocket_new`](https://github.com/asterisk/asterisk/blob/22.11.0/channels/chan_websocket.c), which also supplies that ID to `MEDIA_START`.

The adapter now accepts bounded connection names as well as UUIDs, and keys active calls by the unique `channel_id`. Duplicate connection names alone do not reject simultaneous outbound calls. The fixtures now default to the deployed outbound form, rather than generating UUIDs for every call.

Handshake errors now log a static reason code and stage; other failures log stage and exception class without exception text, raw control messages, channel variables, or customer speech. The deployed log alone did not expose the precise exception, so the UUID mismatch is a verified code defect consistent with that failure; the next real call must confirm the fix on Ubuntu.

## Incoming audio investigation

After the handshake correction, the user confirmed the AI greeting plays, but caller speech does not interrupt it, and extension `6000` also returns no audible echo. The provided log shows a receive failure exactly 30 seconds after STT startup with no recognition events. This is consistent with the adapter's receive timeout and a missing incoming-media path, rather than evidence of an LLM or survey issue. It does not yet identify the exact phone/network fault.

Added a first-PCM receipt marker, exception-class diagnostics for receive failures, and opt-in five-second audio counters/levels via `TELEPHONY_AUDIO_DIAGNOSTICS`. The next deployment checks are phone microphone access and active-call Asterisk RTP reception, actual RTP port range, and LAN firewall rules. Speech-detection thresholds and Azure provider implementations are unchanged; the incoming-audio issue remains unresolved pending those checks.
