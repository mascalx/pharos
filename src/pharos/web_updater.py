"""Rootless Ubuntu web updater.

The worker is launched as a separate systemd user unit, outside the PHAROS
service cgroup, so it survives the application restart. Only a verified
fast-forward of the administrator's preconfigured official Git clone is used.
No user-supplied URL, command, archive or filesystem path is executed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from .config import load_config
from .storage import check_data_volume
from .update_check import _read_installation, _official_origin, _command, SHA

STATE_DIR = Path.home() / ".local/share/pharos"
STATE_FILE = STATE_DIR / "web-update.json"
UNIT = "pharos-update-worker.service"
ACTIVE = frozenset(("queued", "checking", "backup", "applying", "verifying", "rollback"))
PROTECTED_FILES = (
    "src/pharos/knowledge/", "src/pharos/knowledge_jobs.py",
    "src/pharos/download_jobs.py", "src/pharos/kiwix/",
    "src/pharos/kiwix_runtime.py", "src/pharos/kiwix_proxy.py",
    "src/pharos/storage.py", "src/pharos/config.py",
    "src/pharos/config_edit.py", "src/pharos/ubuntu_installer.py",
)
# Previously every change to a protected Core subsystem required SSH.
# Starting with 0.7, publisher-reviewed, explicitly migration-free Core
# releases may install from the UI. Unknown/irreversible data migrations stay
# blocked because SQLite-only updater backups do not cover live Qdrant/ZIM.
CORE_COMPAT_MANIFEST = "release/core-upgrade.json"
MAX_COMPAT_BYTES = 16 * 1024
MAX_INSTALL_SECONDS = 900
class UpdateError(ValueError):
    pass


def _run(args: list[str], *, timeout=120, cwd=None) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes -oConnectTimeout=10"
    try:
        result = subprocess.run(args, cwd=cwd, env=env, input="",
                                capture_output=True, text=True, timeout=timeout,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("command_unavailable_or_timed_out") from exc
    if result.returncode:
        # Neither stderr nor stdout may be exposed: Git credential helpers
        # and pip can emit private remote URLs or confidential local paths.
        raise UpdateError("update_command_failed")
    return result.stdout.strip()


def _write(state: dict):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_FILE.is_symlink():
        raise UpdateError("unsafe_update_state")
    fd, name = tempfile.mkstemp(prefix=".web-update-", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, STATE_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read() -> dict:
    if STATE_FILE.is_symlink():
        raise UpdateError("unsafe_update_state")
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _unit_running() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", UNIT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=4, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def snapshot() -> dict:
    data = _read()
    stage = data.get("status", "idle")
    if stage in ACTIVE and not _unit_running():
        if int(time.time()) - int(data.get("updated_at", 0)) > 50:
            # A hard-killed worker cannot leave PHAROS permanently locked.
            data = dict(data, status="interrupted", phase="interrupted",
                        error="updater_interrupted")
            _write(data)
    # No absolute paths, stderr or secrets in the API response.
    return {k: data.get(k) for k in
            ("status", "phase", "target_sha", "base_sha",
             "started_at", "updated_at", "error")}


def maintenance() -> bool:
    return snapshot().get("status") in ACTIVE


def _stage(status: str, phase: str, *, error=""):
    data = _read()
    data.update(status=status, phase=phase, updated_at=int(time.time()),
                error=error)
    _write(data)


def _ubuntu() -> bool:
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return (sys.platform == "linux"
            and platform.machine() == "x86_64"
            and "ID=ubuntu" in lines
            and os.geteuid() != 0)


def _preflight(marker: Path, *, expected: str):
    if not _ubuntu():
        raise UpdateError("updater_requires_ubuntu_x86_64")
    if not SHA.fullmatch(expected):
        raise UpdateError("invalid_target_sha")
    checkout, installed = _read_installation(marker)
    if not _official_origin(_command(checkout, "remote", "get-url", "origin")):
        raise UpdateError("untrusted_repository")
    if _command(checkout, "rev-parse", "HEAD") != installed:
        raise UpdateError("checkout_not_installed_revision")
    if _command(checkout, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError("checkout_has_local_changes")
    if installed == expected:
        raise UpdateError("already_current")
    return checkout, installed


def _active_jobs(jobs, rag) -> bool:
    if rag is not None and rag.reindex_in_progress:
        return True
    return any(j.get("status") in ("queued", "running", "cancelling")
               for j in jobs.list())


def _active_job_reason(config) -> str:
    """Inspect the entire durable queue, not the limited notification list."""
    path = config.storage.database_dir / "pharos-jobs.sqlite3"
    if not path.is_file() or path.is_symlink():
        return "update_jobs_unavailable"
    try:
        from contextlib import closing
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=5)) as db:
            kinds = {row[0] for row in db.execute(
                "SELECT DISTINCT kind FROM jobs WHERE status IN ('queued','running','cancelling')"
            )}
        for kind, reason in (("reindex-rag", "update_blocked_reindex"),
                             ("ingest-document", "update_blocked_ingestion"),
                             ("build-lexical", "update_blocked_lexical"),
                             ("index-map", "update_blocked_map_index"),
                             ("download-map", "update_blocked_download"),
                             ("download-zim", "update_blocked_download")):
            if kind in kinds:
                return reason
        return "background_jobs_active" if kinds else ""
    except (OSError, sqlite3.Error):
        return "update_jobs_unavailable"


def _active_jobs_on_disk(config) -> bool:
    return bool(_active_job_reason(config))


def _require_idle(config, jobs, rag):
    if rag is not None and rag.reindex_in_progress:
        raise UpdateError("update_blocked_reindex")
    reason = _active_job_reason(config)
    if reason:
        raise UpdateError(reason)
    if _active_jobs(jobs, rag):
        raise UpdateError("background_jobs_active")


def start(checker, config, jobs, rag, expected: str) -> dict:
    if maintenance():
        raise UpdateError("update_already_in_progress")
    checkout, installed = _preflight(checker.marker_path, expected=expected)
    if not check_data_volume(config.storage).available:
        raise UpdateError("data_volume_unavailable")
    _require_idle(config, jobs, rag)
    if not Path(sys.executable).is_file():
        raise UpdateError("updater_python_unavailable")
    _run(["systemctl", "--user", "is-active", "--quiet", "pharos.service"], timeout=5)
    # Recheck the remote revision now; never trust a stale browser SHA.
    # A network error is safe: no source files have been modified.
    from .update_check import _probe
    available = _probe(checker.marker_path)
    if available["remote_sha"] != expected:
        raise UpdateError("update_revision_changed")
    # The remote probe may take seconds: re-evaluate live activity before
    # entering maintenance mode, instead of relying on the earlier snapshot.
    _require_idle(config, jobs, rag)
    state = dict(status="queued", phase="launching", base_sha=installed,
                 target_sha=expected, started_at=int(time.time()),
                 updated_at=int(time.time()), error="")
    _write(state)
    try:
        _run(["systemd-run", "--user", "--unit=pharos-update-worker",
              "--collect", "--property=Type=exec", sys.executable,
              "-m", "pharos.web_updater", "--target", expected,
              "--config", str(config.source)], timeout=25)
    except UpdateError:
        _stage("failed", "launch", error="updater_launch_failed")
        raise UpdateError("updater_launch_failed") from None
    return snapshot()


def _backup(config, name: str) -> Path:
    root = STATE_DIR / "update-backups"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / name
    directory.mkdir(mode=0o700)
    # Configuration and secrets never leave the VM.
    for original in (config.source, config.security.secrets_file):
        if original.is_file() and not original.is_symlink():
            shutil.copy2(original, directory / original.name)
    source = config.storage.database_dir
    db_dir = directory / "sqlite"
    db_dir.mkdir(mode=0o700)
    for db in source.glob("*.sqlite3"):
        if db.is_symlink() or not db.is_file():
            raise UpdateError("unsafe_database_file")
        from contextlib import closing
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=20)) as src, \
             closing(sqlite3.connect(db_dir / db.name)) as dst:
            src.backup(dst, pages=256, sleep=0.1)
            dst.commit()
    return directory


def _restore_sqlite(config, backup: Path):
    # Restore only after systemd has stopped PHAROS. Atomic replacement
    # protects against interrupted local copies.
    for original in (config.source, config.security.secrets_file):
        saved = backup / original.name
        if saved.is_file():
            if original.is_symlink() or saved.is_symlink():
                raise UpdateError("unsafe_restore_target")
            tmp_original = original.with_name("." + original.name + ".restore")
            shutil.copy2(saved, tmp_original)
            os.replace(tmp_original, original)
    for copy in (backup / "sqlite").glob("*.sqlite3"):
        target = config.storage.database_dir / copy.name
        if target.is_symlink():
            raise UpdateError("unsafe_restore_target")
        tmp = target.with_name("." + target.name + ".restore")
        shutil.copy2(copy, tmp)
        # The application service has been stopped. Do not allow an old
        # SQLite WAL to replay transactions over the restored snapshot.
        for sidecar in ("-wal", "-shm"):
            candidate = target.with_name(target.name + sidecar)
            if candidate.is_symlink():
                raise UpdateError("unsafe_restore_target")
            candidate.unlink(missing_ok=True)
        os.replace(tmp, target)


def _installer(checkout: Path, config):
    script = checkout / "scripts/install-ubuntu.sh"
    # Existing installations already have a venv + third-party binaries.
    # Reconfiguring the systemwide Nginx proxy would require sudo; that is
    # deliberately not permitted from the web UI.
    _run(["bash", str(script), "--skip-lan-proxy",
          "--config", str(config.source)], timeout=MAX_INSTALL_SECONDS)


def _core_update_declared_safe(checkout: Path, expected: str, changed: list[str]) -> bool:
    """Read the verified target commit, not a file from the running checkout.

    Migration safety must be explicit. A manifest with no migration and no
    third-party data binary changes is eligible for Core hot-upgrade.
    """
    if CORE_COMPAT_MANIFEST not in changed:
        return False
    try:
        contents = _command(checkout, "show", f"{expected}:{CORE_COMPAT_MANIFEST}")
        if len(contents.encode("utf-8")) > MAX_COMPAT_BYTES:
            return False
        record = json.loads(contents)
    except (ValueError, UnicodeError, OSError, UpdateError):
        return False
    return (isinstance(record, dict) and record.get("schema") == 1
            and record.get("update_mode") == "core_reinstall"
            and record.get("data_migrations") == "none"
            and record.get("third_party_binary_changes") is False
            and record.get("requires_manual_steps") is False)


def _ensure_same_target(checkout: Path, expected: str, base_sha: str):
    # Fetch only origin/main and reject non-fast-forward updates. The intended
    # SHA must still be the tip, preventing a different moving branch from
    # being applied after the user's confirmation.
    _run(["git", "-C", str(checkout), "fetch", "--no-tags", "origin", "main"],
         timeout=180)
    fetched = _command(checkout, "rev-parse", "FETCH_HEAD")
    if fetched != expected:
        raise UpdateError("update_revision_changed")
    _run(["git", "-C", str(checkout), "merge-base", "--is-ancestor",
          base_sha, expected], timeout=15)
    names = _command(checkout, "diff", "--name-only", base_sha, expected).splitlines()
    # A Core change is no longer automatically forbidden. It must carry an
    # audited, commit-pinned migration-free manifest. The rest of the worker
    # still checks clean fast-forward, jobs, storage, install, health and
    # rollback. Future destructive migrations require a separate migration
    # and complete Qdrant restore design, not a dangerous bypass.
    if (any(any(path.startswith(prefix) for prefix in PROTECTED_FILES)
            for path in names)
            and not _core_update_declared_safe(checkout, expected, names)):
        raise UpdateError("update_requires_manual_review")


def run_worker(target: str, config_path: str):
    import fcntl as locklib
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE_DIR / "web-update.lock").open("a+") as lock:
        try:
            locklib.flock(lock.fileno(), locklib.LOCK_EX | locklib.LOCK_NB)
        except BlockingIOError:
            return
        data = _read()
        if data.get("target_sha") != target or data.get("status") not in ACTIVE:
            return
        checkout = None
        old = data.get("base_sha")
        changed = False
        backed_up = None
        config = None
        try:
            _stage("checking", "preflight")
            marker = STATE_DIR / "update-source.json"
            checkout, installed = _preflight(marker, expected=target)
            if installed != old:
                raise UpdateError("installed_revision_changed")
            config = load_config(Path(config_path))
            if not check_data_volume(config.storage).available:
                raise UpdateError("data_volume_unavailable")
            _ensure_same_target(checkout, target, old)
            reason = _active_job_reason(config)
            if reason:
                raise UpdateError(reason)
            _stage("backup", "sqlite_and_config")
            backed_up = _backup(config, str(data["started_at"]) + "-" + old[:12])
            _stage("applying", "fast_forward")
            _run(["git", "-C", str(checkout), "merge", "--ff-only", target],
                 timeout=50)
            changed = True
            _stage("applying", "installer")
            _installer(checkout, config)
            _stage("verifying", "service_and_revision")
            marker_checkout, marker_sha = _read_installation(marker)
            if marker_checkout != checkout or marker_sha != target:
                raise UpdateError("installed_revision_not_confirmed")
            _run(["systemctl", "--user", "is-active", "--quiet", "pharos.service"],
                 timeout=10)
            _stage("completed", "complete")
        except Exception as exc:
            # Use constant error codes; NEVER expose exception text or paths.
            error = str(exc) if isinstance(exc, UpdateError) else "updater_failed"
            if not changed or not checkout or not config:
                _stage("failed", "before_install", error=error)
                return
            _stage("rollback", "restoring")
            try:
                _run(["systemctl", "--user", "stop", "pharos.service"],
                     timeout=35)
                if backed_up is not None:
                    _restore_sqlite(config, backed_up)
                # Only the clean branch we just moved may be reset.
                if _command(checkout, "rev-parse", "HEAD") != target:
                    raise UpdateError("rollback_checkout_changed")
                if _command(checkout, "status", "--porcelain", "--untracked-files=no"):
                    raise UpdateError("rollback_checkout_modified")
                _run(["git", "-C", str(checkout), "reset", "--hard", old],
                     timeout=60)
                _installer(checkout, config)
                _stage("rolled_back", "previous_version_restored", error=error)
            except Exception:
                _stage("rollback_failed", "manual_recovery_required",
                       error="rollback_failed")
        finally:
            locklib.flock(lock.fileno(), locklib.LOCK_UN)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    if not SHA.fullmatch(args.target):
        raise SystemExit(2)
    run_worker(args.target, args.config)


if __name__ == "__main__":
    main()
