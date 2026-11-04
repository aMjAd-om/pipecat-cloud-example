"""Create a complete private PBX recovery directory. Never installs or starts it."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import secrets
from string import Template

from scripts.configure_omantel import render


REFERENCE = Path(__file__).resolve().parents[1] / "deploy" / "asterisk"
OMANTEL = REFERENCE.parent / "omantel"


def recovery_files(settings, lan_ip, sip_password, ami_secret=None):
    address = ipaddress.IPv4Address(lan_ip)
    if (not address.is_private or address.is_loopback or address.is_link_local
            or address.is_unspecified or address.is_reserved or address.is_multicast):
        raise ValueError("LAN address must be the confirmed private IPv4 address")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", sip_password):
        raise ValueError("SIP password must have 16-128 ASCII letters, digits, underscores or hyphens")
    fragments = render(settings)
    if settings["outbound_enabled"] or settings["failover_enabled"]:
        raise ValueError("Recovery does not enable outbound calling or untested failover")
    local = Template((REFERENCE / "pjsip-local.conf.in").read_text(encoding="utf-8"))
    pjsip = local.substitute(lan_ip=str(address), sip_password=sip_password)
    pjsip += "\n" + fragments["omantel-transport.conf"] + "\n" + fragments["omantel-primary.conf"]
    if settings["secondary_enabled"]:
        pjsip += "\n" + fragments["omantel-secondary.conf"]
    result = {
        "config/pjsip.conf": pjsip,
        "config/extensions.conf": (REFERENCE / "extensions-local.conf").read_text(encoding="utf-8")
            + "\n" + fragments["omantel-extensions.conf"],
        "recovery-settings.json": json.dumps(settings, indent=2) + "\n",
    }
    for name in ("Dockerfile", ".dockerignore", "compose.yml", "rtp.conf", "websocket_client.conf", "logger.conf"):
        target = name if name in ("Dockerfile", ".dockerignore", "compose.yml") else "config/" + name
        result[target] = (REFERENCE / name).read_text(encoding="utf-8")
    for name in ("90-omantel.yaml.proposed", "omantel-signaling.nft.proposed",
                 "omantel-signaling.service.proposed"):
        result["host/" + name] = (OMANTEL / name).read_text(encoding="utf-8")
    if ami_secret is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", ami_secret):
            raise ValueError("AMI secret must have 16-128 ASCII letters, digits, underscores or hyphens")
        result["config/manager.conf"] = Template((REFERENCE / "manager.conf.in").read_text()).substitute(ami_secret=ami_secret)
        result["config/extensions.conf"] += "\n" + (REFERENCE / "extensions-web.conf").read_text()
        result["compose.web.yml"] = (REFERENCE / "compose.web.yml").read_text()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory, never an existing live PBX")
    parser.add_argument("--lan-ip", default="192.168.1.95")
    parser.add_argument("--settings", type=Path, default=REFERENCE / "recovery-settings.json")
    parser.add_argument("--ami-secret-file", type=Path, help="restore the web dialer using its private AMI secret")
    password = parser.add_mutually_exclusive_group(required=True)
    password.add_argument("--password-file", type=Path, help="existing Linphone password from private storage")
    password.add_argument("--generate-password", action="store_true", help="rotate Linphone password on recovery")
    args = parser.parse_args()
    try:
        secret = (args.password_file.read_text(encoding="utf-8").strip()
                  if args.password_file else secrets.token_hex(24))
        ami_secret = args.ami_secret_file.read_text(encoding="utf-8").strip() if args.ami_secret_file else None
        files = recovery_files(json.loads(args.settings.read_text(encoding="utf-8")), args.lan_ip, secret, ami_secret)
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, data in files.items():
            target = args.output / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.touch(mode=0o600, exist_ok=False)
            target.write_text(data, encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot prepare recovery directory: {exc}\n")
    print(f"Complete private PBX files created in {args.output}; nothing installed or started.")
    print("Keep config/pjsip.conf private. Set container read permissions as documented before startup.")
    if args.generate_password:
        print("A NEW Linphone password is stored in config/pjsip.conf; update the phone when restoring.")


if __name__ == "__main__":
    main()
