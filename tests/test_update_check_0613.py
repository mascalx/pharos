"""Update-discovery tests (no actual GitHub requests or system installation)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.update_check import UpdateChecker, _official_origin, _probe

OLD = "a" * 40
NEW = "b" * 40


def test_official_repository_origin_only():
    assert _official_origin("git@github.com:mascalx/pharos.git")
    assert _official_origin("https://github.com/mascalx/pharos.git")
    assert _official_origin("ssh://git@github.com/mascalx/pharos.git")
    for remote in ("https://evil.example/mascalx/pharos.git",
                   "http://github.com/mascalx/pharos",
                   "https://user:secret@github.com/mascalx/pharos.git",
                   "https://github.com/other/pharos",
                   "file:///tmp/pharos"):
        assert not _official_origin(remote)


def test_read_only_revision_check(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    marker = tmp_path / "update-source.json"
    marker.write_text(json.dumps({"checkout": str(checkout),
                                  "installed_sha": OLD}))
    calls = []

    def fake_command(path, *args):
        calls.append(args)
        if args[:2] == ("remote", "get-url"):
            return "git@github.com:mascalx/pharos.git"
        if args[:1] == ("ls-remote",):
            return NEW + "\trefs/heads/main"
        pytest.fail("unexpected git command")

    monkeypatch.setattr("pharos.update_check._command", fake_command)
    result = _probe(marker)
    assert result["available"] is True
    assert result["installed_sha"] == OLD
    assert result["remote_sha"] == NEW
    assert result["compare_url"].endswith(OLD + "...main")
    assert len(calls) == 2

    def unchanged(path, *args):
        return "https://github.com/mascalx/pharos.git" if args[0] == "remote" else OLD + "\trefs/heads/main"

    monkeypatch.setattr("pharos.update_check._command", unchanged)
    assert _probe(marker)["state"] == "current"


def test_marker_refuses_symlinks_and_untrusted_origin(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    marker = tmp_path / "marker.json"
    marker.write_text(json.dumps({"checkout": str(checkout),
                                  "installed_sha": OLD}))
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(marker)
    except (OSError, NotImplementedError):
        # Windows CI may not grant symlink creation privileges.
        pass
    else:
        with pytest.raises(ValueError, match="untrusted_installation_marker"):
            _probe(alias)
    monkeypatch.setattr("pharos.update_check._command",
                        lambda path, *args: "https://other.example/pharos")
    with pytest.raises(ValueError, match="untrusted_repository"):
        _probe(marker)


def test_manual_check_and_background_state(prepared, tmp_path, monkeypatch):
    cfg = load_config(prepared)
    fake_result = {
        "state": "available", "available": True,
        "installed_sha": OLD, "remote_sha": NEW,
        "compare_url": "https://github.com/mascalx/pharos/compare/" + OLD + "...main"
    }
    monkeypatch.setattr("pharos.update_check._probe", lambda _: fake_result)
    monkeypatch.setattr("pharos.update_check._remote_version", lambda marker, head: "99.0.0")
    # The production marker may exist on an Ubuntu test machine. Keep the
    # synthetic installation SHA and the marker read by snapshot() consistent.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    marker = tmp_path / "update-source.json"
    marker.write_text(json.dumps({"checkout": str(checkout),
                                  "installed_sha": OLD}), encoding="utf-8")
    app = create_app(cfg)
    app.state.update_checker.marker_path = marker
    with TestClient(app) as client:
        assert client.get("/api/updates").status_code == 200
        assert client.post("/api/updates/check").status_code == 403
        response = client.post("/api/updates/check",
                               headers={"X-Pharos-Request": "1"})
        assert response.status_code == 200
        assert response.json()["available"] is True
        assert response.json()["installed_sha"] == OLD
        assert response.json()["remote_version"] == "99.0.0"
        assert response.json()["channel"] == "development"
        assert response.json()["interval_hours"] == 24
        assert client.get("/api/updates").json()["remote_sha"] == NEW


def test_snapshot_invalidates_available_cache_after_installation(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    marker = tmp_path / "update-source.json"
    marker.write_text(json.dumps({"checkout": str(checkout),
                                  "installed_sha": OLD}), encoding="utf-8")
    config = SimpleNamespace(updates=SimpleNamespace(enabled=False,
                                                      check_interval_hours=24))
    checker = UpdateChecker(config, marker)
    checker.state.update(state="available", available=True,
                         installed_sha=OLD, remote_sha=NEW,
                         compare_url="https://github.com/mascalx/pharos/compare/"
                                     + OLD + "...main",
                         last_checked=123)
    assert checker.snapshot()["available"] is True

    # Installation of the advertised remote revision must immediately
    # invalidate a stale cached offer without requiring any network request.
    marker.write_text(json.dumps({"checkout": str(checkout),
                                  "installed_sha": NEW}), encoding="utf-8")
    state = checker.snapshot()
    assert state["available"] is False
    assert state["state"] == "current"
    assert state["installed_sha"] == NEW
    assert state["remote_sha"] == NEW
    assert state["compare_url"] == ""
    assert state["last_checked"] is None


def test_offline_check_is_nonfatal(tmp_path, monkeypatch):
    config = SimpleNamespace(updates=SimpleNamespace(enabled=True,
                                                      check_interval_hours=24))
    checker = UpdateChecker(config, tmp_path / "no-marker.json")
    state = asyncio.run(checker.check())
    assert state["available"] is False
    assert state["state"] in ("unsupported", "unavailable")
    assert state["last_checked"] is not None
