"""Stage private web dialer PBX files and credentials; never install or call."""
import argparse
from pathlib import Path
import secrets
from string import Template

REFERENCE = Path(__file__).resolve().parents[1] / "deploy" / "asterisk"


def prepare(pbx_dir, output):
    original = (pbx_dir / "config" / "extensions.conf").read_text(encoding="utf-8")
    if "[web-dialer]" in original or "[from-web-agent]" in original:
        raise ValueError("Web dialer contexts already exist; review instead of appending duplicates")
    if (pbx_dir / "config" / "manager.conf").exists():
        raise ValueError("Existing host manager.conf must be reviewed and merged manually")
    ami_secret, password = secrets.token_hex(32), secrets.token_hex(24)
    files = {
        "extensions-before-web.conf": original,
        "extensions.conf": original.rstrip() + "\n\n" + (REFERENCE / "extensions-web.conf").read_text(),
        "manager.conf": Template((REFERENCE / "manager.conf.in").read_text()).substitute(ami_secret=ami_secret),
        "compose.web.yml": (REFERENCE / "compose.web.yml").read_text(),
        "web-dialer.env": (
            "\n# Web operator dialer (private credentials)\n"
            "DIALER_USERNAME=operator\n" f"DIALER_PASSWORD={password}\n"
            "ASTERISK_AMI_USERNAME=web-dialer\n" f"ASTERISK_AMI_SECRET={ami_secret}\n"
            "ASTERISK_AMI_PORT=5038\nOUTBOUND_ENABLED=true\nOUTBOUND_ALLOWED_NUMBERS=\n"
            "TELEPHONY_ENABLED=true\n"
        ),
    }
    output.mkdir(mode=0o700, exist_ok=False)
    for name, contents in files.items():
        path = output / name
        path.touch(mode=0o600, exist_ok=False)
        path.write_text(contents, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbx-dir", type=Path, default=Path("/home/data_mining/voice-pbx"))
    parser.add_argument("--output", type=Path, required=True, help="a new private staging directory")
    args = parser.parse_args()
    try:
        prepare(args.pbx_dir, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot stage web dialer: {exc}\n")
    print(f"Staged private PBX candidates and operator credentials in {args.output}.")
    print("Nothing installed, restarted, or called. Follow docs/WEB_DIALER.md.")


if __name__ == "__main__":
    main()
