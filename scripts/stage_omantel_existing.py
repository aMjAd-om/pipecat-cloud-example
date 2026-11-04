"""Prepare private primary-only PBX candidate files; never install or restart."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re

from scripts.configure_omantel import render


def candidates(pjsip, extensions, settings, linphone_ip):
    address = ipaddress.IPv4Address(linphone_ip)
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError("Linphone bind must be the confirmed private LAN IPv4 address")
    fragments = render(settings)
    if (settings["secondary_enabled"] or settings["failover_enabled"]
            or settings["outbound_enabled"] or settings["inbound_mode"] != "echo"):
        raise ValueError("Initial staging requires primary only, Echo, outbound and failover disabled")
    if re.search(r"(?m)^\[omantel-", pjsip):
        raise ValueError("Omantel sections already exist; review rather than appending duplicates")
    if re.search(r"(?m)^\[(from-omantel|omantel-outbound-test)\]", extensions):
        raise ValueError("Omantel contexts already exist; review rather than appending duplicates")
    pattern = re.compile(r"(?ms)^\[transport-udp\][ \t]*(?:;[^\n]*)?\n(.*?)(?=^\[|\Z)")
    sections = list(pattern.finditer(pjsip))
    if len(sections) != 1:
        raise ValueError("Expected exactly one existing transport-udp section")
    section = sections[0]
    body = section.group(1)
    for option in ("type=transport", "protocol=udp"):
        if not re.search(r"(?m)^" + option + r"[ \t]*(?:;[^\n]*)?$", body):
            raise ValueError("Existing transport differs from the audited UDP configuration")
    body, count = re.subn(
        r"(?m)(^[ \t]*bind[ \t]*=[ \t]*)0\.0\.0\.0:5060(?=[ \t]*(?:;[^\n]*)?$)",
        lambda match: match.group(1) + str(address) + ":5060", body,
    )
    if count != 1:
        raise ValueError("Expected one wildcard UDP/5060 bind in transport-udp")
    updated = pjsip[:section.start(1)] + body + pjsip[section.end(1):]
    return {
        "pjsip.conf": updated.rstrip() + "\n\n; Omantel primary pilot\n"
            + fragments["omantel-transport.conf"] + "\n" + fragments["omantel-primary.conf"],
        "extensions.conf": extensions.rstrip() + "\n\n" + fragments["omantel-extensions.conf"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbx-dir", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--linphone-ip", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new private review directory")
    args = parser.parse_args()
    try:
        source = (args.pbx_dir / "config").resolve()
        if args.output.resolve().is_relative_to(source):
            raise ValueError("Review output must be outside the live config directory")
        originals = {name: (source / name).read_bytes()
                     for name in ("pjsip.conf", "extensions.conf")}
        settings = json.loads(args.settings.read_text(encoding="utf-8"))
        files = candidates(*(originals[name].decode("utf-8").replace("\r\n", "\n")
                             for name in ("pjsip.conf", "extensions.conf")),
                           settings, args.linphone_ip)
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, data in originals.items():
            target = args.output / ("original-" + name)
            target.touch(mode=0o600, exist_ok=False)
            target.write_bytes(data)
        for name, data in files.items():
            target = args.output / name
            target.touch(mode=0o600, exist_ok=False)
            target.write_text(data, encoding="utf-8", newline="\n")
        manifest = {str(source / name): hashlib.sha256(data).hexdigest()
                    for name, data in originals.items()}
        (args.output / "source-sha256.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot prepare PBX candidates: {exc}\n")
    print(f"Private candidate files and original snapshots prepared in {args.output}.")
    print("SIP credentials are retained in these files; do not share their contents.")
    print("Live files are unchanged. No reload, restart, firewall or network operation was performed.")


if __name__ == "__main__":
    main()
