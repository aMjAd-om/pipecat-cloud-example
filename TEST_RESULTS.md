# Verification results — 2026-10-27

Execution host: Windows; runtime checks executed in Docker Linux/amd64 with Python
3.11.18, Pipecat 0.0.72, Azure Speech SDK 1.42.0, OpenAI SDK 1.70.0, Daily 0.19.3.

| Check | Result |
|---|---|
| Linux Docker build using frozen lock | PASS |
| Compose configuration validation | PASS |
| Python syntax and application import | PASS |
| `uv lock --check` | PASS |
| `git diff --check` | PASS |
| `.env` excluded from Git and Docker context | PASS |
| Complete Daily/Azure pipeline construction with pinned runtime APIs | PASS, no live call |
| Partial suppression, silence, final result-ID deduplication, repeated phrases and stale-session callbacks | PASS, SDK result mocks |
| TTS audio before completion, Arabic/English voice selection, escaped SSML, cancellation and stale callbacks | PASS, threaded SDK mock |
| STT failure exhausts three bounded retries and emits fatal error | PASS, SDK mocks |
| LLM failure emits a sanitized recoverable error | PASS, SDK mock |
| TTS cancellation/failure emits error and stops synthesis | PASS, SDK mock |
| SIGTERM traverses native Pipecat runner and closes recognition/stream/synthesis | PASS, live Linux signal with mocked SDK connections |
| Independent Foundry completion | PASS: live streaming Arabic completion, 117 characters, first token 1686.2 ms, total 1737.1 ms |
| Independent Arabic and English TTS | PASS: live PCM audio, Arabic 88800 bytes / first audio 633.0 ms; English 106800 bytes / first audio 262.3 ms |
| Independent streaming STT | PASS: temporary synthetic Arabic fixture streamed as 16 kHz PCM; seven partial events and one final; first partial 876.2 ms, final 91.3 ms after fixture end |
| Foundry hostname/resource/deployment match | Configured endpoint/key/deployment accepted a live completion; portal configuration not independently inspected |
| Seven requested Arabic/English spoken phrases | NOT RUN: human microphone participant required |
| End-to-end silence, interruptions, rapid consecutive turns, connection failures | NOT RUN against live services; relevant offline adapter paths pass |
| Actual browser audible latency | NOT MEASURED: requires client playback acknowledgement/loopback |

The offline suite ran **7 tests, all passing**. No production deployment was performed.

Live checks above used the user's populated credentials and no Daily rooms or Daily
API calls. Measurements are individual smoke-test samples, not latency benchmarks.
STT used synthesized audio, not human microphone input; its file-end timestamp is not
VAD end. No full conversational or browser-audibility latency is available.

A live TTS check exposed a per-request timestamp callback isolation bug and one native
SDK process-exit failure after successful synthesis. The checker now isolates callback
state, explicitly disconnects synthesis callbacks, and releases native objects before
event-loop shutdown; the subsequent live check passed with process exit code 0. The
production TTS adapter also disconnects callbacks when stopping synthesis.

Instrumentation remains available for final STT relative to VAD end, LLM first
token/total, TTS first PCM, and VAD end to transport handoff. See README for definitions.

Known limits: default recognition is ar-OM; restart with en-US for English recognition
tests. TTS voice selection is per sentence by output script. Audio during STT reconnect
is dropped. Rapid utterances can merge under native turn aggregation; latency state
assumes one active turn. Daily remains a cloud transport. Synthesizers are created per
sentence to isolate cancellation, which can add connection latency. The server handoff
metric excludes WebRTC delivery and actual device playback.
