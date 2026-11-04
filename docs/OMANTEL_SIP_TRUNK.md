# Omantel SIP trunk: staged configuration and deployment record

Status on 2026-11-04: **configuration staged; user-supplied Ubuntu audit reviewed;
trunk not installed or verified**.
The supplied block `211308XX` is configured as `21130800`–`21130899`.
The pilot DID and outbound identity are `21130800`. Service line `9999421`
is retained here as an account reference, not a seven-digit SIP caller ID.
No outbound test destination or call approval has been supplied.

The user subsequently approved the proposed Netplan and Omantel SIP firewall
changes, including the dedicated firewall startup service. That approval remains
valid for this session. It does not approve an Asterisk restart or telephone calls.
Application on Ubuntu and its verification results are still pending; this
Windows workspace has no established SSH connection to that server.

For the approved network rollout, generate backend configuration in a private
staging root using `netplan generate --root-dir`, retaining all existing Netplan
source directories in that root. Install the new persistent YAML and only the
generated `10-netplan-enp11s0.network` runtime file. Then reload networkd's
definitions and reconfigure `enp11s0`. This avoids regenerating/reloading the
internet and Wi-Fi configuration as part of a global `netplan apply`.
`networkctl reload` can reconfigure interfaces whose files changed, so existing
runtime network files must remain untouched. Record the default routes before
and after and confirm both SBCs from the bound source address.

The [Netplan generator](https://netplan.readthedocs.io/en/1.1/netplan-generate/)
supports isolated generation, and [networkctl](https://www.freedesktop.org/software/systemd/man/249/networkctl.html)
documents reloading definitions and reconfiguring interfaces.

## Inspection findings and actual results

The checkout is on Windows, with no Docker executable. The separate Ubuntu PBX
configuration is not in this repository. The user supplied an audit captured at
11:31 Dubai time (07:31 UTC) on 2026-11-04. Findings below come from that snapshot,
not direct SSH access. No production configuration has been changed by this task.

| Item | Repository evidence | Live status |
|---|---|---|
| Asterisk Compose | `/home/data_mining/voice-pbx/compose.yml`; five individual read-only config mounts | Supplied Compose confirms host networking and existing image build; no Compose change needed for inline fragments |
| Version | Runtime `core show version` | Asterisk 22.11.0 |
| Local SIP | Live `pjsip.conf` and endpoint inspection, followed by user call confirmation | Authenticated `1001`, A-law/mu-law, wildcard UDP/5060; user confirms `2000@192.168.1.95` works; no contact was registered at the earlier audit instant |
| Dialplan | Live dialplan and mounted file | `6000@from-linphone` Echo and `2000@from-linphone` WebSocket/slin16/json confirmed |
| RTP | Runtime settings and mounted file | UDP 10000–10100, strict RTP enabled; carrier media ranges remain unknown |
| Media bridge | WebSocket module and loopback health | Module loaded; `127.0.0.1:7861/health` reports ok, zero calls; A-law/8 kHz to slin/16 kHz translation exists, reverse/live audio not tested |
| Agent networking | `docker-compose.ubuntu.yml` uses host networking; development Compose uses bridge networking | Deployed agent mode unknown |
| Network | Host/Asterisk namespace and subsequent nmcli output | Address and both `/32` routes correct; both peers 4/4 pings, primary average 3.258 ms, secondary 3.971 ms; Omantel externally configured, autoconnect no; persistence not confirmed |
| Internet/Azure | Current default route is via `192.168.1.1` on `enp10s0`, source `192.168.1.95`; Wi-Fi backup uses `.102`, metric 600 | Default internet route exists; Azure connectivity not tested |
| Firewall | UFW inactive; IPv4 INPUT accepts with no displayed SIP source restriction; Docker rules primarily affect forwarding | No host SIP ingress restriction shown; upstream router exposure unknown |
| OPTIONS | Independent AORs configured for 30-second qualification | Neither SBC tested; no availability claim |
| Calls/redundancy | Restricted candidate dialplan prepared | No inbound, outbound, AI, or failover calls performed |

## Next steps for the audited server

The existing PBX files are under `/home/data_mining/voice-pbx/config`. No Docker
network change or image rebuild is needed. All required modules inspected
(`chan_websocket`, IP endpoint identification, PJSIP ACL) are loaded.

The follow-up nmcli output reports `enp11s0` connected externally with profile
`enp11s0`, UUID `b46db0a1-f527-4f03-9d14-ec6d0045b1e2`, autoconnect **no**.
`networkctl` reports it unmanaged and the shown Netplan files cover only Ethernet
internet and Wi-Fi. The external profile reflects the current network state;
it does not establish a boot-time configuration. See the
[NetworkManager external-connection behavior](https://www.networkmanager.dev/docs/api/latest/NetworkManager.conf.html).
The unused `Wired connection 1` profile has autoconnect yes, but its interface
binding is not shown; do not delete or modify it blindly. Use explicit networkd
ownership of only `enp11s0` in the proposed Netplan file, keeping Wi-Fi's current
NetworkManager ownership and the existing internet Ethernet configuration.
Any existing custom boot script that configures this interface must be reconciled
before installing the proposal; none has been identified in the supplied output.

`deploy/omantel/90-omantel.yaml.proposed` is a concrete review-only candidate if
no existing manager profile provides persistence. It adds only the Omantel
interface/address and two SBC host routes, with no default gateway or DNS changes.
Approval is required before installing or applying it. Check for any other boot
scripts/services that configure the interface before choosing the final owner.
The [Netplan YAML reference](https://netplan.readthedocs.io/en/latest/netplan-yaml/)
documents per-interface renderers, static addresses and routes.

The minimal PBX proposal is:

1. User confirms Linphone/AI works at `2000@192.168.1.95`. Narrow `transport-udp`
   from `0.0.0.0:5060` to `192.168.1.95:5060`. The Omantel transport binds only
   `100.127.29.138:5060`. No additional Wi-Fi listener is needed for the confirmed
   call path. Current observed addresses are Ethernet `192.168.1.95` and Wi-Fi
   `192.168.1.102`, both DHCP.
   Keep endpoint `1001`, its authentication and `from-linphone` dialplan intact.
   Confirm address stability before relying on a specific-IP listener across boots.
2. Append the contents of `omantel-transport.conf` and `omantel-primary.conf`
   directly to the existing mounted `pjsip.conf`, and append
   `omantel-extensions.conf` directly to the existing mounted `extensions.conf`.
   Prepare private candidate copies and backups first; do not print the SIP secret.
   Do not append `omantel-pjsip.conf`: its includes need mounts that do not currently
   exist. Inlining these fragments avoids Compose changes and container recreation.
3. `deploy/omantel/omantel-signaling.nft.proposed` adds a separate
   `inet omantel_signaling` table. For destination `100.127.29.138:5060/UDP`,
   allow only the two SBCs received on `enp11s0`, then drop all other signaling to
   that destination. All other traffic passes this guard unchanged; existing
   rules still apply. No blanket UFW enable/reset, ruleset flush, or change to
   Docker's generated chains. The companion
   `omantel-signaling.service.proposed` loads `/etc/omantel-signaling.nft` after
   the existing nftables service (if active) and before Docker at boot; stopping
   it removes only the added `inet omantel_signaling` table. Verify `/usr/sbin/nft`
   exists, syntax-check the rules and review the unit on Ubuntu before enabling.
   Approval is required for installation/enabling. Carrier RTP restrictions wait for verified media
   ranges; the proposal contains no guessed media source addresses.
4. After persistence, bind and security prerequisites are resolved, obtain approval
   for one controlled restart of the existing container, then verify primary
   OPTIONS source/response/contact status. Primary only, pilot Echo, outbound off.
   No call approval is implied by restart approval.

The [Asterisk transport reference](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/PJSIP-Configuration-Sections-and-Relationships/)
documents socket uniqueness and transport reload constraints. Never add the
dedicated UDP/5060 bind while keeping the existing wildcard bind.

The earlier fragment-mount instructions below remain an alternative deployment
method; inlining is the smaller change for this audited container.

Prepare private original snapshots and the exact inline PBX candidates on Ubuntu
from this application's checkout (after the staging helper is committed/pushed
and pulled), without installing them:

```bash
sudo python3 -m scripts.stage_omantel_existing \
  --pbx-dir /home/data_mining/voice-pbx \
  --settings deploy/omantel/settings.example.json \
  --linphone-ip 192.168.1.95 \
  --output /home/data_mining/voice-pbx/omantel-review

sudo nft --check --file deploy/omantel/omantel-signaling.nft.proposed
```

Use a new output path if that review directory exists. The helper refuses existing
Omantel objects, unexpected transport binds, and any stage that enables secondary,
AI or outbound calls. It preserves authentication and the Linphone dialplan,
records source hashes for a later unchanged-source check, and writes files under
a private directory (0700) with SIP files 0600. Original snapshots are retained
byte-for-byte. The files contain the real SIP password; do not share full files
or raw diffs. The helper never installs, reloads, restarts, contacts an SBC or
changes networking. The nft check validates the candidate without applying it.
Netplan generation still needs validation on Ubuntu; no Netplan validation tool
is available in this Windows workspace.

Local verification: ten Omantel policy/renderer/staging tests passed. They check number
boundaries, configuration injection rejection, safe defaults, isolated primary and
secondary policies, outbound restrictions, failover generation, and refusal to
overwrite an existing directory, and agreement between the checked-in candidates
and their settings/templates. The extra staging checks verify auth/dialplan
preservation, safe refusal of duplicate or unexpected input, original snapshots,
and unchanged source files. These checks do not run Asterisk's parser.
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
- `scripts/stage_omantel_existing.py`: private inline candidates and original
  snapshots for this existing file-mounted PBX.
- `deploy/omantel/90-omantel.yaml.proposed` and
  `deploy/omantel/omantel-signaling.nft.proposed` with
  `omantel-signaling.service.proposed`: approval-only network/firewall
  proposals, not installed settings.
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
