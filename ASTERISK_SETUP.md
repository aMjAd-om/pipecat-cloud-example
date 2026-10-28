# Asterisk telephone pilot on local Ubuntu

The application now includes an optional Asterisk WebSocket audio adapter alongside the existing SmallWebRTC browser connection. Start with extension `6000` to test telephone audio, then enable the adapter using step 7 and dial `2000` for the Arabic survey. The implementation is tested offline; an actual Linphone-to-Azure AI call still needs validation on your Ubuntu server.

The telephone listener defaults to disabled. Nothing is automatically deployed, and existing Azure integrations and TURN settings are retained.

## 1. Use this topology

```text
Existing browser path:
Browser -> public VM / Nginx -> reverse SSH tunnel -> local Ubuntu / agent
Browser WebRTC media -> existing ICE / coturn configuration

New telephone pilot:
Linphone on Android
  -> same local Wi-Fi, or VPN to Ubuntu when using mobile data
  -> Asterisk container on local Ubuntu (SIP + RTP)
  -> private WebSocket audio connection on localhost
  -> Pipecat survey -> Azure STT -> GPT-4.1-mini -> Azure TTS
```

Keep `voice.dm-mn.com`, `turn.voice.dm-mn.com`, the VM coturn service, the working `TURN_URLS`, and the SSH tunnel as they are. Do not reinstall coturn locally for this pilot.

Ordinary SSH port forwarding carries TCP connections; it does not forward the UDP RTP media path used in this configuration. Pointing a new SIP hostname at the VM will not make the local PBX reachable. The existing HTTP Nginx configuration also does not provide that path.

First test with the phone on Ubuntu's local network. For mobile data, put Android and Ubuntu on the same VPN. Tailscale is one option: follow its [Ubuntu and Android setup instructions](https://tailscale.com/docs/how-to/quickstart), connect both devices to the same tailnet, and permit the phone to reach the PBX. Use Ubuntu's Tailscale IPv4 address as the SIP server. This does not require a VM SIP proxy or public SIP port forwarding. Review the VPN service's plan terms for your business use; a self-managed WireGuard VPN is another option.

The extension calls do not require a paid SIP trunk. Azure STT, LLM, and TTS usage still incurs your normal Azure charges.

## 2. Asterisk version and Docker image

Use a patched Asterisk 22 LTS release with `chan_websocket`. The official release directory currently lists stable **22.11.0**. Recheck the [official releases](https://downloads.asterisk.org/pub/telephony/asterisk/) when building later. Do not select a release candidate just because its number is higher.

The WebSocket driver supports direct dialplan calls and handles audio timing. It is available starting with 22.6.0; the JSON control format used below requires 22.8.0 or later. See the [official driver documentation](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/).

On **local Ubuntu**, create a separate directory outside the application checkout:

```bash
mkdir -p ~/voice-pbx/config
cd ~/voice-pbx
```

Create `Dockerfile` with this reference build. It builds upstream source instead of assuming a particular third-party Docker image has the required modules. This recipe has not been built in this Windows workspace; validate it on Ubuntu before using it for calls.

```dockerfile
FROM debian:bookworm-slim
ARG ASTERISK_VERSION=22.11.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config ca-certificates wget bzip2 patch python3 \
    libedit-dev libjansson-dev libxml2-dev libsqlite3-dev uuid-dev \
    libssl-dev libncurses-dev libcurl4-openssl-dev libsrtp2-dev \
    libspeexdsp-dev libunbound-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src
RUN wget -q https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz \
    && wget -q https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.sha256 \
    && sha256sum -c asterisk-${ASTERISK_VERSION}.sha256 \
    && tar -xzf asterisk-${ASTERISK_VERSION}.tar.gz

WORKDIR /usr/src/asterisk-${ASTERISK_VERSION}
RUN ./configure --prefix=/usr --libdir=/usr/lib \
       --sysconfdir=/etc --localstatedir=/var --with-pjproject-bundled \
    && make menuselect.makeopts \
    && menuselect/menuselect --disable BUILD_NATIVE \
       --enable chan_websocket --enable res_websocket_client \
       --enable res_http_websocket --enable app_echo menuselect.makeopts \
    && make -j2 \
    && make install \
    && make samples \
    && test -f /usr/lib/asterisk/modules/chan_websocket.so \
    && groupadd --system asterisk \
    && useradd --system --gid asterisk --home-dir /var/lib/asterisk asterisk \
    && mkdir -p /var/run/asterisk /var/log/asterisk /var/spool/asterisk \
    && chown -R asterisk:asterisk /var/lib/asterisk /var/run/asterisk \
       /var/log/asterisk /var/spool/asterisk

USER asterisk
CMD ["asterisk", "-f", "-vvv"]
```

This keeps build tools in the pilot image for simplicity. A production image should separate build and runtime stages. The build follows Asterisk's [source installation procedure](https://docs.asterisk.org/Getting-Started/Installing-Asterisk/Installing-Asterisk-From-Source/Building-and-Installing-Asterisk/).

Create `compose.yml`:

```yaml
services:
  asterisk:
    build:
      context: .
      args:
        ASTERISK_VERSION: "22.11.0"
    network_mode: host
    init: true
    restart: unless-stopped
    stop_grace_period: 20s
    volumes:
      - ./config/pjsip.conf:/etc/asterisk/pjsip.conf:ro
      - ./config/extensions.conf:/etc/asterisk/extensions.conf:ro
      - ./config/rtp.conf:/etc/asterisk/rtp.conf:ro
      - ./config/websocket_client.conf:/etc/asterisk/websocket_client.conf:ro
      - ./config/logger.conf:/etc/asterisk/logger.conf:ro
```

Host networking here is for **Linux Docker on Ubuntu**. It lets Asterisk use the host's LAN/VPN interfaces and reach a future localhost media listener without Docker RTP address translation. It does not open the Ubuntu firewall. No `ports:` section is needed.

## 3. Configure extension 1001

Generate a unique SIP password:

```bash
openssl rand -hex 24
```

Put it in `config/pjsip.conf` in place of `REPLACE_WITH_GENERATED_PASSWORD`:

```ini
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

[1001]
type=endpoint
transport=transport-udp
context=from-linphone
disallow=all
allow=alaw,ulaw
auth=auth1001
aors=1001
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733

[auth1001]
type=auth
auth_type=userpass
username=1001
password=REPLACE_WITH_GENERATED_PASSWORD

[1001]
type=aor
max_contacts=1
remove_existing=yes
```

The endpoint and AOR sharing a name is intentional. These settings follow the [upstream PJSIP examples](https://github.com/asterisk/asterisk/blob/22/configs/samples/pjsip.conf.sample). `direct_media=no` keeps Asterisk in the audio path; the symmetric RTP/contact options accommodate remote phones. See the [NAT configuration reference](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/Configuring-res_pjsip-to-work-through-NAT/).

This is a LAN/VPN configuration. Do not set `external_media_address` or `external_signaling_address` to the VM's public IP: that VM is not the RTP endpoint in this design. Do not expose this unencrypted SIP/RTP pilot to the public Internet.

Create `config/rtp.conf`:

```ini
[general]
rtpstart=10000
rtpend=10100
strictrtp=yes
```

This is a small pilot range, distinct from coturn's relay range. See the [upstream RTP sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/rtp.conf.sample).

Create `config/logger.conf`:

```ini
[general]
[logfiles]
console => notice,warning,error
```

Keep the SIP password out of Git and shared logs. Ensure the container's `asterisk` user can read the bind-mounted configuration files; if you restrict permissions, assign an appropriate matching group rather than making the directory unreadable to the container.

## 4. Configure the dialplan and future AI connection

Create `config/extensions.conf`:

```ini
[from-linphone]
; Immediate test: hear your own speech returned by Asterisk.
exten => 6000,1,Answer()
 same => n,Echo()
 same => n,Hangup()

; Requires TELEPHONY_ENABLED=true in the agent.
exten => 2000,1,NoOp(AI survey call)
 same => n,Dial(WebSocket/pipecat/c(slin16)f(json),30)
 same => n,Hangup()
```

Create `config/websocket_client.conf`:

```ini
[pipecat]
type=websocket_client
connection_type=per_call_config
uri=ws://127.0.0.1:7861/telephony/media
protocols=media
connection_timeout=3000
reconnect_attempts=0
```

The URI is the additional private Python listener enabled in step 7. Do not route it to the browser offer API. These client settings follow the [upstream WebSocket client sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/websocket_client.conf.sample).

There is no need to expose ARI, AMI, or an Asterisk HTTP listener for this direct-dialplan pilot. There is also no external-number dial pattern in this context.

## 5. Allow the phone network on Ubuntu

If UFW is active, allow only the network/interface used by the phone. For **Tailscale**, for example:

```bash
sudo ufw allow in on tailscale0 proto udp to any port 5060
sudo ufw allow in on tailscale0 proto udp to any port 10000:10100
```

Your tailnet access policy must also permit this traffic. For a **LAN-only** test, substitute your actual LAN subnet for `192.168.1.0/24`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 5060 proto udp
sudo ufw allow from 192.168.1.0/24 to any port 10000:10100 proto udp
```

Use the applicable rules, not public `allow 5060` rules. Do not enable/reset UFW blindly on a remotely managed server. No changes to public VM firewall, Nginx, TURN ports, or home-router forwarding are required for this VPN design.

## 6. Build and test Linphone before the AI

From `~/voice-pbx` on Ubuntu:

```bash
docker compose build asterisk
docker compose up -d asterisk
docker compose exec asterisk asterisk -rx 'core show version'
docker compose exec asterisk asterisk -rx 'module show like chan_websocket'
docker compose exec asterisk asterisk -rx 'module show like res_websocket_client'
docker compose exec asterisk asterisk -rx 'pjsip show endpoints'
docker compose exec asterisk asterisk -rx 'dialplan show from-linphone'
```

Stop if a required module is missing or the build fails. Do not assume a successful container start means all required modules loaded.

In Linphone, choose an existing/third-party SIP account rather than creating a Linphone-hosted account:

| Setting | Value |
| --- | --- |
| Username / authentication username | `1001` |
| Password | The generated SIP password |
| Domain / server | Ubuntu's LAN IP, or its Tailscale IPv4 address |
| SIP identity, if requested | `sip:1001@UBUNTU_IP` |
| Proxy, if requested | `sip:UBUNTU_IP:5060;transport=udp` |
| Transport | UDP |
| Audio codec | PCMA / PCMU (G.711 A-law / mu-law) |
| Media encryption | None for this private LAN/VPN pilot |
| ICE / STUN / TURN | Disable for this simple LAN/VPN pilot |

The application UI labels vary. The VPN encrypts the remote network path; SIP TLS/SRTP can be added later if required. Do not reuse the browser's TURN credentials here.

Check registration:

```bash
docker compose exec asterisk asterisk -rx 'pjsip show contacts'
```

Dial `6000`, or the full URI `sip:6000@UBUNTU_IP`. You should hear your own voice returned. Test on Wi-Fi first, then on mobile data with the VPN connected. This verifies registration and bidirectional audio **without Azure or Python**.

If registration succeeds but echo is silent, inspect routing and the RTP firewall before changing the AI. Temporary SIP/RTP debug logs can contain identifiers and network details; disable debugging after diagnosis.

## 7. Enable the Pipecat telephone adapter

After updating the application checkout on Ubuntu with the adapter code, edit the application's `.env` (not the Asterisk SIP configuration):

```dotenv
TELEPHONY_ENABLED=true
TELEPHONY_PORT=7861
TELEPHONY_MAX_CALLS=2
TELEPHONY_MAX_SECONDS=900
SURVEY_CONVERSATION_MANAGER=true
SURVEY_QUESTIONNAIRE=questionnaire.customer-satisfaction.ar.json
DEFAULT_LANGUAGE=ar
AZURE_TTS_VOICE_AR=ar-SA-HamedNeural
```

Keep all existing Azure credentials and browser/TURN settings. From the **application checkout directory**, build and recreate only the agent:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
curl --max-time 5 http://127.0.0.1:7861/health
docker compose -f docker-compose.ubuntu.yml logs --tail=80 agent
```

Expected health response: `{"status":"ok","calls":0}`. The logs should include `Telephone media listening on 127.0.0.1:7861`. This requires the Ubuntu host-network compose file; the default bridge-network file does not share localhost with Asterisk. Do not open port 7861 in UFW or add it to public Nginx routes.

The `2000` dialplan and WebSocket client configuration from step 4 already target this listener. If those files are unchanged, Asterisk does not need restarting. If you edited them, restart Asterisk from `~/voice-pbx` after ending active calls:

```bash
docker compose restart asterisk
```

Dial `2000` in Linphone. The agent should introduce the Arabic survey, accept consent, and continue across turns. Speak over a response to test interruption, then hang up and check that `/health` returns to zero calls. You can also monitor the agent log with `docker compose -f docker-compose.ubuntu.yml logs -f agent` from the application directory.

To disable telephone calls later, set `TELEPHONY_ENABLED=false` and recreate the agent without `--build`. The browser service remains available.

Implementation details follow for maintenance and review. `bot.py` now accepts an alternate transport and starts the telephone greeting on media readiness, while browser calls retain RTVI startup.

Implementation structure:

| Component | Responsibility |
| --- | --- |
| `bot.py` | Reuse Azure services, survey state, Arabic language lock, and metrics with either transport |
| `asterisk_transport.py` | Asterisk-specific mixed binary/text WebSocket media transport and control handling |
| `telephony_server.py` | Private listener on `127.0.0.1:7861`, WebSocket subprotocol `media`, call admission and lifecycle |
| Per-call session | Map Asterisk channel/connection ID to one isolated survey, task, and cancellation scope |
| Tests | Protocol, resampling, barge-in, disconnect races, isolation, plus existing survey regressions |

The installed Pipecat version remains `0.0.72`. Its generic FastAPI WebSocket transport selects either binary or text messages for a connection. Asterisk uses **both**, so the new adapter handles both message types directly using the pinned base transport APIs.

The adapter contract is:

1. Validate `MEDIA_START` before starting a call session. Receive `slin16` mono PCM at 16 kHz and deliver it to the existing VAD/STT pipeline. Start the Arabic survey greeting when media is ready, without waiting for browser RTVI.
2. Preserve Azure TTS's existing 24 kHz output. Resample continuously to 16 kHz at the telephone transport boundary; let Asterisk transcode to the phone's G.711 codec. Upsampling G.711 input does not recover lost telephone bandwidth.
3. Send raw PCM binary frames using the announced optimal frame size, retaining partial frames and padding the final fragment when appropriate. Keep queues bounded and handle flow control.
4. On caller speech, cancel the current Pipecat/TTS output, invalidate old output, and flush Asterisk's queued audio. Serialize flush and audio sends to prevent stale audio being queued again after the flush. Continue receiving caller audio throughout playback.
5. Treat Asterisk playback progress separately from WebSocket transmission completion. Do not close immediately after sending the final spoken message; wait for playback completion with a bounded timeout.
6. On hangup/socket close, cancel and await call tasks, close Azure resources, clear conversation memory, and reject late output. Never resume a disconnected call's survey in a new call accidentally.
7. Enforce call limits, idle/maximum duration, and explicit graceful shutdown. Keep the media listener private and out of the public Nginx routes. Do not log credentials, raw audio, or full transcripts by default.

Protocol controls, including `FLUSH_MEDIA`, are defined by the [Asterisk driver](https://github.com/asterisk/asterisk/blob/22/channels/chan_websocket.c). The media driver handles pacing, but the adapter must still obey its framing, flow-control, and cancellation contract.

Keep the current questionnaire, one model call per finalized turn, authoritative survey state, `DEFAULT_LANGUAGE=ar`, and `ar-SA-HamedNeural`. Do not replace the working Azure services or upgrade Pipecat as a prerequisite without a separate reason.

The loopback port `7861` must be unused before enabling the listener; a bind conflict fails app startup explicitly. A temporary test server has used that port previously. The listener is feature-flagged off by default and runs under the main application's startup/shutdown lifecycle.

The current pilot waits for the caller to hang up after the survey; the call-duration cap also ends an abandoned call. Automatic hangup after the survey closing is not implemented. A normal pipeline end waits briefly for Asterisk's playback-drained acknowledgement.

## 8. Acceptance checks

- `1001` registers and `6000` returns audio over LAN and VPN/mobile data.
- `2000` greets once in Arabic with the configured voice.
- Several consecutive questions work; unrelated numbers and off-topic speech do not commit an answer or advance the questionnaire.
- Speaking during a long reply stops audible playback promptly; the interrupted reply does not restart from a stale queue.
- Hangup during STT, model inference, TTS, and playback leaves no active call task or retry callbacks. Repeat calls start cleanly.
- Two simultaneous calls have independent histories and answers; stale/duplicate transcripts cannot mutate another session.
- The existing browser path and survey regression suite still pass.
- Record speech-end-to-first-audio latency, interruption-to-silence latency, token usage, codec, and error counts without storing customer speech by default.

The user has confirmed Linphone reaches the Asterisk echo application. Local automated tests exercise the new adapter using simulated Azure services; they do not establish real microphone, telephone codec, Azure, or audible interruption quality. See [the implementation report](ASTERISK_REPORT.md) for validation and remaining checks.

## 9. Later carrier trunk

The later route becomes:

```text
Omani telephone number <-> carrier SIP trunk <-> Asterisk
                                             <-> same private Pipecat media adapter
```

Obtain the carrier's actual handoff requirements: private circuit/VPN/public connection, SBC addresses, authentication method, assigned numbers, codecs, signaling/media transport, RTP ranges, number format, permitted caller IDs, channel limits, and inbound/outbound permissions. Do not guess Omantel/Ooredoo settings.

Add a separate restricted trunk context. Route authorized inbound numbers into the same AI dialplan/media entry point. Outbound calling additionally needs an authenticated origination controller, permitted destinations, and carrier configuration. Do not expose arbitrary outbound dialing to extension `1001` or let model output select unrestricted telephone destinations.

The Azure/survey pipeline can remain shared. Carrier provisioning and network/security configuration still require their own integration and tests.
