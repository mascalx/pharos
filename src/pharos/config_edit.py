"""Validated, comment-preserving, atomic TOML editing for web and CLI consumers.

The source TOML remains authoritative. The web API applies safe runtime values
immediately; structural changes stay on disk until a controlled restart.
Secrets are stored separately and never included in settings responses.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import threading

import tomlkit
from .config import Config, ConfigError, load_config
from .secrets import read_secrets, write_secrets


class SettingsError(ValueError):
    pass


# Explicit allowlist: no arbitrary file writes, tables, or unknown keys.
# types are strict, since bool is a subclass of int in Python.
FIELDS = {
    'general': {'language': ('it', str)},
    'server': {'host': ('127.0.0.1', str), 'port': (8080, int)},
    'security': {'secrets_file': ('./secrets.toml', str)},
    'storage': {'data_dir': ('./pharos-data', str), 'database_dir': ('./pharos-db', str),
                'cache_dir': ('./pharos-cache', str), 'marker_id': ('', str),
                'require_marker': (True, bool), 'require_mount': (False, bool)},
    'ai': {'enabled': (False, bool), 'provider': ('tiiny_direct', str),
           'base_url': ('http://127.0.0.1:9876', str), 'default_model': ('', str),
           'request_timeout_seconds': (120, int), 'max_output_tokens': (1200, int),
           'management_url': ('', str), 'npu_policy': ('protected', str),
           'protected_models': ('', str), 'queue_limit': (16, int),
           'npu_load_timeout_seconds': (120, int)},
    'kiwix': {'server_url': ('http://127.0.0.1:8081', str), 'browser_url': ('', str)},
    'rag': {'enabled': (False, bool), 'embedding_model': ('', str),
            'qdrant_url': ('http://127.0.0.1:6333', str), 'chunk_chars': (1100, int),
            'overlap_chars': (150, int), 'max_document_mb': (12, int), 'search_limit': (5, int)},
}
SETTINGS_LOCK = threading.RLock()


def revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def settings_values(path: Path) -> dict:
    try:
        raw = tomlkit.parse(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise SettingsError('config_unreadable') from exc
    result = {}
    for section, fields in FIELDS.items():
        contents = raw.get(section) or {}
        result[section] = {key: (value.unwrap() if hasattr(value, 'unwrap') else value) for key, (default, _) in fields.items() for value in [contents.get(key, default)]}
    return {'values': result, 'revision': revision(path)}


def _verify_payload(values: dict) -> None:
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise SettingsError('invalid_config_sections')
    for section, fields in FIELDS.items():
        data = values[section]
        if not isinstance(data, dict) or set(data) != set(fields):
            raise SettingsError('invalid_config_fields')
        for key, (_, expected_type) in fields.items():
            value = data[key]
            if type(value) is not expected_type or (isinstance(value, str) and (len(value) > 2048 or '\0' in value)):
                raise SettingsError(f'invalid_config_value:{section}.{key}')


def save_settings(config: Config, values: dict, expected_revision: str) -> str:
    """Save a complete validated configuration, preserving comments and unknown fields.

    Reject stale edits; validate via the original parser before replacing the file.
    The caller separately merges safe fields into the running application.
    """
    _verify_payload(values)
    path = config.source
    with SETTINGS_LOCK:
        if revision(path) != expected_revision:
            raise SettingsError('config_changed_reload')
        raw = tomlkit.parse(path.read_text(encoding='utf-8'))
        for section, fields in FIELDS.items():
            if section not in raw:
                raw.add(section, tomlkit.table())
            for key in fields:
                value = values[section][key]
                if raw[section].get(key) != value:
                    raw[section][key] = value
        # Validate through the real config parser, *without modifying the live config*.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                             prefix='.pharos-config-', suffix='.toml', delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(tomlkit.dumps(raw))
                stream.flush()
                os.fsync(stream.fileno())
            candidate = load_config(temporary)
            if candidate.language != config.language:
                from .locales import valid_language
                if not valid_language(candidate.language, config.storage.data_dir):
                    raise SettingsError("language_not_available")
            if candidate.storage.marker_id != config.storage.marker_id and config.storage.require_marker:
                # Markers are volume identities, not arbitrary UI-generated UUIDs.
                raise SettingsError('marker_change_requires_cli')
            old_secrets, new_secrets = config.security.secrets_file, candidate.security.secrets_file
            if old_secrets != new_secrets:
                if new_secrets.exists():
                    raise SettingsError('secrets_target_exists')
                write_secrets(new_secrets, read_secrets(old_secrets))
            # Preserve original file mode and ownership (os.replace keeps temp's mode).
            original_mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(temporary, original_mode)
            os.replace(temporary, path)
            temporary = None
            # fsync parent directory when supported, for crash consistency.
            try:
                fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(fd)
                finally: os.close(fd)
            except OSError:
                pass
            return revision(path)
        except ConfigError as exc:
            raise SettingsError(str(exc)) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
