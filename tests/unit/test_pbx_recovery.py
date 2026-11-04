"""Offline recovery policy and real file/archive checks; no Docker or Azure calls."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from scripts.backup_voice_server import add_source, covered_by, sha256_file
from scripts.prepare_pbx_recovery import REFERENCE, recovery_files
from scripts.prepare_web_dialer import prepare


ROOT = Path(__file__).resolve().parents[2]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((REFERENCE / "recovery-settings.json").read_text())

    def test_rebuild_has_two_peers_ai_and_linphone_without_outbound(self):
        files = recovery_files(self.settings, "192.168.1.95", "offline_fixture_secret_123")
        sip, plan = files["config/pjsip.conf"], files["config/extensions.conf"]
        for value in ("bind=192.168.1.95:5060", "bind=100.127.29.138:5060",
                      "[omantel-primary]", "[omantel-secondary]", "password=offline_fixture_secret_123"):
            self.assertIn(value, sip)
        self.assertEqual(sip.count("qualify_frequency=30"), 2)
        self.assertNotIn("bind=0.0.0.0", sip)
        self.assertNotIn("#include", sip)
        self.assertNotIn("type=registration", sip)
        self.assertIn("exten => 6000,1,Answer()", plan)
        self.assertIn("Dial(WebSocket/pipecat/c(slin16)f(json),30)", plan)
        self.assertIn("Goto(from-linphone,2000,1)", plan)
        self.assertNotIn("Dial(PJSIP/", plan)
        self.assertIn("name: voice-pbx", files["compose.yml"])
        self.assertIn("network_mode: host", files["compose.yml"])
        for name in ("rtp.conf", "websocket_client.conf", "logger.conf"):
            self.assertIn("config/" + name, files)

    def test_rebuild_rejects_secret_injection_and_enabled_outbound(self):
        for secret in ("short", "fixture-secret-123\ncontext=default", "fixture-secret-123;comment"):
            with self.assertRaises(ValueError):
                recovery_files(self.settings, "192.168.1.95", secret)
        with self.assertRaises(ValueError):
            recovery_files({**self.settings, "outbound_enabled": True}, "192.168.1.95", "offline_fixture_secret_123")
        with self.assertRaises(ValueError):
            recovery_files(self.settings, "0.0.0.0", "offline_fixture_secret_123")

    def test_generated_password_is_private_output_and_existing_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "pbx"
            command = [sys.executable, "-m", "scripts.prepare_pbx_recovery", "--generate-password",
                       "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            sip = (output / "config/pjsip.conf").read_text()
            secret = next(line.split("=", 1)[1] for line in sip.splitlines() if line.startswith("password="))
            self.assertEqual(len(secret), 48)
            self.assertNotIn(secret, result.stdout + result.stderr)
            before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()})
            for data in before.values():
                self.assertNotIn(b"\r\n", data)

    def test_backup_keeps_private_env_and_source_but_excludes_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "app"
            source.mkdir()
            (source / ".env").write_text("TEST_SECRET=backup-fixture")
            (source / "questionnaire.json").write_text('{"id":"fixture"}')
            for name in (".git", ".venv", "node_modules", "__pycache__"):
                (source / name).mkdir()
                (source / name / "excluded").write_text("omit")
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                add_source(archive, source)
            data.seek(0)
            with tarfile.open(fileobj=data, mode="r:gz") as archive:
                files = [member for member in archive.getmembers() if member.isfile()]
                self.assertEqual(len(files), 2)
                env = next(member for member in files if member.name.endswith("/.env"))
                self.assertEqual(archive.extractfile(env).read(), b"TEST_SECRET=backup-fixture")
                self.assertTrue(all(member.name.startswith("rootfs/") for member in files))

    def test_checksum_detects_file_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "backup"
            path.write_bytes(b"original")
            digest = sha256_file(path)
            path.write_bytes(b"modified")
            self.assertNotEqual(digest, sha256_file(path))

    def test_excluded_paths_are_not_reported_as_covered_without_explicit_source(self):
        root = Path("/voice-app")
        secret = root / ".cache" / "private.env"
        self.assertFalse(covered_by(secret, root))
        self.assertTrue(covered_by(secret, secret))
        self.assertTrue(covered_by(root / ".env", root))

    def test_web_reconstruction_restores_private_ami_and_preserves_inbound(self):
        files = recovery_files(self.settings, "192.168.1.95", "offline_fixture_secret_123",
                               "offline_ami_secret_123")
        manager = files["config/manager.conf"]
        self.assertIn("bindaddr=127.0.0.1", manager)
        self.assertIn("secret=offline_ami_secret_123", manager)
        self.assertIn("write=originate,call", manager)
        self.assertIn("./config/manager.conf:/etc/asterisk/manager.conf:ro", files["compose.web.yml"])
        plan = files["config/extensions.conf"]
        self.assertIn("Goto(from-linphone,2000,1)", plan)
        self.assertIn("[web-dialer]", plan)
        self.assertIn("Dial(PJSIP/${EXTEN}@omantel-primary,30,iI)", plan)
        self.assertIn("v(call_id=${WEB_CALL_ID})", plan)
        private_test = plan.split("[omantel-outbound-test]", 1)[1].split("[web-dialer]", 1)[0]
        self.assertNotIn("Dial(", private_test)

    def test_web_staging_preserves_live_files_and_creates_matching_private_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            pbx = Path(temp) / "pbx"
            (pbx / "config").mkdir(parents=True)
            original = "[from-linphone]\nexten => 2000,1,Hangup()\n"
            (pbx / "config/extensions.conf").write_text(original)
            stage = Path(temp) / "stage"
            prepare(pbx, stage)
            self.assertEqual((pbx / "config/extensions.conf").read_text(), original)
            self.assertFalse((pbx / "config/manager.conf").exists())
            settings = dict(line.split("=", 1) for line in (stage / "web-dialer.env").read_text().splitlines()
                            if line and not line.startswith("#"))
            self.assertIn("secret=" + settings["ASTERISK_AMI_SECRET"], (stage / "manager.conf").read_text())
            self.assertGreaterEqual(len(settings["DIALER_PASSWORD"]), 16)
            self.assertTrue((stage / "extensions.conf").read_text().startswith(original))
            self.assertEqual((stage / "extensions-before-web.conf").read_text(), original)
            with self.assertRaises(FileExistsError):
                prepare(pbx, stage)
            (pbx / "config/extensions.conf").write_text(original + "\n[web-dialer]\n")
            with self.assertRaises(ValueError):
                prepare(pbx, Path(temp) / "second")


if __name__ == "__main__":
    unittest.main()
