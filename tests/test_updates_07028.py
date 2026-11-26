"""Dev28 updater regressions: commit-pinned versions and server-side admission."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pharos import update_check, web_updater
from pharos.app import create_app
from pharos.config import load_config

OLD = "a" * 40
NEW = "b" * 40
HEADERS = {"X-Pharos-Request": "1"}


def test_remote_version_reads_pinned_git_object(tmp_path, monkeypatch):
    checkout = tmp_path / "clone"
    checkout.mkdir()
    marker = tmp_path / "marker.json"
    marker.write_text(json.dumps({"checkout": str(checkout), "installed_sha": OLD}))
    calls = []
    fetched = False

    def command(path, *args):
        nonlocal fetched
        assert path == checkout
        calls.append(args)
        if args == ("remote", "get-url", "origin"):
            return "git@github.com:mascalx/pharos.git"
        if args == ("show", NEW + ":pyproject.toml"):
            if not fetched:
                raise RuntimeError("git_command_failed")
            return '[project]\nversion = "0.7.0.dev28"\n'
        if args == ("fetch", "--no-tags", "--no-write-fetch-head",
                    "--refmap=", "--no-auto-maintenance", "origin", NEW):
            fetched = True
            return ""
        pytest.fail(f"Unexpected command {args}")

    monkeypatch.setattr(update_check, "_command", command)
    assert update_check._remote_version(marker, NEW) == "0.7.0.dev28"
    assert len(calls) == 4
    assert update_check._remote_version(marker, "main") == ""


def test_remote_version_failure_keeps_update_available(tmp_path, monkeypatch):
    cfg = SimpleNamespace(updates=SimpleNamespace(enabled=False, check_interval_hours=24))
    checker = update_check.UpdateChecker(cfg, tmp_path / "missing-marker")
    result = {"state": "available", "available": True, "installed_sha": OLD,
              "remote_sha": NEW, "compare_url": ""}
    monkeypatch.setattr(update_check, "_probe", lambda _: result)
    monkeypatch.setattr(update_check, "_remote_version", lambda *args: "")
    state = asyncio.run(checker.check())
    assert state["available"] is True and state["remote_sha"] == NEW
    assert state["remote_version"] == ""
    calls = []
    monkeypatch.setattr(update_check, "_remote_version",
                        lambda *args: calls.append(args) or "0.7.0.dev28")
    async def check_twice():
        await checker.check()
        return await checker.check()
    state = asyncio.run(check_twice())
    assert state["remote_version"] == "0.7.0.dev28"
    assert len(calls) == 1


@pytest.mark.parametrize("kind,status,reason", [
    ("reindex-rag", "running", "update_blocked_reindex"),
    ("ingest-document", "queued", "update_blocked_ingestion"),
    ("build-lexical", "running", "update_blocked_lexical"),
    ("index-map", "cancelling", "update_blocked_map_index"),
    ("download-zim", "running", "update_blocked_download"),
    ("download-map", "queued", "update_blocked_download"),
    ("other", "running", "background_jobs_active"),
])
def test_entire_job_queue_blocks_install(tmp_path, kind, status, reason):
    cfg = SimpleNamespace(storage=SimpleNamespace(database_dir=tmp_path))
    assert web_updater._active_job_reason(cfg) == "update_jobs_unavailable"
    with closing(sqlite3.connect(tmp_path / "pharos-jobs.sqlite3")) as db:
        db.execute("CREATE TABLE jobs (kind TEXT, status TEXT)")
        db.execute("INSERT INTO jobs VALUES (?,?)", (kind, status))
        db.executemany("INSERT INTO jobs VALUES (?,?)", [("download-zim", "completed")] * 80)
        db.commit()
        assert web_updater._active_job_reason(cfg) == reason
        with pytest.raises(web_updater.UpdateError, match=reason):
            web_updater._require_idle(cfg, SimpleNamespace(list=lambda: []), None)
        db.execute("UPDATE jobs SET status='completed'")
        db.commit()
        assert web_updater._active_job_reason(cfg) == ""


def test_inflight_request_blocks_update(prepared, monkeypatch):
    cfg = load_config(prepared)
    app = create_app(cfg)
    entered, release = Event(), Event()
    monkeypatch.setattr(web_updater, "maintenance", lambda: False)
    @app.post("/api/test-long-operation")
    def long_operation():
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test timeout")
        return {"ok": True}
    def fail_start(*args):
        pytest.fail("Updater must not start while a mutation is active")
    monkeypatch.setattr(web_updater, "start", fail_start)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(client.post, "/api/test-long-operation", headers=HEADERS)
        try:
            assert entered.wait(5)
            response = client.post("/api/updates/start", headers=HEADERS,
                                   json={"target_sha": NEW})
            assert response.status_code == 409
            assert response.json()["detail"] == "update_blocked_operation"
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 200


def test_preflight_gate_refuses_new_mutations_and_releases_on_failure(prepared, monkeypatch):
    cfg = load_config(prepared)
    app = create_app(cfg)
    entered, release = Event(), Event()
    monkeypatch.setattr(web_updater, "maintenance", lambda: False)
    @app.post("/api/test-operation")
    def operation():
        return {"ok": True}
    def blocked_start(*args):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test timeout")
        raise web_updater.UpdateError("background_jobs_active")
    monkeypatch.setattr(web_updater, "start", blocked_start)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(client.post, "/api/updates/start", headers=HEADERS,
                             json={"target_sha": NEW})
        try:
            assert entered.wait(5)
            assert client.get("/healthz").status_code == 200
            response = client.post("/api/test-operation", headers=HEADERS)
            assert response.status_code == 423
        finally:
            release.set()
        response = future.result(timeout=10)
        assert response.status_code == 409
        assert response.json()["detail"] == "background_jobs_active"
        assert client.post("/api/test-operation", headers=HEADERS).status_code == 200
