"""Safe web-update workflow: no actual Git, systemd or installations."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos import web_updater


OLD = "a" * 40
NEW = "b" * 40
HEAD = {"X-Pharos-Request": "1"}


def test_read_only_api_and_confirmed_start(prepared, monkeypatch):
    cfg = __import__("pharos.config", fromlist=["load_config"]).load_config(prepared)
    monkeypatch.setattr(web_updater, "snapshot", lambda: {
        "status": "idle", "phase": None})
    calls = []

    def start_fake(checker, config, jobs, rag, target):
        calls.append(target)
        return {"status": "queued", "target_sha": target, "phase": "launching"}

    monkeypatch.setattr(web_updater, "start", start_fake)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/updates/installation").json()["status"] == "idle"
        assert client.post("/api/updates/start",
                           json={"target_sha": NEW}).status_code == 403
        assert client.post("/api/updates/start", headers=HEAD,
                           json={"target_sha": "not-a-sha"}).status_code == 422
        response = client.post("/api/updates/start", headers=HEAD,
                               json={"target_sha": NEW})
        assert response.status_code == 202
        assert response.json()["target_sha"] == NEW
        assert calls == [NEW]


def test_updates_reject_ordinary_mutations_during_maintenance(prepared, monkeypatch):
    cfg = __import__("pharos.config", fromlist=["load_config"]).load_config(prepared)
    monkeypatch.setattr(web_updater, "maintenance", lambda: True)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/updates/installation").status_code == 200
        result = client.post("/api/jobs/kiwix/zim", headers=HEAD,
                             json={"url": "https://download.kiwix.org/example.zim"})
        assert result.status_code == 423
        assert result.json()["detail"] == "update_in_progress"


def test_preflight_refuses_dirty_checkout_and_unauthorized_origin(tmp_path, monkeypatch):
    checkout = tmp_path / "clone"
    checkout.mkdir()
    marker = tmp_path / "marker.json"
    monkeypatch.setattr(web_updater, "_ubuntu", lambda: True)
    monkeypatch.setattr(web_updater, "_read_installation", lambda _: (checkout, OLD))
    responses = {
        ("remote", "get-url", "origin"): "https://github.com/mascalx/pharos.git",
        ("rev-parse", "HEAD"): OLD,
        ("status", "--porcelain", "--untracked-files=no"): " M README.md",
    }
    monkeypatch.setattr(web_updater, "_command",
                        lambda _, *args: responses[args])
    with pytest.raises(web_updater.UpdateError, match="checkout_has_local_changes"):
        web_updater._preflight(marker, expected=NEW)
    responses[("remote", "get-url", "origin")] = "https://attacker.example/a.git"
    with pytest.raises(web_updater.UpdateError, match="untrusted_repository"):
        web_updater._preflight(marker, expected=NEW)


def test_active_jobs_block_updates():
    idle = SimpleNamespace(list=lambda: [])
    busy = SimpleNamespace(list=lambda: [
        {"kind": "download-zim", "status": "queued"}])
    assert web_updater._active_jobs(busy, None)
    assert web_updater._active_jobs(idle, SimpleNamespace(reindex_in_progress=True))
    assert not web_updater._active_jobs(idle, None)


def test_worker_refuses_sensitive_source_migrations(tmp_path, monkeypatch):
    commands = []

    def fake_run(args, **kw):
        commands.append(args)
        return ""

    monkeypatch.setattr(web_updater, "_run", fake_run)
    monkeypatch.setattr(web_updater, "_command", lambda _, *args:
                        NEW if args[0] == "rev-parse" else
                        "src/pharos/knowledge/rag.py")
    with pytest.raises(web_updater.UpdateError, match="manual_review"):
        web_updater._ensure_same_target(tmp_path, NEW, OLD)
    assert any("fetch" in command for command in commands)


def test_launcher_passes_only_expected_sha_and_authoritative_config(tmp_path, monkeypatch):
    checkout = tmp_path / "source"
    checkout.mkdir()
    config_path = tmp_path / "custom-config.toml"
    import sqlite3
    from contextlib import closing
    config = SimpleNamespace(source=config_path,
                             storage=SimpleNamespace(database_dir=tmp_path))
    with closing(sqlite3.connect(tmp_path / "pharos-jobs.sqlite3")) as db:
        db.execute("CREATE TABLE jobs (kind TEXT, status TEXT)")
        db.commit()
    checker = SimpleNamespace(marker_path=tmp_path / "marker.json")
    jobs = SimpleNamespace(list=lambda: [])
    states = []
    commands = []
    monkeypatch.setattr(web_updater, "maintenance", lambda: False)
    monkeypatch.setattr(web_updater, "_preflight",
                        lambda _, expected: (checkout, OLD))
    monkeypatch.setattr(web_updater, "check_data_volume",
                        lambda _: SimpleNamespace(available=True))
    monkeypatch.setattr(web_updater, "_probe", lambda _:
                        {"remote_sha": NEW}, raising=False)
    monkeypatch.setattr("pharos.update_check._probe", lambda _:
                        {"remote_sha": NEW})
    monkeypatch.setattr(web_updater, "_write", lambda data: states.append(data))
    monkeypatch.setattr(web_updater, "snapshot", lambda: states[-1])
    def fake_run(args, **kwargs):
        commands.append(args)
        return ""
    monkeypatch.setattr(web_updater, "_run", fake_run)
    result = web_updater.start(checker, config, jobs, None, NEW)
    assert result["target_sha"] == NEW
    command = commands[-1]
    assert "systemd-run" in command
    assert "--target" in command and NEW in command
    assert "--config" in command and str(config_path) in command


def test_sqlite_config_backup_and_atomic_rollback(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing
    store = tmp_path / "state"
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    config_file = tmp_path / "config.toml"
    secrets_file = tmp_path / "secrets.toml"
    config_file.write_text("original config\n", encoding="utf-8")
    secrets_file.write_text("original secrets\n", encoding="utf-8")
    db_file = db_dir / "rag.sqlite3"
    with closing(sqlite3.connect(db_file)) as conn:
        with conn:
            conn.execute("CREATE TABLE data (value TEXT)")
            conn.execute("INSERT INTO data VALUES ('original')")
    cfg = SimpleNamespace(source=config_file,
                          security=SimpleNamespace(secrets_file=secrets_file),
                          storage=SimpleNamespace(database_dir=db_dir))
    monkeypatch.setattr(web_updater, "STATE_DIR", store)
    backup = web_updater._backup(cfg, "before")
    assert (backup / "sqlite" / "rag.sqlite3").is_file()
    assert (backup / "config.toml").read_text() == "original config\n"
    config_file.write_text("changed config\n", encoding="utf-8")
    secrets_file.write_text("changed secrets\n", encoding="utf-8")
    with closing(sqlite3.connect(db_file)) as conn:
        with conn:
            conn.execute("UPDATE data SET value='changed'")
    web_updater._restore_sqlite(cfg, backup)
    assert config_file.read_text() == "original config\n"
    assert secrets_file.read_text() == "original secrets\n"
    with closing(sqlite3.connect(db_file)) as conn:
        assert conn.execute("SELECT value FROM data").fetchone()[0] == "original"



def test_manifest_verified_core_updates_can_pass_from_web(tmp_path, monkeypatch):
    import json
    def fake_run(args, **kwargs):
        return ""
    def fake_command(_, *args):
        if args[0] == "rev-parse":
            return NEW
        if args[0] == "diff":
            return "src/pharos/knowledge/rag.py\nrelease/core-upgrade.json"
        if args[0] == "show":
            assert args[1] == f"{NEW}:release/core-upgrade.json"
            return json.dumps({"schema": 1, "update_mode": "core_reinstall",
                "data_migrations": "none",
                "third_party_binary_changes": False,
                "requires_manual_steps": False})
        raise AssertionError(args)
    monkeypatch.setattr(web_updater, "_run", fake_run)
    monkeypatch.setattr(web_updater, "_command", fake_command)
    web_updater._ensure_same_target(tmp_path, NEW, OLD)


@pytest.mark.parametrize("manifest", [
    {"schema": 1, "update_mode": "core_reinstall",
     "data_migrations": "qdrant_rebuild", "third_party_binary_changes": False,
     "requires_manual_steps": False},
    {"schema": 1, "update_mode": "core_reinstall",
     "data_migrations": "none", "third_party_binary_changes": True,
     "requires_manual_steps": False},
    {"schema": 1, "update_mode": "core_reinstall",
     "data_migrations": "none", "third_party_binary_changes": False,
     "requires_manual_steps": True},
])
def test_core_update_blocks_irreversible_migration(manifest, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(web_updater, "_run", lambda *args, **kw: "")
    def fake_command(_, *args):
        if args[0] == "rev-parse":
            return NEW
        if args[0] == "diff":
            return "src/pharos/config.py\nrelease/core-upgrade.json"
        if args[0] == "show":
            return json.dumps(manifest)
        raise AssertionError(args)
    monkeypatch.setattr(web_updater, "_command", fake_command)
    with pytest.raises(web_updater.UpdateError, match="manual_review"):
        web_updater._ensure_same_target(tmp_path, NEW, OLD)


def test_default_updater_state_and_marker_are_isolated(prepared, isolated_updater_state):
    from pharos.config import load_config
    from pharos.update_check import UpdateChecker
    state = isolated_updater_state
    assert web_updater.STATE_DIR == state
    assert web_updater.STATE_FILE == state / "web-update.json"
    checker = UpdateChecker(load_config(prepared))
    assert checker.marker_path == state / "update-source.json"
    assert not checker.marker_path.exists()
    assert not web_updater.maintenance()
    web_updater._write({"status": "completed", "phase": "completed"})
    assert web_updater.STATE_FILE.is_file()
    assert web_updater.snapshot()["status"] == "completed"


def test_isolated_state_still_enforces_maintenance(prepared, monkeypatch):
    from pharos.config import load_config
    import time
    monkeypatch.setattr(web_updater, "_unit_running", lambda: True)
    web_updater._write({"status": "applying", "updated_at": int(time.time())})
    with TestClient(create_app(load_config(prepared))) as client:
        response = client.post("/api/chat", headers=HEAD, json={
            "model": "synthetic", "messages": [{"role": "user", "content": "test"}]})
        assert response.status_code == 423
        assert response.json()["detail"] == "update_in_progress"
