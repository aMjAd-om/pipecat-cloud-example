"""Render review-only Asterisk fragments. Never connects to or reloads a PBX."""
import argparse
import json
from pathlib import Path
import re
from string import Template


TEMPLATES = Path(__file__).resolve().parents[1] / "deploy" / "omantel"
FIELDS = {
    "number_block_start", "number_block_end", "did", "caller_id",
    "outbound_test_numbers", "outbound_enabled", "secondary_enabled",
    "failover_enabled", "inbound_mode",
}


def number(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError(f"{field} must be an explicitly supplied eight-digit string")
    return value


def validate(settings):
    if not isinstance(settings, dict) or set(settings) != FIELDS:
        raise ValueError("settings must contain exactly the fields in settings.example.json")
    start = number(settings["number_block_start"], "number_block_start")
    end = number(settings["number_block_end"], "number_block_end")
    if start > end:
        raise ValueError("number block start exceeds end")
    for field in ("did", "caller_id"):
        if not start <= number(settings[field], field) <= end:
            raise ValueError(f"{field} is outside the supplied Omantel number block")
    destinations = settings["outbound_test_numbers"]
    if not isinstance(destinations, list):
        raise ValueError("outbound_test_numbers must be a list")
    for destination in destinations:
        number(destination, "outbound test destination")
    if len(set(destinations)) != len(destinations):
        raise ValueError("duplicate outbound test destinations")
    for field in ("outbound_enabled", "secondary_enabled", "failover_enabled"):
        if type(settings[field]) is not bool:
            raise ValueError(f"{field} must be a JSON boolean")
    if settings["outbound_enabled"] and not destinations:
        raise ValueError("outbound calling requires explicit test destinations")
    if settings["failover_enabled"] and not settings["secondary_enabled"]:
        raise ValueError("failover requires the secondary configuration")
    if settings["inbound_mode"] not in ("echo", "ai"):
        raise ValueError("inbound_mode must be echo or ai")


def outbound_route(destination, caller_id, failover):
    # Literal destinations only; no channel-supplied variables select a number.
    lines = [
        f"exten => {destination},1,NoOp(Approved Omantel test destination)",
        " same => n,Set(TIMEOUT(absolute)=120)",
        f" same => n,Set(CALLERID(all)={caller_id})",
        " same => n,Set(CALLERID(num-pres)=allowed_not_screened)",
        ' same => n,GotoIf($["${DEVICE_STATE(PJSIP/omantel-primary)}" = "UNAVAILABLE"]?unavailable)',
        f" same => n,Dial(PJSIP/{destination}@omantel-primary,30,iI)",
    ]
    if failover:
        lines.extend([
            # Retry transport/network failures only. Never retry busy/no-answer,
            # rejection, cancellation, or a completed/answered call.
            ' same => n,GotoIf($[($["${DIALSTATUS}" = "CHANUNAVAIL"] | $["${DIALSTATUS}" = "CONGESTION"]) & ${REGEX("^(3|27|34|38|41|42|44|47)$" ${HANGUPCAUSE})}]?unavailable)',
            " same => n,Hangup()",
            ' same => n(unavailable),GotoIf($["${DEVICE_STATE(PJSIP/omantel-secondary)}" = "UNAVAILABLE"]?failed)',
            f" same => n,Dial(PJSIP/{destination}@omantel-secondary,30,iI)",
            " same => n,Hangup()",
            " same => n(failed),Hangup(38)",
        ])
    else:
        lines.extend([" same => n,Hangup()", " same => n(unavailable),Hangup(38)"])
    return "\n".join(lines)


def render(settings):
    validate(settings)
    peer = Template((TEMPLATES / "peer.conf.in").read_text(encoding="utf-8"))
    peers = []
    for role, sbc in (("primary", "212.72.15.217"), ("secondary", "212.72.15.220")):
        peers.append(peer.substitute(
            endpoint=f"omantel-{role}", aor=f"omantel-{role}-aor",
            identify=f"omantel-{role}-identify", sbc=sbc, caller_id=settings["caller_id"],
        ))
    inbound = [
        "; Generated candidate. No internal or outbound context is included here.",
        "[from-omantel]",
        f'exten => {settings["did"]},1,NoOp(Authorized Omantel DID)',
    ]
    if settings["inbound_mode"] == "echo":
        inbound.extend([
            " same => n,Set(TIMEOUT(absolute)=60)", " same => n,Answer()",
            " same => n,Echo()", " same => n,Hangup()",
        ])
    else:
        # Explicit transfer to the established AI entry point; no context include.
        inbound.append(" same => n,Goto(from-linphone,2000,1)")
    reject = ["exten => _.,1,Hangup(1)", "exten => s,1,Hangup(1)",
              "exten => i,1,Hangup(1)", "exten => t,1,Hangup(16)"]
    inbound.extend(reject)
    inbound.extend(["", "; Private operator context. Do not include from any SIP context.",
                    "[omantel-outbound-test]"])
    if settings["outbound_enabled"]:
        for destination in settings["outbound_test_numbers"]:
            inbound.append(outbound_route(destination, settings["caller_id"],
                                          settings["failover_enabled"]))
    else:
        inbound.append("; Outbound calling disabled: no Dial applications in this context.")
    inbound.extend(reject)
    includes = [
        "; Candidate only. Resolve transport collisions before installation.",
        '#include "omantel-transport.conf"',
        '#include "omantel-primary.conf"',
    ]
    if settings["secondary_enabled"]:
        includes.append('#include "omantel-secondary.conf"')
    else:
        includes.append("; Secondary staged but not loaded. Validate primary first.")
    return {
        "omantel-transport.conf": (TEMPLATES / "transport.conf.in").read_text(encoding="utf-8"),
        "omantel-primary.conf": peers[0],
        "omantel-secondary.conf": peers[1],
        "omantel-pjsip.conf": "\n".join(includes) + "\n",
        "omantel-extensions.conf": "\n".join(inbound) + "\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="new staging directory, never the live /etc/asterisk directory")
    args = parser.parse_args()
    try:
        files = render(json.loads(args.settings.read_text(encoding="utf-8")))
        # Refuse an existing directory so a typo cannot overwrite live configs.
        args.output.mkdir(parents=True, exist_ok=False)
        for name, contents in files.items():
            (args.output / name).write_text(contents, encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot stage Omantel configuration: {exc}\n")
    print(f"Staged {len(files)} files in {args.output}. Nothing installed or reloaded.")


if __name__ == "__main__":
    main()
