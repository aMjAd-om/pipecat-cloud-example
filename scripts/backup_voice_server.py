"""Ubuntu voice recovery backup. Copies files/metadata; never restarts services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".history", ".cache"}


def covered_by(path, source):
    if path == source:
        return True
    return (path.is_relative_to(source)
            and not any(part in EXCLUDED for part in path.relative_to(source).parts))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_source(archive, source):
    """Preserve numeric ownership and dotfiles, including .env; omit rebuildable caches."""
    source = Path(source).resolve()
    prefix = "rootfs/" + source.as_posix().lstrip("/")

    def include(info):
        # Only compare source-relative components: a parent named 'venv' is not
        # grounds for omitting an explicitly requested source directory.
        relative = info.name[len(prefix):].strip("/")
        if any(part in EXCLUDED for part in relative.split("/")):
            return None
        if info.isdev() or info.isfifo():
            return None
        return info

    archive.add(source, arcname=prefix, filter=include)


def command(args, *, required=False, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        record = {"command": args, "exit_code": result.returncode,
                  "stdout": result.stdout, "stderr": result.stderr}
    except OSError as exc:
        record = {"command": args, "exit_code": -1, "stdout": "", "stderr": str(exc)}
    if required and record["exit_code"]:
        raise ValueError(f"Required command failed: {args[0]} {' '.join(args[1:3])}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--pbx-dir", type=Path, default=Path("/home/data_mining/voice-pbx"))
    parser.add_argument("--output-dir", type=Path, required=True, help="private directory OUTSIDE the source directories")
    parser.add_argument("--asterisk-container", default="voice-pbx-asterisk-1")
    parser.add_argument("--agent-container", help="override discovery using the Ubuntu Compose file")
    parser.add_argument("--extra", type=Path, action="append", default=[], help="additional host config or external bind-mount source")
    parser.add_argument("--save-images", action="store_true", help="include exact Asterisk/agent images; needs substantial disk space")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.exit(2, "Run this backup on Ubuntu with sudo.\n")
    os.umask(0o077)
    try:
        app, pbx, output = args.app_dir.resolve(), args.pbx_dir.resolve(), args.output_dir.resolve()
        required_files = [app / ".env", app / "docker-compose.ubuntu.yml", pbx / "Dockerfile",
                          pbx / "compose.yml", *[pbx / "config" / name for name in
                          ("pjsip.conf", "extensions.conf", "rtp.conf", "websocket_client.conf", "logger.conf")],
                          Path("/etc/netplan"), Path("/etc/omantel-signaling.nft"),
                          Path("/etc/systemd/system/omantel-signaling.service"), *args.extra]
        for path in required_files:
            if not path.exists():
                raise ValueError(f"Required recovery source is missing: {path}")
        sources = [app, pbx, Path("/etc/netplan"), Path("/etc/omantel-signaling.nft"),
                   Path("/etc/systemd/system/omantel-signaling.service"), Path("/etc/os-release"),
                   *[p.resolve() for p in args.extra]]
        # Include additional Netplan layers, if present, to preserve effective config.
        sources += [p for p in (Path("/lib/netplan"), Path("/run/netplan")) if p.is_dir()]
        sources = list(dict.fromkeys(p.resolve() for p in sources))
        sources = [p for p in sources if not any(p != q and covered_by(p, q) for q in sources)]
        if any(output.is_relative_to(p) for p in sources):
            raise ValueError("Output directory must be outside every backup source")
        env_target = (app / ".env").resolve()
        if not any(covered_by(env_target, p) for p in sources):
            raise ValueError(f"External .env symlink target is not covered; add --extra {env_target}")
        agent = args.agent_container
        if not agent:
            agent = command(["docker", "compose", "-f", str(app / "docker-compose.ubuntu.yml"),
                             "ps", "-q", "agent"], required=True, cwd=app)["stdout"].strip()
        if not agent or len(agent.splitlines()) != 1:
            raise ValueError("Cannot identify one agent container; supply --agent-container NAME")
        containers = {}
        for role, name in (("asterisk", args.asterisk_container), ("agent", agent)):
            containers[role] = json.loads(command(["docker", "inspect", name], required=True)["stdout"])[0]
        for info in containers.values():
            for mount in info.get("Mounts", []):
                if mount["Type"] == "volume":
                    raise ValueError("A named/anonymous Docker volume needs a separate data backup before this file-based procedure")
                if mount["Type"] == "bind":
                    bound = Path(mount["Source"]).resolve()
                    if not any(covered_by(bound, p) for p in sources):
                        raise ValueError(f"External bind mount is not covered; add --extra {bound}")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        output.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = output / f"voice-recovery-{stamp}.tar.gz"
        if target.exists():
            raise ValueError("Backup filename already exists")
        with tempfile.TemporaryDirectory(prefix=".voice-recovery-", dir=output) as temp:
            work = Path(temp)
            metadata = work / "metadata"
            (metadata / "containers").mkdir(parents=True)
            for role, info in containers.items():
                (metadata / "containers" / f"{role}.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            checks = [
                ["uname", "-a"], ["docker", "compose", "version"],
                ["ip", "-j", "address", "show"], ["ip", "-j", "route", "show", "table", "all"],
                ["ip", "-j", "rule", "show"], ["nft", "list", "ruleset"],
                ["networkctl", "status", "enp11s0", "--no-pager"],
                ["systemctl", "is-enabled", "omantel-signaling.service"],
                ["systemctl", "cat", "omantel-signaling.service"],
                ["git", "-c", f"safe.directory={app}", "-C", str(app), "rev-parse", "HEAD"],
                ["git", "-c", f"safe.directory={app}", "-C", str(app), "status", "--short"],
                ["git", "-c", f"safe.directory={app}", "-C", str(app), "remote", "get-url", "origin"],
            ]
            for role, info in containers.items():
                checks += [["docker", "exec", info["Id"], "id"]]
            for cli in ("core show version", "pjsip show transports", "pjsip show endpoints",
                        "pjsip show contacts", "dialplan show from-omantel", "dialplan show from-linphone"):
                checks.append(["docker", "exec", args.asterisk_container, "asterisk", "-rx", cli])
            records = [command(check) for check in checks]
            (metadata / "checks.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
            (metadata / "backup.json").write_text(json.dumps({
                "created_utc": stamp, "app_dir": str(app), "pbx_dir": str(pbx),
                "sources": [str(p) for p in sources], "images_saved": args.save_images,
                "excluded_components": sorted(EXCLUDED),
                "note": "Private configuration/source snapshot; no service or network state was changed.",
            }, indent=2), encoding="utf-8")
            if args.save_images:
                images = work / "images"
                images.mkdir()
                ids = list(dict.fromkeys(info["Image"] for info in containers.values()))
                command(["docker", "image", "save", "--output", str(images / "docker-images.tar"), *ids], required=True)
            partial = work / "archive.tar.gz"
            with tarfile.open(partial, "w:gz", dereference=False) as archive:
                for source in sources:
                    add_source(archive, source)
                archive.add(metadata, arcname="metadata")
                if args.save_images:
                    archive.add(work / "images", arcname="images")
            os.replace(partial, target)
        digest = sha256_file(target)
        checksum = target.with_name(target.name + ".sha256")
        checksum.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(2, f"Backup failed: {exc}\n")
    print(f"Created private backup: {target}")
    print(f"Checksum: {checksum}")
    print("Contains SIP/Azure credentials and Docker environment metadata. Encrypt and copy OFF this server.")
    print("No restart, reload, network change, SIP probe or telephone call was performed.")


if __name__ == "__main__":
    main()
