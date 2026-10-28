# Self-hosted Azure voice agent

Browser microphone → native Pipecat SmallWebRTC → continuous Azure Speech STT →
Foundry GPT-4.1-mini → streaming Azure or Cartesia TTS → SmallWebRTC → browser speaker.

Speech output defaults to Azure. See [Cartesia setup and switching](CARTESIA_TTS.md)
to select Sonic 3.6 for both browser and Asterisk calls through `TTS_PROVIDER`.

No Daily account, key, hosted room, browser SDK, or Pipecat Cloud runtime is used.

The supplied `.env.example` enables the Arabic customer-satisfaction survey from
شركة تنقيب البيانات, with explicit consent and eight questions. Speech stays in
`DEFAULT_LANGUAGE` for the entire call, using `ar-SA-HamedNeural` for Arabic.
See [customer survey setup and verification](CUSTOMER_SURVEY.md).
`SURVEY_CONVERSATION_MANAGER=false` selects the legacy assistant for comparison.
The [Phase 6 report](PHASE6.md) records the original implementation and benchmark.

To tune replies without making telephone calls, run
`uv run --frozen python survey_chat.py` and open **http://127.0.0.1:7862**.
The [local conversation lab](SURVEY_CHAT.md) uses the same survey decisions and
shows accepted answers, validation, and exportable traces without STT/TTS.

For Linphone/Asterisk telephone calls, see [Asterisk setup](ASTERISK_SETUP.md),
especially step 7 to enable extension `2000`, and the
[adapter implementation report](ASTERISK_REPORT.md). Telephone media is an opt-in
loopback listener (`TELEPHONY_ENABLED=false` by default); Ubuntu Docker host
networking connects it to Asterisk while the browser service keeps its existing path.

Azure provider implementations, credentials, deployment, prompt, voice selection, and
VAD settings were preserved during the transport migration. Pipecat remains 0.0.72.
There was no repository frontend before this change; the previous browser page was
hosted externally. The new page supplies only Start, Stop, mute, status, and errors.

## Native localhost startup

Use Python 3.11/3.12, uv, and Node 22+ with npm. Keep your populated `.env`.
For a new checkout only, copy `.env.example` to `.env` and enter Azure credentials.
The transport does not need an API key. Azure secrets stay on the backend.

```bash
uv sync --frozen
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
uv run --frozen python server.py
```

Open **http://localhost:7860**, click Start conversation, and grant microphone access.
`python bot.py` and `python main.py` also launch the server. Use headphones for barge-in.
A browser on another device needs a reachable HTTPS origin; ordinary HTTP on a LAN IP
cannot request a microphone. Stop releases the microphone, connection, and Azure pipeline.

Ubuntu native packages:

```bash
sudo apt-get update
sudo apt-get install -y libasound2 libssl3 libgomp1 libgl1 libglib2.0-0 ca-certificates
```

Windows native development is supported. PyAV is constrained to 14.2.0 on Windows
because 14.4.0 has no Windows wheel; both versions meet aiortc 1.11's av<15 requirement.
Pipecat remains pinned to 0.0.72. Azure Speech SDK requires 1.48.2 or later to
address Linux certificate revocation compatibility failures; this project uses 1.48.x.
Speech is installed directly because Pipecat 0.0.72's optional `azure` extra pins 1.42.x.

## Docker

Build and serve HTTP on the local host:

```bash
docker compose up --build
# Open http://localhost:7860
```

The default Compose file publishes **only HTTP** to 127.0.0.1:7860. Bridge-networked
Docker, especially Docker Desktop, also needs a reachable media path (usually your
self-hosted TURN server). HTTP port publication alone does not make container ICE
candidates reachable. For the simplest localhost audio test on Windows/macOS, use the
native command above. Do not interpret a successful page load as media connectivity.

On Ubuntu/Linux, use the standalone host-network configuration:

```bash
docker compose -f docker-compose.ubuntu.yml up --build
# Stop this configuration:
docker compose -f docker-compose.ubuntu.yml down
```

Host networking makes the native ICE UDP sockets reachable on the host interfaces;
it does not publish HTTP publicly: the Ubuntu configuration binds HTTP to loopback
for Nginx. Do not combine this file with the bridge configuration. Use one worker:
connection ownership is in memory, and multiple workers need session-aware routing.
Image builds compile the locked frontend and install the frozen Python lock. The
runtime is non-root, with an init process and a 35-second shutdown grace period.

## Signaling and sessions

The browser creates a session with `POST /api/sessions`. The backend issues an opaque
session ID and an HttpOnly, SameSite=Strict cookie scoped to that session's API path.
HTTPS uses Secure cookies. No Azure credentials are returned. TURN credentials, when
configured, are short-lived and intentionally supplied to the browser for relay access.

The compatible client sends an SDP offer to `/api/sessions/{id}/offer`. Pipecat's
native connection initializes it and returns `{sdp, type, pc_id}`. Both endpoints wait
for ICE gathering: candidates travel **inside SDP**, with no separate trickle-candidate
endpoint. Renegotiation calls the installed native `renegotiate` method. Retried offers
and renegotiation reuse the existing connection and pipeline. A mismatched peer ID is
rejected; a fresh Start creates a fresh isolated conversation context.

The default cap is four sessions and 15 minutes per session. Unconnected sessions
expire after 30 seconds. Closed/failed peers and completed pipelines are removed;
missing heartbeat/connectivity is reaped after about 10 seconds. A two-second sweeper
also removes abandoned tabs. Stop is idempotent. Uvicorn owns process signals and
closes every session on shutdown; individual Pipecat runners do not install signal
handlers. Signaling has JSON size/schema checks, exact-origin validation, and
sanitized errors. This is an experimental app: protect public access using Nginx
authentication (example supplied) or your existing application login. CORS alone is
not authentication and does not block direct HTTP clients from consuming session slots.

All frontend assets are served by the backend under the same origin. Set
`FRONTEND_ORIGINS` to the exact public origin, including scheme/port and no trailing
slash. Cross-origin hosting is not required or the supported default for this client.

## Browser SDK compatibility

The matching browser client is `@pipecat-ai/client-js@0.4.0`; its exported class is
`RTVIClient`, locally aliased to PipecatClient. The native backend uses RTVI 0.3.

The official SmallWebRTC browser package, including newer releases checked during
implementation, transitively depends on a Daily device-capture helper. To remove that
SDK completely while retaining the pinned Pipecat API, this repository explicitly
vendors an **audio-only adaptation of SmallWebRTC 0.4.0**. This is a local package,
not an unmodified npm release. It keeps the upstream SDP, data-channel, heartbeat,
track-status and renegotiation protocol, replacing device capture with getUserMedia.
See `frontend/vendor/small-webrtc-transport/README.md` for source, license, and changes.
The dependency tree contains no `@daily-co/daily-js`; browser assets make no external
transport SDK requests. Camera, screen sharing, and device-switching UI are omitted.

## Ubuntu HTTPS, ICE and firewall review

`deploy/nginx.conf.example` and `deploy/turnserver.conf.example` are reviewable examples.
No production Nginx, firewall, certificates, or deployment settings were changed.
There is no existing domain/Nginx configuration in this repository to safely infer.
Use the intended domain, certificate paths, and existing application's routing.

Nginx proxies HTTPS signaling and frontend requests to 127.0.0.1:7860. It does **not**
proxy WebRTC audio. Pipecat's native connection exposes no public-IP override or
fixed UDP-range constructor in 0.0.72; the app does not invent such settings or rewrite
SDP addresses. Host candidates come from the host interfaces. For a host behind NAT,
configure your self-hosted STUN/TURN server so ICE can gather reflexive/relay addresses.

| Port / range | Purpose |
|---|---|
| TCP 7860, loopback | Backend frontend/signaling; Nginx upstream |
| TCP 443 | Public HTTPS in your existing Nginx configuration |
| OS ephemeral UDP range on the bot host | Direct WebRTC DTLS/SRTP/SCTP sockets |
| UDP/TCP 3478 on coturn host | TURN/STUN listener |
| TCP 5349 on coturn host | TURN over TLS listener |
| UDP 49160–49200 on coturn host | Example coturn relay allocation range |
| Outbound TCP 443 | Azure HTTPS/WebSockets and model streaming |

On Linux, inspect the actual bot-host ephemeral range with
`cat /proc/sys/net/ipv4/ip_local_port_range`; a common default is 32768–60999, but the
host value determines the necessary direct-media firewall rules. These sockets use
kernel-selected ports via aiortc/aioice. Approve any host/cloud security group/router
changes before applying them. No `sysctl` or firewall rule is applied by this repo.

For restrictive NAT/firewalls, self-host coturn with a public IP and a valid TLS
certificate. Configure `external-ip=PUBLIC_IP/PRIVATE_IP` if it is behind 1:1 NAT and
forward the listener and relay ranges. The example uses shared-secret authentication,
quotas, and a bounded UDP relay range. Keep its real configuration and shared secret
out of Git. For clients that only allow TCP 443, TURN TLS needs a separate public IP
or separate routing from Nginx's HTTPS listener, with a listener on 443; sharing the
same address/port with the normal Nginx HTTP proxy will not work automatically.

Backend `.env` example for your own coturn host:

```dotenv
FRONTEND_ORIGINS=https://voice.example.com
ICE_SERVERS=[{"urls":"stun:voice.example.com:3478"}]
TURN_URLS=turn:voice.example.com:3478?transport=udp,turns:voice.example.com:5349?transport=tcp
TURN_SECRET=the_same_long_random_secret_as_coturn_static_auth_secret
```

Only the generated HMAC credentials expire after one hour; `TURN_SECRET` stays on the
backend. There is no default public STUN server or new paid transport service.
Whether TURN is necessary depends on the deployed client/server NAT/firewall paths;
it cannot be determined from localhost. Inspect Chrome/Edge `webrtc-internals` for the
selected candidate pair and inbound/outbound RTP packet counts. Confirm `relay`
candidates in an actual restrictive-network test. HTTPS success is insufficient.

References: [pinned native SmallWebRTC source](https://github.com/pipecat-ai/pipecat/blob/v0.0.72/src/pipecat/transports/network/webrtc_connection.py),
[official SmallWebRTC client](https://github.com/pipecat-ai/pipecat-client-web-transports),
[coturn configuration](https://github.com/coturn/coturn/blob/master/examples/etc/turnserver.conf),
[Docker Desktop networking](https://docs.docker.com/desktop/features/networking/).

## Azure and transport verification

Independent Azure checks still require no browser or transport:

```bash
uv run --frozen python verify_services.py llm
uv run --frozen python verify_services.py tts
uv run --frozen python verify_services.py speech-roundtrip
uv run --frozen python verify_services.py stt --wav /path/to/test-16k-mono.wav
```

If the server connects WebRTC but repeatedly logs Azure STT cancellations, run
the checks **inside the deployed container** to use its credentials and network:

```bash
docker compose -f docker-compose.ubuntu.yml exec agent python verify_services.py tts
docker compose -f docker-compose.ubuntu.yml exec agent python verify_services.py speech-roundtrip
```

The roundtrip synthesizes a test phrase before checking recognition, so resolve any
TTS failure first. Backend logs and checks include Azure cancellation reason, error
code, and error details, with configured credentials and endpoint URLs redacted.
`recognizer started` means the SDK started locally; it does not confirm Azure accepted
the connection. For `AuthenticationFailure`, verify the Speech key and region belong
to the same Azure resource. For connection failures, check container DNS, outbound
HTTPS/WebSockets, proxy settings, and Azure resource network restrictions. Quota and
request errors require the corresponding Azure resource or voice/language correction.

On Linux, `WS_OPEN_ERROR_UNDERLYING_IO_OPEN_FAILED` can also indicate the
[Speech SDK CRL compatibility issue](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/migrate-to-sdk-1-48-2)
in versions before 1.48.2. Rebuild the image from the updated lockfile and confirm
the running SDK version with `python -c "import azure.cognitiveservices.speech as s; print(s.__version__)"`
inside the container, then rerun the checks above.

Backend regressions:

```bash
uv run --frozen python -m unittest discover -s tests -v
```

Automated browser smoke test against an already-running server:

```bash
# Set BROWSER_PATH to your Chrome/Chromium/Edge executable if needed.
# Optional TEST_WAV: path to non-sensitive synthetic/test PCM WAV input.
npm --prefix frontend run test:browser
```

The included runner defaults to installed Edge on Windows. It requests a fake device,
then injects synthetic WebAudio input; it checks real browser WebRTC negotiation,
nonzero decoded return-audio energy, mute/unmute, two sessions, Stop and reconnect.
It does not establish physical microphone quality or actual speaker audibility.
See `TEST_RESULTS.md` for actual runs, limitations and latency samples. Manually test
Arabic, English, repeated turns, silence, interruption during speech, mute/unmute,
tab close, network loss and remote HTTPS on the actual target network.

Recognition remains fixed at the existing `AZURE_STT_LANGUAGE` (ar-OM default). TTS
uses the voice for the configured `DEFAULT_LANGUAGE` throughout the session; customer
speech never changes the output language. Arabic defaults to `ar-SA-HamedNeural`.
The SSML locale follows the configured voice, independently of the STT locale. For reliable
English STT, run a test process with `AZURE_STT_LANGUAGE=en-US`; automatic bilingual
recognition was not added because provider/language settings were explicitly preserved.

## Latency

Existing monotonic per-turn STT, LLM and TTS instrumentation is preserved. Browser
connection readiness is measured from Start through microphone permission, ICE and
RTVI ready; backend connection time starts at session allocation. These definitions
can include permission delay and should not be compared as identical metrics.

| Metric | Definition |
|---|---|
| `stt_first_partial` | VAD speech-start frame to first nonempty recognizing callback |
| `stt_final_after_vad_end` | VAD speech-stop frame to recognized callback |
| `llm_ttft` / `llm_total` | Context entering LLM to first token / response-end frame |
| `tts_first_audio` | First sentence synthesis start to first PCM chunk |
| `speech_end_to_transport_audio` | VAD speech-stop to first PCM frame offered to output |

The last metric excludes WebRTC delivery, jitter buffers, decoding, and physical
speaker playback. Browser tests establish decoded nonzero audio, not end-of-speech
to actual audibility. That measurement still needs a synchronized client playback
acknowledgement or acoustic loopback. Failed/interrupted turns can omit metrics;
rapid utterances can merge under existing turn aggregation. No comparable earlier
Daily end-to-end baseline was captured, so no transport speedup is claimed.
