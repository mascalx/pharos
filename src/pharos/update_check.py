"""Read-only checks for updates of the Ubuntu Git installation.

PHAROS never installs or switches code here. Version discovery may fetch
Git objects without changing refs, FETCH_HEAD or the working tree. The regular
Ubuntu installer writes a revision marker *after* successful setup. The
configured checkout's private Git credentials are never exposed to clients.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
import time
import tomllib
from urllib.parse import urlsplit

from . import __version__

SHA = re.compile(r"[0-9a-f]{40}\Z")
OFFICIAL_REPOSITORY = "mascalx/pharos"
COMPARE_URL = "https://github.com/mascalx/pharos/compare/"


def _official_origin(remote: str) -> bool:
    value = remote.strip()
    if value in ("git@github.com:mascalx/pharos.git",
                 "git@github.com:mascalx/pharos"):
        return True
    parsed = urlsplit(value)
    return (parsed.scheme in ("https", "ssh")
            and parsed.hostname == "github.com"
            and parsed.username in (None, "git")
            and parsed.password is None and parsed.port is None
            and parsed.path.strip("/") in
            ("mascalx/pharos", "mascalx/pharos.git")
            and not parsed.query and not parsed.fragment
            and (parsed.scheme != "https" or parsed.username is None))


def _command(checkout: Path, *args: str) -> str:
    # Prevent prompts from hanging a background task on headless installations.
    # Never include git stderr or remote URLs (possibly containing credentials)
    # in the user-facing response.
    import os
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -oBatchMode=yes -oConnectTimeout=10"
    result = subprocess.run(["git", "-C", str(checkout), *args],
                            capture_output=True, text=True, timeout=22,
                            check=False, env=env)
    if result.returncode:
        raise RuntimeError("git_command_failed")
    return result.stdout.strip()


def _read_installation(marker_path: Path) -> tuple[Path, str]:
    if marker_path.is_symlink():
        raise ValueError("untrusted_installation_marker")
    record = json.loads(marker_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("invalid_installation_marker")
    location = record.get("checkout")
    installed = record.get("installed_sha")
    if not isinstance(location, str) or not location:
        raise ValueError("invalid_checkout")
    if not isinstance(installed, str) or not SHA.fullmatch(installed):
        raise ValueError("invalid_installed_revision")
    checkout = Path(location)
    if not checkout.is_absolute() or not checkout.is_dir():
        raise ValueError("checkout_missing")
    return checkout, installed


def _probe(marker_path: Path) -> dict:
    checkout, installed = _read_installation(marker_path)
    remote = _command(checkout, "remote", "get-url", "origin")
    if not _official_origin(remote):
        raise ValueError("untrusted_repository")
    heads = _command(checkout, "ls-remote", "--heads", "origin", "refs/heads/main")
    head = heads.split("\t", 1)[0].strip()
    if not SHA.fullmatch(head):
        raise ValueError("remote_revision_unavailable")
    # We report a changed tip, not a semantic version or an unverified claim
    # that all commits between these SHAs have passed CI.
    return {
        "state": "available" if installed != head else "current",
        "available": installed != head,
        "installed_sha": installed,
        "remote_sha": head,
        "compare_url": COMPARE_URL + installed + "...main" if installed != head else "",
    }


def _remote_version(marker_path: Path, head: str) -> str:
    """Read package metadata from the exact checked commit."""
    if not SHA.fullmatch(head):
        return ""
    try:
        checkout, _ = _read_installation(marker_path)
        if not _official_origin(_command(checkout, "remote", "get-url", "origin")):
            return ""
        try:
            contents = _command(checkout, "show", f"{head}:pyproject.toml")
        except RuntimeError:
            # Reuse private Git credentials without updating refs or FETCH_HEAD.
            _command(checkout, "fetch", "--no-tags", "--no-write-fetch-head",
                     "--refmap=", "--no-auto-maintenance", "origin", head)
            contents = _command(checkout, "show", f"{head}:pyproject.toml")
        if len(contents.encode("utf-8")) > 65536:
            return ""
        version = tomllib.loads(contents).get("project", {}).get("version")
        return version if isinstance(version, str) and re.fullmatch(
            r"[0-9][A-Za-z0-9.!+_-]{0,63}", version) else ""
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError,
            subprocess.TimeoutExpired):
        # Metadata failure must not suppress a verified changed Git tip.
        return ""


class UpdateChecker:
    def __init__(self, config, marker_path: Path | None = None):
        self.config = config
        self.marker_path = marker_path or (Path.home() / ".local/share/pharos/update-source.json")
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._last_refresh_request = 0.0
        self.state = {
            "state": "unchecked", "available": False,
            "installed_sha": "", "remote_sha": "", "compare_url": "",
            "last_checked": None, "channel": "development",
            "version": __version__, "remote_version": "",
            "automatic": config.updates.enabled,
            "interval_hours": config.updates.check_interval_hours,
        }

    def snapshot(self) -> dict:
        # Reading the local installation marker is instantaneous; GitHub
        # network I/O is deliberately outside the GET request path.
        # In particular, drop a stale "available" result immediately when
        # an update just installed the SHA that we previously saw remotely.
        try:
            _, installed = _read_installation(self.marker_path)
            if self.state.get("installed_sha") != installed:
                remote = self.state.get("remote_sha", "")
                self.state.update(
                    installed_sha=installed,
                    state="current" if remote == installed else "unchecked",
                    available=False,
                    compare_url="",
                    last_checked=None,
                )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return {**self.state, "checking": self._lock.locked() or
                (self._refresh_task is not None and not self._refresh_task.done())}

    def request_refresh(self, *, force: bool = False) -> dict:
        """Schedule a network check, return cached/local state without waiting.

        Normal page loads revalidate after 30 s; Diagnostics asks for an
        immediate refresh, rate limited to one network probe in 8 seconds.
        """
        state = self.snapshot()
        age = time.time() - (self.state.get("last_checked") or 0)
        now = time.monotonic()
        if self._lock.locked() or (self._refresh_task and not self._refresh_task.done()):
            return state
        if force:
            if now - self._last_refresh_request < 8:
                return state
        elif self.state["state"] != "unchecked" and age < 30:
            return state
        self._last_refresh_request = now
        self._refresh_task = asyncio.create_task(self.check())
        return self.snapshot()

    async def check(self) -> dict:
        async with self._lock:
            try:
                result = await asyncio.to_thread(_probe, self.marker_path)
                head = result["remote_sha"]
                if head == result["installed_sha"]:
                    version = __version__
                elif head == self.state.get("remote_sha") and self.state.get("remote_version"):
                    version = self.state["remote_version"]
                else:
                    version = await asyncio.to_thread(_remote_version, self.marker_path, head)
                self.state.update(result, remote_version=version)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired,
                    json.JSONDecodeError) as exc:
                reason = str(exc)
                if reason in ("invalid_installation_marker", "untrusted_installation_marker",
                              "invalid_installed_revision", "invalid_checkout",
                              "checkout_missing", "untrusted_repository"):
                    state = "unsupported"
                elif isinstance(exc, FileNotFoundError):
                    state = "unsupported"
                else:
                    state = "unavailable"
                self.state.update(state=state, available=False, remote_sha="",
                                  compare_url="", remote_version="")
            self.state["last_checked"] = int(time.time())
            return self.snapshot()

    async def _poll(self):
        try:
            await self.check()
            while True:
                await asyncio.sleep(self.config.updates.check_interval_hours * 3600)
                await self.check()
        except asyncio.CancelledError:
            raise

    def start(self):
        if self.config.updates.enabled and self._task is None:
            self._task = asyncio.create_task(self._poll())

    async def stop(self):
        for attr in ("_refresh_task", "_task"):
            task=getattr(self,attr)
            setattr(self,attr,None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
