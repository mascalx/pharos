"""Versioned, data-only PHAROS translation catalog and custom language editing.

Built-ins ship inside the Python package at web/locales/{it,en}.json.
Custom translations live in storage.data_dir/locales, outside the Git checkout.
No code execution, HTML templates, remote downloads or arbitrary paths.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .storage import check_data_volume

BUILTINS = Path(__file__).parent / "web" / "locales"
CODE_RE = re.compile(r"[a-z]{2}(?:-[A-Z]{2})?\Z")
KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}\Z")
LOCK = threading.RLock()
MAX_FILE_BYTES = 256 * 1024
MAX_KEYS = 1500
MAX_VALUE = 4000


class LocaleError(ValueError):
    pass


def _code(code: str) -> str:
    if not isinstance(code, str) or not CODE_RE.fullmatch(code):
        raise LocaleError("invalid_language_code")
    return code


def _parse(data: object, expected: str | None = None) -> dict:
    if not isinstance(data, dict) or set(data) != {"code", "name", "messages"}:
        raise LocaleError("invalid_translation_file")
    code = _code(data["code"])
    if expected is not None and code != expected:
        raise LocaleError("language_code_mismatch")
    name = data["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
        raise LocaleError("invalid_language_name")
    messages = data["messages"]
    if not isinstance(messages, dict) or len(messages) > MAX_KEYS:
        raise LocaleError("invalid_translation_messages")
    for key, value in messages.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise LocaleError("invalid_translation_key")
        if not isinstance(value, str) or len(value) > MAX_VALUE or "\0" in value:
            raise LocaleError("invalid_translation_value")
    return {"code": code, "name": name.strip(), "messages": messages}


def _read(path: Path, expected: str) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise LocaleError("invalid_translation_file")
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8")), expected)
    except (OSError, ValueError, UnicodeError) as exc:
        raise LocaleError("invalid_translation_file") from exc


def _custom_dir(data_dir: Path) -> Path:
    folder = data_dir / "locales"
    if folder.is_symlink():
        raise LocaleError("unsafe_translation_directory")
    return folder


def _builtin(code: str) -> dict:
    return _read(BUILTINS / (code + ".json"), code)


def available(data_dir: Path) -> list[dict]:
    result = []
    for code in ("it", "en"):
        data = _builtin(code)
        result.append({"code": code, "name": data["name"], "builtin": True,
                       "count": len(data["messages"])})
    try:
        folder = _custom_dir(data_dir)
    except LocaleError:
        # A malformed custom directory must not hide the built-in languages.
        return result
    if folder.is_dir() and not folder.is_symlink():
        for path in sorted(folder.glob("*.json")):
            try:
                code = _code(path.stem)
                if code in ("it", "en"):
                    continue
                data = _read(path, code)
                result.append({"code": code, "name": data["name"], "builtin": False,
                               "count": len(data["messages"])})
            except LocaleError:
                continue  # Damaged user files never break the entire UI
    return result


def valid_language(code: str, data_dir: Path) -> bool:
    return any(item["code"] == code for item in available(data_dir))


def get(data_dir: Path, code: str) -> dict:
    code = _code(code)
    # The English baseline is mandatory. Custom files override only English
    # keys; absent/blank values fall back rather than leaking raw key names.
    english = _builtin("en")
    base = _builtin("it") if code == "it" else english
    custom = None if code in ("it", "en") else _read(_custom_dir(data_dir) / (code + ".json"), code)
    if code in ("it", "en"):
        merged = dict(english["messages"])
        merged.update(base["messages"])
    else:
        merged = dict(english["messages"])
        merged.update({k: v for k, v in custom["messages"].items() if v.strip() and k in merged})
    return {"code": code, "name": custom["name"] if custom else base["name"],
            "messages": merged, "source": "custom" if custom else "builtin",
            "fallback": "en"}


def edit_template(data_dir: Path, code: str) -> dict:
    code = _code(code)
    if code in ("it", "en"):
        return {"code": code, "name": _builtin(code)["name"], "builtin": True,
                "messages": _builtin(code)["messages"], "revision": None}
    path = _custom_dir(data_dir) / (code + ".json")
    data = _read(path, code)
    return {**data, "builtin": False, "revision": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(config, data: dict, expected_revision: str | None) -> dict:
    # Write only to the verified data volume and the dedicated language folder.
    if not check_data_volume(config.storage).available:
        raise LocaleError("data_volume_unavailable")
    checked = _parse(data)
    code = checked["code"]
    if code in ("en", "it"):
        raise LocaleError("builtin_language_readonly")
    valid_keys = set(_builtin("en")["messages"])
    if not set(checked["messages"]).issubset(valid_keys):
        raise LocaleError("unknown_translation_keys")
    folder = _custom_dir(config.storage.data_dir)
    if folder.is_symlink():
        raise LocaleError("unsafe_translation_directory")
    with LOCK:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = folder / (code + ".json")
        if target.is_symlink():
            raise LocaleError("unsafe_translation_file")
        if target.is_file() and target.stat().st_size > MAX_FILE_BYTES:
            raise LocaleError("invalid_translation_file")
        previous = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if previous != expected_revision:
            raise LocaleError("translation_changed_reload")
        raw = json.dumps(checked, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        if len(raw) > MAX_FILE_BYTES:
            raise LocaleError("translation_too_large")
        fd, tmp = tempfile.mkstemp(prefix=".locale-", dir=folder)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return edit_template(config.storage.data_dir, code)


def delete(config, code: str, expected_revision: str) -> None:
    code = _code(code)
    if code in ("it", "en"):
        raise LocaleError("builtin_language_readonly")
    if not check_data_volume(config.storage).available:
        raise LocaleError("data_volume_unavailable")
    target = _custom_dir(config.storage.data_dir) / (code + ".json")
    with LOCK:
        if target.is_symlink() or not target.is_file():
            raise LocaleError("translation_not_found")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise LocaleError("invalid_translation_file")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_revision:
            raise LocaleError("translation_changed_reload")
        # Callers must prevent deleting the currently configured language.
        target.unlink()
