# Asterisk reconstruction reference

Use [the recovery guide](../../docs/VOICE_SERVER_RECOVERY.md). This directory holds
source templates, not live configs. Do not run Compose here directly.

For the phone-number page and live transcript, follow
[web dialer deployment](../../docs/WEB_DIALER.md). It adds
[extensions-web.conf](extensions-web.conf), a private loopback AMI account from
[manager.conf.in](manager.conf.in), and the sixth read-only config mount in
[compose.web.yml](compose.web.yml). The local/inbound dialplan below remains in
place. Only the separate web operator route initiates calls;
`omantel-outbound-test` remains disabled.

After installing the web dialer, use `-f compose.yml -f compose.web.yml` when
recreating the PBX. For reconstruction, add
`--ami-secret-file /private/asterisk-ami-secret` to the generator command and
restore the matching AMI secret/operator login in the private application `.env`.

Create a complete **new private PBX directory** from the repository root:

```bash
python3 -m scripts.prepare_pbx_recovery \
  --output /home/data_mining/voice-pbx \
  --lan-ip 192.168.1.95 \
  --generate-password
```

The output includes both SBCs, the pilot AI route, local extension 1001, a source
build recipe and host config references. It never installs/starts anything and
refuses existing directories. A generated password replaces the old Linphone
password; use a private password file to retain the old one. Follow the guide for
numeric UID/GID permissions, network/firewall restoration, agent startup, tests
and rollback. Prefer the exact live backup/images when available.

## Working dialplan (`config/extensions.conf`)

The recovery generator combines [extensions-local.conf](extensions-local.conf)
with the Omantel dialplan generated from
[recovery-settings.json](recovery-settings.json). The complete working dialplan
is shown below. `NoOp` messages are diagnostic labels; the generator may use a
different label for the pilot without changing its route.

On the server, save this configuration in
`/home/data_mining/voice-pbx/config/extensions.conf`. The Compose mount
`./config/extensions.conf:/etc/asterisk/extensions.conf:ro` makes that host file
available to Asterisk read-only. Edit the host file, then reload the dialplan:

```bash
docker exec voice-pbx-asterisk-1 asterisk -rx 'dialplan reload'
```

```ini
[from-linphone]
; Immediate test: hear your own speech returned by Asterisk.
exten => 6000,1,Answer()
 same => n,Echo()
 same => n,Hangup()

; Requires the Python media adapter described below.
exten => 2000,1,NoOp(AI survey call)
 same => n,Dial(WebSocket/pipecat/c(slin16)f(json),30)
 same => n,Hangup()

; Generated candidate. No internal or outbound context is included here.
[from-omantel]
exten => 21130800,1,NoOp(Omantel pilot AI test)
 same => n,Goto(from-linphone,2000,1)
exten => _.,1,Hangup(1)
exten => s,1,Hangup(1)
exten => i,1,Hangup(1)
exten => t,1,Hangup(16)

; Private operator context. Do not include from any SIP context.
[omantel-outbound-test]
; Outbound calling disabled: no Dial applications in this context.
exten => _.,1,Hangup(1)
exten => s,1,Hangup(1)
exten => i,1,Hangup(1)
exten => t,1,Hangup(16)
```

Extension `6000` runs the local echo test. Extension `2000` connects to the
Pipecat agent through the `pipecat` WebSocket client in
[websocket_client.conf](websocket_client.conf), using
`ws://127.0.0.1:7861/telephony/media`. Start the Python agent before testing the
AI route. Incoming calls to `21130800` enter `from-omantel` and go directly to
extension `2000`; other incoming numbers are rejected. Outbound calling remains
disabled.
