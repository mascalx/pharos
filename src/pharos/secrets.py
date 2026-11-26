"""Protected file storage for authentication hash and gateway tokens."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import tomllib


class SecretError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def read_secrets(path: Path) -> dict:
    if not path.exists():
        return {}
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise SecretError(f"Secrets file must not be readable by group/others (chmod 600): {path}")
    try:
        with path.open("rb") as stream:
            content = tomllib.load(stream)
        if not isinstance(content, dict):
            raise SecretError("Invalid secrets format")
        return content
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SecretError(f"Cannot read secrets file: {exc}") from exc


def _toml_quote(text: str) -> str:
    import json
    return json.dumps(text, ensure_ascii=False)


def write_secrets(path: Path, values: dict) -> None:
    """Atomic write, restrictive permissions, preserve known credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for section, entries in values.items():
        if section not in ("security", "ai", "modules") or not isinstance(entries, dict):
            raise SecretError("Unknown secrets section")
        lines.append(f"[{section}]")
        for name, value in entries.items():
            if name not in {"security": {"password_hash"}, "ai": {"api_key", "management_api_key"}, "modules": {"admin_token_hash"}}[section] or not isinstance(value, str):
                raise SecretError("Unknown secret name")
            lines.append(f"{name} = {_toml_quote(value)}")
        lines.append("")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".pharos-secrets-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write("\n".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
