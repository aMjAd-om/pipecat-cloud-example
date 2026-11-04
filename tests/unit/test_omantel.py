"""Offline policy checks for the carrier fragments; no SIP or billable calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.configure_omantel import render, validate
from scripts.stage_omantel_existing import candidates


ROOT = Path(__file__).resolve().parents[2]


class OmantelTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / "deploy/omantel/settings.example.json").read_text())

    def test_supplied_pilot_is_restricted_and_secondary_is_staged(self):
        files = render(self.settings)
        inbound = files["omantel-extensions.conf"]
        self.assertIn("exten => 21130800,1,", inbound)
        self.assertIn("Echo()", inbound)
        self.assertNotIn("Dial(", inbound)
        self.assertNotIn("include =>", inbound)
        self.assertNotIn('#include "omantel-secondary.conf"', files["omantel-pjsip.conf"])
        self.assertIn("[omantel-secondary]", files["omantel-secondary.conf"])
        self.assertNotIn("9999421", "".join(files.values()))

    def test_reject_invalid_or_unassigned_numbers_and_injected_config(self):
        for field, value in (
            ("did", "21130900"), ("caller_id", "9999421"),
            ("caller_id", "21130800\ncontext=default"),
            ("did", 21130800), ("did", "٢١١٣٠٨٠٠"),
            ("number_block_end", "21130799"),
            ("outbound_test_numbers", ["12345678&other"]),
            ("outbound_enabled", "false"), ("secondary_enabled", 1),
            ("inbound_mode", "arbitrary"),
        ):
            with self.subTest(field=field, value=value):
                candidate = {**self.settings, field: value}
                with self.assertRaises(ValueError):
                    render(candidate)
        with self.assertRaises(ValueError):
            validate({**self.settings, "typo": True})

    def test_both_peer_policies_and_independent_heartbeat(self):
        files = render(self.settings)
        for role, ip in (("primary", "212.72.15.217"), ("secondary", "212.72.15.220")):
            peer = files[f"omantel-{role}.conf"]
            for option in ("allow=alaw:20", "disallow=all", "dtmf_mode=inband",
                           "use_ptime=no", "transport=omantel-udp", "identify_by=ip",
                           "qualify_frequency=30", "qualify_2xx_only=yes",
                           "send_pai=yes", "send_rpid=no", "max_contacts=0",
                           "bind_rtp_to_media_address=yes", "media_address=100.127.29.138",
                           f"permit={ip}/32", f"match={ip}/32", f"contact=sip:{ip}:5060"):
                self.assertIn(option + "\n", peer)
            for forbidden in ("type=registration", "type=auth", "ulaw", "rfc4733"):
                self.assertNotIn(forbidden, peer)

    def test_outbound_requires_allowlist_and_failover_requires_secondary(self):
        with self.assertRaises(ValueError):
            render({**self.settings, "outbound_enabled": True})
        with self.assertRaises(ValueError):
            render({**self.settings, "failover_enabled": True})
        # Synthetic destination is only an offline fixture, never a deploy default.
        candidate = {**self.settings, "outbound_test_numbers": ["12345678"],
                     "outbound_enabled": True}
        plan = render(candidate)["omantel-extensions.conf"]
        self.assertIn("Dial(PJSIP/12345678@omantel-primary,30,iI)", plan)
        self.assertNotIn("@omantel-secondary", plan)
        self.assertNotIn("${EXTEN}", plan)
        candidate.update(secondary_enabled=True, failover_enabled=True)
        files = render(candidate)
        plan = files["omantel-extensions.conf"]
        self.assertLess(plan.index("@omantel-primary"), plan.index("@omantel-secondary"))
        self.assertNotIn('"${DIALSTATUS}" = "BUSY"', plan)
        self.assertNotIn('"${DIALSTATUS}" = "NOANSWER"', plan)
        self.assertIn('^(3|27|34|38|41|42|44|47)$', plan)
        self.assertIn('#include "omantel-secondary.conf"', files["omantel-pjsip.conf"])

    def test_ai_uses_existing_entry_point_without_opening_context(self):
        plan = render({**self.settings, "inbound_mode": "ai"})["omantel-extensions.conf"]
        self.assertIn("Goto(from-linphone,2000,1)", plan)
        self.assertNotIn("include =>", plan)
        self.assertNotIn("Echo()", plan)

    def test_checked_in_candidates_match_the_supplied_settings(self):
        for name, contents in render(self.settings).items():
            self.assertEqual((ROOT / "deploy/omantel/staged" / name).read_text(), contents)

    def test_existing_candidates_preserve_authentication_and_linphone_routing(self):
        sip = ("[transport-udp]\ntype=transport\nprotocol=udp\nbind=0.0.0.0:5060\n\n"
               "[1001]\ntype=endpoint\ntransport=transport-udp\nauth=auth1001\n"
               "[auth1001]\ntype=auth\npassword=fixture-private-secret\n")
        plan = "[from-linphone]\nexten => 2000,1,Dial(WebSocket/pipecat/c(slin16)f(json),30)\n"
        files = candidates(sip, plan, self.settings, "192.168.1.95")
        self.assertTrue(files["pjsip.conf"].startswith(
            sip.replace("bind=0.0.0.0:5060", "bind=192.168.1.95:5060")))
        self.assertTrue(files["extensions.conf"].startswith(plan))
        self.assertIn("password=fixture-private-secret", files["pjsip.conf"])
        self.assertIn("bind=100.127.29.138:5060", files["pjsip.conf"])
        self.assertNotIn("#include", files["pjsip.conf"])
        self.assertNotIn("[omantel-secondary]", files["pjsip.conf"])

    def test_existing_candidates_refuse_duplicates_unknown_binds_or_unsafe_stage(self):
        sip = "[transport-udp]\ntype=transport\nprotocol=udp\nbind=0.0.0.0:5060\n"
        for candidate in (sip + "[omantel-primary]\n", sip + sip,
                          sip.replace("0.0.0.0", "192.168.1.95")):
            with self.assertRaises(ValueError):
                candidates(candidate, "", self.settings, "192.168.1.95")
        with self.assertRaises(ValueError):
            candidates(sip, "[from-omantel]\n", self.settings, "192.168.1.95")
        with self.assertRaises(ValueError):
            candidates(sip, "", {**self.settings, "inbound_mode": "ai"}, "192.168.1.95")

    def test_existing_cli_only_writes_private_review_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "pbx" / "config"
            source.mkdir(parents=True)
            originals = {
                "pjsip.conf": "[transport-udp]\ntype=transport\nprotocol=udp\nbind=0.0.0.0:5060\n",
                "extensions.conf": "[from-linphone]\nexten => 2000,1,Hangup()\n",
            }
            for name, data in originals.items():
                (source / name).write_text(data)
            before = {name: (source / name).read_bytes() for name in originals}
            output = base / "review"
            command = [sys.executable, "-m", "scripts.stage_omantel_existing", "--pbx-dir",
                       str(source.parent), "--settings", str(ROOT / "deploy/omantel/settings.example.json"),
                       "--linphone-ip", "192.168.1.95", "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, {name: (source / name).read_bytes() for name in originals})
            for name, data in before.items():
                self.assertEqual((output / ("original-" + name)).read_bytes(), data)
            self.assertEqual(len(json.loads((output / "source-sha256.json").read_text())), 2)
            self.assertNotEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 0)

    def test_cli_cannot_overwrite_existing_config_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "staged"
            command = [sys.executable, "-m", "scripts.configure_omantel", "--settings",
                       str(ROOT / "deploy/omantel/settings.example.json"), "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = {p.name: p.read_bytes() for p in output.iterdir()}
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(original, {p.name: p.read_bytes() for p in output.iterdir()})
            for data in original.values():
                self.assertNotIn(b"\r\n", data)


if __name__ == "__main__":
    unittest.main()
