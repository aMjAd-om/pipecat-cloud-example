#!/usr/bin/env bash
# Read-only Ubuntu audit. No reload/restart, calls, SIP probes or network changes.
set -uo pipefail
container=voice-pbx-asterisk-1
if (( EUID != 0 )); then
    printf 'Run on the Ubuntu PBX host: sudo bash scripts/inspect_omantel.sh\n' >&2
    exit 2
fi
failures=0
run() {
    printf '\nInspecting: %s\n' "$*"
    "$@" || { printf 'Check failed (or command unavailable).\n' >&2; failures=$((failures + 1)); }
}
cli() { run docker exec "$container" asterisk -rx "$1"; }
redact() {
    python3 -c '
import re, sys
for line in sys.stdin:
    if re.search(r"(?i)(password|passwd|secret|token|api.key|authorization)\s*[:=]", line):
        print("; credential-bearing line redacted")
    else:
        print(re.sub(r"(\w+://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", line), end="")
'
}
run date -u
run ip -4 addr show dev enp11s0
run ip -4 route show table all
run ip -4 rule show
for sbc in 212.72.15.217 212.72.15.220; do
    run ip -4 route get "$sbc" from 100.127.29.138
done
run ip -4 route get 1.1.1.1
run networkctl status enp11s0 --no-pager
printf '\nPersistent Netplan source (credential lines redacted; review locally):\n'
shopt -s nullglob
netplan_files=(/etc/netplan/*.yaml /etc/netplan/*.yml)
if (( ${#netplan_files[@]} )); then
    for config in "${netplan_files[@]}"; do
        printf '\n%s\n' "$config"
        cat "$config" | redact || failures=$((failures + 1))
    done
else
    printf 'No Netplan YAML found; inspect the active network manager persistence.\n'
fi
run docker inspect --format 'Network={{.HostConfig.NetworkMode}} PID={{.State.Pid}} Running={{.State.Running}} Image={{.Config.Image}}' "$container"
run docker inspect --format '{{json .Mounts}}' "$container"
run docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' "$container"
run ss -lntup
run ufw status verbose
run nft list ruleset
run iptables-save
pid=$(docker inspect --format '{{.State.Pid}}' "$container") || exit 1
if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Container is not running; no runtime namespace to inspect.\n' >&2
    exit 1
fi
run nsenter -t "$pid" -n ip -4 addr show
for sbc in 212.72.15.217 212.72.15.220; do
    run nsenter -t "$pid" -n ip -4 route get "$sbc" from 100.127.29.138
    run nsenter -t "$pid" -n ping -I 100.127.29.138 -c 4 -W 2 "$sbc"
done
cli 'core show version'
cli 'core show channels count'
cli 'pjsip show transports'
cli 'pjsip show endpoints'
cli 'pjsip show endpoint 1001'
cli 'pjsip show aors'
cli 'pjsip show contacts'
cli 'pjsip show identifies'
cli 'pjsip show registrations'
cli 'dialplan show from-linphone'
cli 'dialplan show 2000@from-linphone'
cli 'rtp show settings'
cli 'core show translation paths alaw'
cli 'module show like chan_websocket'
cli 'module show like res_pjsip_endpoint_identifier_ip'
cli 'module show like res_pjsip_acl'
printf '\nMounted configuration (credential lines redacted):\n'
for config in pjsip.conf extensions.conf rtp.conf websocket_client.conf modules.conf; do
    printf '\n/etc/asterisk/%s\n' "$config"
    docker exec "$container" cat "/etc/asterisk/$config" | redact || failures=$((failures + 1))
done
run curl --fail --silent --show-error --max-time 5 http://127.0.0.1:7861/health
printf '\nAudit completed with %s failed/unavailable commands. Review each result; this is not an acceptance test.\n' "$failures"
(( failures == 0 ))
