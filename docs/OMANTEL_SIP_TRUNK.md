# Omantel SIP trunk: staged configuration and deployment record

Status on 2026-11-04: **prepared locally; not installed or verified on Ubuntu**.
The supplied block `211308XX` is configured as `21130800`–`21130899`.
The pilot DID and outbound identity are `21130800`. Service line `9999421`
is retained here as an account reference, not a seven-digit SIP caller ID.
No outbound test destination or call approval has been supplied.

## Inspection findings and actual results

The checkout is on Windows, with no Docker executable. The separate Ubuntu PBX
configuration is not in this repository. SSH configuration has no alias identifying
the Ubuntu PBX; the server SSH target is still needed. No unrelated server was
contacted. Consequently the running container, live files, network persistence,
firewall, and SIP responses have **not** been inspected remotely.

| Item | Repository evidence | Live status |
|---|---|---|
| Asterisk Compose | `docs/ASTERISK_SETUP.md` describes `~/voice-pbx/compose.yml`, service `asterisk`, host networking, individual read-only config mounts | Actual file, mounts and network mode unknown |
| Version | Guide targets Asterisk 22.11.0; WebSocket JSON requires 22.8+ | Running version unknown |
| Local SIP | Guide uses `transport-udp`, wildcard UDP/5060, authenticated extension `1001`, A-law and mu-law | Live transport and registration unknown |
| Dialplan | Guide uses `6000@from-linphone` for Echo, `2000@from-linphone` for `Dial(WebSocket/pipecat/c(slin16)f(json),30)` | Must inspect live entry point |
| RTP | Guide configures UDP 10000–10100 and strict RTP | Actual range/firewall unknown |
| Media bridge | Existing adapter accepts 16 kHz mono `slin16`, 640-byte/20 ms WebSocket audio; stateful TTS resampling, interruption flush and per-call cleanup | Live transcoding/audio not tested |
| Agent networking | `docker-compose.ubuntu.yml` uses host networking; development Compose uses bridge networking | Deployed agent mode unknown |
| Network | User reports `enp11s0`, `100.127.29.138/30`, gateway `100.127.29.137`, successful pings to both SBCs | Host/runtime routes and reboot persistence unverified |
| Internet/Azure | Providers and existing application Compose files unchanged | Actual Ubuntu internet/Azure connectivity unverified |
| OPTIONS | Independent AORs configured for 30-second qualification | Neither SBC tested; no availability claim |
| Calls/redundancy | Restricted candidate dialplan prepared | No inbound, outbound, AI, or failover calls performed |

Local verification: seven Omantel policy/renderer tests passed. They check number
boundaries, configuration injection rejection, safe defaults, isolated primary and
secondary policies, outbound restrictions, failover generation, and refusal to
overwrite an existing directory, and agreement between the checked-in candidates
and their settings/templates. These checks do not run Asterisk's parser.
`bash -n scripts/inspect_omantel.sh` passed. Twelve existing offline telephone
protocol/listener tests passed, covering 20 ms PCM framing, resampling, interruption
flush, pacing, socket validation, admission and cleanup. The full telephone test
module stalled in `test_real_pipeline_greeting_multiple_turns_resampling_and_cleanup`
after initial greeting/turn processing and a media receive timeout; its test
processes were stopped. That test is unresolved, not a pass. The application and
its existing tests were not modified; full AI pipeline acceptance remains open.

## Files and generated configuration

New files:

- `deploy/omantel/settings.example.json`: supplied number block and pilot, safe stage flags.
- `deploy/omantel/transport.conf.in`, `peer.conf.in`: isolated PJSIP templates.
- `deploy/omantel/staged/omantel-transport.conf`: dedicated UDP bind `100.127.29.138:5060`.
- `deploy/omantel/staged/omantel-primary.conf`: endpoint `omantel-primary`, AOR
  `omantel-primary-aor`, identify `omantel-primary-identify`, SBC `212.72.15.217`.
- `deploy/omantel/staged/omantel-secondary.conf`: matching secondary objects for
  `212.72.15.220`, staged separately.
- `deploy/omantel/staged/omantel-pjsip.conf`: transport and primary includes;
  secondary include initially omitted.
- `deploy/omantel/staged/omantel-extensions.conf`: isolated inbound pilot Echo route
  and closed outbound test context.
- `scripts/configure_omantel.py`: standard-library-only review-file generator;
  no Docker, SSH, installation, reload or call operation.
- `scripts/inspect_omantel.sh`: Ubuntu read-only audit, including namespace pings.
- `tests/unit/test_omantel.py`, this guide, and a README link.

No application provider, transport implementation, existing Compose, local SIP,
Netplan, firewall, or running container was changed.

Both peer fragments contain:

```ini
transport=omantel-udp
context=from-omantel
identify_by=ip
disallow=all
allow=alaw:20
use_ptime=no
dtmf_mode=inband
direct_media=no
media_address=100.127.29.138
bind_rtp_to_media_address=yes
send_pai=yes
send_rpid=no
from_user=21130800
callerid=21130800
```

Each has an endpoint ACL permitting only its own SBC, a `/32` identify match, no
auth or registration object, and a fixed AOR contact on UDP/5060. AOR qualification
is every 30 seconds, with a three-second timeout and `qualify_2xx_only=yes`.
The latter requires Asterisk 22.2+; verify the actual version before installation.
Transfers and subscriptions are disabled on these endpoints. Load
`res_pjsip_endpoint_identifier_ip` and `res_pjsip_acl`; retain the existing
Linphone authentication and inspect any anonymous endpoints or global includes.

`allow=alaw:20` selects local 20 ms framing. `use_ptime=no` keeps a remote SDP
ptime from overriding it. `dtmf_mode=inband` avoids RFC4733 negotiation on this
leg. Actual SDP and transmitted RTP still require call capture. Do not set
`use_ptime=yes` assuming that it forces 20 ms.

The Asterisk 22 [PJSIP reference](https://docs.asterisk.org/Asterisk_22_Documentation/API_Documentation/Module_Configuration/res_pjsip/)
documents transport selection, source ACLs, media binding, identity and qualification.
The [22.11 RTP implementation](https://github.com/asterisk/asterisk/blob/22.11.0/res/res_pjsip_sdp_rtp.c)
shows how remote ptime is applied. The [OPTIONS implementation](https://github.com/asterisk/asterisk/blob/22.11.0/res/res_pjsip/pjsip_options.c)
creates qualification requests using the associated endpoint, which selects the
dedicated transport. Capture packets to verify the resulting source address.

## First: inspect Ubuntu without changing it

Copy or check out these files on the identified Ubuntu PBX host, then run:

```bash
sudo bash scripts/inspect_omantel.sh
```

Review the output locally before sharing it. Credential-bearing single lines are
redacted, but this is not a general secret scrubber. Included configuration files
must also be inspected locally; never share SIP passwords, keys or raw `.env` files.
The audit does not restart/reload anything, send SIP requests, or place calls.
It reads the existing namespace using host tools, so the image need not contain
`ip` or `ping`. Missing tools and permissions produce explicit failed checks;
the final count is not proof of a valid configuration. Asterisk CLI can also
report an error while returning exit status zero; inspect its actual response.

Confirm the interface has exactly `100.127.29.138/30`, and both SBC routes are
`/32`, via `100.127.29.137`, device `enp11s0`, source `100.127.29.138`. Inspect
policy rules and the effective Netplan/network manager configuration, not just
current `ip route` output. If systemd-networkd or NetworkManager owns persistence
outside Netplan, inspect that source too. Do not reboot to test persistence now.

If persistence is missing, the following is an **approval-only proposal** to merge
into the existing interface definition, preserving its renderer and all unrelated
addresses/routes. Do not copy it over an existing Netplan file:

```yaml
network:
  version: 2
  ethernets:
    enp11s0:
      addresses:
        - 100.127.29.138/30
      routes:
        - to: 212.72.15.217/32
          via: 100.127.29.137
        - to: 212.72.15.220/32
          via: 100.127.29.137
```

No `gateway4`, default route, DNS or other interface changes are needed. Present
the actual merged diff for approval before applying it. `netplan generate` only
checks generation; a controlled apply/try requires approval and a recovery path.

Compare the default internet route before/after the eventual approved work.
Check the deployed agent's existing Azure endpoints with DNS and TLS connection
checks, without printing keys. The private `/health` check proves listener health,
not Azure authentication. Existing `scripts.verify_services` checks consume Azure
services and are separate from a network/TLS check.

## Resolve transport and Docker prerequisites before loading

Do not include the candidate transport alongside a wildcard UDP/5060 transport.
The documented `bind=0.0.0.0:5060` may prevent a second socket binding
`100.127.29.138:5060`. Inspect `pjsip show transports` and host/runtime `ss` first.
If the wildcard exists, prepare an exact diff that preserves its name and local
endpoint references while binding it to the actual LAN/VPN address(es) needed by
Linphone. Multiple local interfaces may need separate transports and endpoint
review; no LAN/VPN address is guessed here. Confirm Linphone registration, `6000`
and `2000` still work after the approved change.

The dedicated transport requires the Omantel IP in the Asterisk network namespace.
If Asterisk already uses Linux host networking, retain it. If it uses Docker bridge
networking, stop and review its published ports, source NAT, SDP and loopback bridge
access. Do not assume the host's successful ping is sufficient. Host networking
is the preferred candidate on this Ubuntu topology, but changing it would recreate
the existing container and needs approval. No new container is required.

Use the mount paths reported by `docker inspect`. If `/etc/asterisk` is already a
directory bind mount, the five generated files can be staged in that directory.
If the guide's individual file mounts are used, add explicit read-only mounts for
the five fragments to the **existing** PBX Compose service. For example, one entry
would be `./config/omantel-primary.conf:/etc/asterisk/omantel-primary.conf:ro`.
Mount changes need an approved recreation of that container; restart alone cannot
add mounts. Do not replace the image or application architecture.

Back up the actual Compose and Asterisk files before any approved installation.
The reviewed main-file additions are:

```ini
; pjsip.conf, outside an endpoint/transport definition:
#include "omantel-pjsip.conf"

; extensions.conf, after the existing contexts:
#include "omantel-extensions.conf"
```

Keep existing context contents intact. With individual file mounts, preserve the
mounted file inode when changing its contents, or use the approved recreation to
refresh mounts. Do not use a live reload as an offline syntax check: it changes
production state. This workspace cannot validate these fragments with the actual
Asterisk parser. Validate effective objects and logs during the controlled rollout.

Inspect existing firewall rules and exposure first. The candidate ACL limits
Omantel endpoint acceptance, but it does not replace a firewall. If needed, propose
interface/source/destination-specific UDP/5060 allows on `enp11s0`, only from the
two SBCs to `100.127.29.138`. RTP/RTCP rules must use the actual Asterisk port range
and Omantel-confirmed media source ranges, which may differ from SBC signaling
IPs. Preserve LAN/VPN rules, default policies and the production firewall. Obtain
approval for the actual rule diff; no blanket SIP/RTP public allow is appropriate.

## Stages and controlled tests

Render changed settings into a **new** staging directory:

```bash
python3 -m scripts.configure_omantel \
  --settings deploy/omantel/settings.example.json \
  --output /tmp/omantel-primary-review
```

The renderer refuses existing output directories and unknown settings. It enforces
eight ASCII digits and verifies the DID/caller ID belong to the supplied block.
The supplied block itself must reflect the carrier allocation. Do not expand it
just to pass validation. Generated files are review artifacts, not an installer.

1. **Network and primary heartbeat:** finish the audit, resolve bind/mount issues,
   present actual diffs, and obtain approval for the controlled restart/recreation.
   Confirm no active calls first. Start with `secondary_enabled=false`,
   `failover_enabled=false`, `outbound_enabled=false`, `inbound_mode="echo"`.
   After the approved load, inspect primary endpoint, AOR, contacts and logs.
   Confirm packet capture shows OPTIONS from `100.127.29.138:5060` to
   `212.72.15.217:5060` and a matching successful response. Capture at least two
   30-second cycles and record SIP status, RTT, timestamp and contact status.
2. **Secondary heartbeat:** only after primary qualification succeeds, set
   `secondary_enabled=true`, regenerate/review and load its include. Confirm the
   same source address, responses and independent availability for `.220`.
   This enables monitoring/inbound identification, not outbound failover.
3. **Incoming pilot call:** arrange an approved mobile call to `21130800`.
   Only that exact Request-URI extension routes to the 60-second Echo answer.
   Other numbers, `s`, invalid and timeout extensions terminate; no internal or
   outbound context is included. Confirm INVITE/DID, answer, bidirectional echo,
   SDP, media routes, in-band digits, and BYE/cleanup. If the carrier presents a
   different DID format, inspect it and agree the handling before changing routing.
4. **Outgoing test:** obtain an explicit approved eight-digit destination and
   approval to place one call. Add only that destination to
   `outbound_test_numbers`, then enable `outbound_enabled` in reviewed settings.
   The private `omantel-outbound-test` context contains literal permitted numbers;
   never include it from `from-linphone`, `from-omantel`, or a public context.
   Dial options `iI` ignore forwarding and identity updates during these test calls.
   A trusted operator can originate the approved destination through a Local
   channel into this context, bridging the answer to `Echo`. No origination
   command is executed by the scripts. Verify `21130800` in From/caller ID and
   P-Asserted-Identity, primary routing, PCMA audio, and hangup. Return outbound
   calling to disabled after the test.
5. **AI:** once ordinary calls pass, inspect the actual `2000` entry point and
   set `inbound_mode="ai"`. The exact pilot route then uses
   `Goto(from-linphone,2000,1)`; no broad context include is added. If the actual
   AI context differs, correct this explicit target before installation. Verify
   Arabic STT, model responses, configured Azure TTS, interruption, repeated calls,
   caller hangup during each provider stage, and `/health` returning to zero calls.
6. **Secondary calls and failover:** with separate call approval, validate an
   outbound call through the secondary in a temporary operator-only route. Do not
   include that route from a SIP context. Only after successful secondary calls,
   set `failover_enabled=true`. Generated routing prefers primary and checks its
   qualified device state. An unavailable primary selects secondary; transient
   CHANUNAVAIL/CONGESTION results retry only for the listed network/resource Q.850
   causes. Busy, no-answer, rejection, cancellation and answered calls are not
   intentionally retried. Both legs have a 30-second dial timeout; the test channel
   has a 120-second absolute limit. Do not block an SBC or disrupt production to
   simulate failure without approval. Until real routing/failure tests pass,
   redundancy remains **unverified**, even if both contacts qualify.

Routing uses Asterisk's [documented trunk dial syntax](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/Dialing-PJSIP-Channels/)
and [Dial result behavior](https://docs.asterisk.org/Asterisk_22_Documentation/API_Documentation/Dialplan_Applications/Dial/).
OPTIONS is reachability information; it is not a guarantee that a call will succeed.

## SIP, RTP and audio inspection commands

Run these on Ubuntu; none originates a telephone call:

```bash
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show endpoint omantel-primary'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show aor omantel-primary-aor'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show endpoint omantel-secondary'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show aor omantel-secondary-aor'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show contacts'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show registrations'
docker exec voice-pbx-asterisk-1 asterisk -rx 'dialplan show from-omantel'
docker exec voice-pbx-asterisk-1 asterisk -rx 'dialplan show omantel-outbound-test'
docker exec voice-pbx-asterisk-1 asterisk -rx 'core show translation paths alaw'
docker exec voice-pbx-asterisk-1 asterisk -rx 'rtp show settings'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip show channelstats'
sudo timeout 70 tcpdump -ni enp11s0 -s0 -A \
  'udp port 5060 and (host 212.72.15.217 or host 212.72.15.220)'
```

`pjsip show registrations` may show unrelated existing registrations; the Omantel
objects must not add any. During a scheduled diagnostic window, temporary Asterisk
SIP/RTP logging can also be enabled:

```bash
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip set logger on'
docker exec voice-pbx-asterisk-1 asterisk -rx 'rtp set debug ip MEDIA_IP_FROM_SDP'
# Inspect with the existing console/logging configuration, then disable:
docker exec voice-pbx-asterisk-1 asterisk -rx 'rtp set debug off'
docker exec voice-pbx-asterisk-1 asterisk -rx 'pjsip set logger off'
```

Treat captures and SIP debug logs as sensitive; avoid capturing unrelated calls.
Confirm the negotiated Omantel audio SDP contains only payload 8 / PCMA at 8000 Hz,
`a=ptime:20`, `c=IN IP4 100.127.29.138`, and no telephone-event, PCMU, Opus or CN.
Static payload 8 may omit an explicit rtpmap. Incoming offers are carrier-controlled;
the requirement applies to the negotiated answer and our outgoing offers too.

For the actual SDP media IP, inspect (do not blindly apply routes):

```bash
ip -4 route get MEDIA_IP_FROM_SDP from 100.127.29.138
sudo timeout 30 tcpdump -ni enp11s0 -s0 -vv \
  'udp and host MEDIA_IP_FROM_SDP and portrange ACTUAL_RTP_START-ACTUAL_RTP_END'
```

Substitute the observed address/range first. Inspect namespace routing as in the
audit. Both directions must use the carrier interface; binding a source IP alone
does not create a route to an arbitrary media address. If a route is missing,
confirm it with Omantel and seek approval for its persistent network/firewall diff.
Do not add guessed media subnet routes or assume media comes from the signaling SBC.
PCMA/20 ms normally carries 160 audio bytes per packet with an RTP timestamp step
of 160; verify a capture with Wireshark or tshark, along with received/transmitted
packet counters, return audio and call teardown.

Asterisk must provide translation between A-law/8 kHz and `slin16`/16 kHz on the
existing WebSocket leg. Verify the loaded codec/resampling translation path in
both directions. The Python adapter already accepts the required 640-byte PCM
frames, resamples existing TTS output, paces 20 ms output, flushes interrupted
speech, and cancels per-call services on disconnect. It was left unchanged.
Upsampling telephone audio cannot restore missing telephone bandwidth. Automated
bridge checks cannot establish live Arabic recognition quality or carrier RTP.

## Outstanding carrier requirements and rollback

Omantel must confirm IP authentication for `100.127.29.138`, initial pilot routing
to `21130800`, the presented DID format, accepted PAI identity/domain if constrained,
both SBC roles, media address/port ranges, and the permitted channel count. Verify
that `21130800` is authorized for DOD. Initial call success must be reported to
Omantel before assuming that the full `211308XX` block and all channels are active.

Record network, heartbeat, ordinary calls, AI calls and redundancy separately.
The user-reported successful pings do not establish SIP or full-block activation.

For rollback after an eventual approved deployment:

1. Stop test origination; restore the backed-up `pjsip.conf`, `extensions.conf`,
   transport binds and any changed PBX Compose mounts. Preserve mounted inodes or
   use the approved recreation when restoring individual file mounts.
2. Remove only the added Omantel includes/mounts, retaining the original Linphone,
   RTP and WebSocket settings. Fragments left on disk without includes are inert.
3. Transport/mount rollback requires a controlled Asterisk restart/recreation;
   check active calls and obtain approval first. Do not run `compose down` on the
   application stack. Dialplan-only changes can use a reviewed reload.
4. Restore only separately approved network/firewall changes from their exact
   backups/diffs, using a recovery session and approval; never reset the firewall
   or replace the server's whole network configuration.
5. Verify Linphone registration, `6000`, `2000`, default internet/Azure access and
   private media listener health. This task made no live changes to roll back.
