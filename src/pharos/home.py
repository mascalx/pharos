"""TOML-backed home display preferences, independent of feature availability.

These are presentation preferences, not feature enable/disable switches.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import tomlkit

from .config_edit import SETTINGS_LOCK

FEATURES = ('chat', 'knowledge', 'kiwix', 'settings', 'diagnostics', 'notifications', 'translations', 'maps')
LEGACY_FEATURES = FEATURES[:-3]
PRE_TRANSLATIONS_FEATURES = FEATURES[:-2]
PRE_MAPS_FEATURES = FEATURES[:-1]

class HomeError(ValueError):
    pass


def _validated(value: object, *, read_legacy: bool = False) -> dict:
    if not isinstance(value, dict) or set(value) != {'order', 'hidden'}:
        raise HomeError('home_invalid_preferences')
    order, hidden = value['order'], value['hidden']
    # Preserve existing personal order/visibility when new Home tiles appear.
    # Existing 5-/6-tile TOML configurations remain valid, adding only the
    # missing services at the end without resetting user preferences.
    if read_legacy and isinstance(order, list) and isinstance(hidden, list):
        for earlier in (LEGACY_FEATURES, PRE_TRANSLATIONS_FEATURES, PRE_MAPS_FEATURES):
            if (len(order) == len(earlier) and set(order) == set(earlier)
                    and len(set(order)) == len(order)
                    and len(set(hidden)) == len(hidden)
                    and set(hidden).issubset(earlier)):
                order = [*order, *(name for name in FEATURES if name not in earlier)]
                break
    if (not isinstance(order, list) or not isinstance(hidden, list)
        or any(not isinstance(item, str) for item in order + hidden)
        or len(order) != len(FEATURES) or set(order) != set(FEATURES)
        or len(set(order)) != len(order) or len(set(hidden)) != len(hidden)
        or not set(hidden).issubset(FEATURES)):
        raise HomeError('home_invalid_preferences')
    return {'order': list(order), 'hidden': list(hidden)}


def home_preferences(path: Path) -> dict:
    with SETTINGS_LOCK:
        try:
            raw = tomlkit.parse(path.read_text(encoding='utf-8'))
            current = raw.get('home', {})
            order = list(current.get('order', FEATURES))
            hidden = list(current.get('hidden', []))
            return _validated({'order': order, 'hidden': hidden}, read_legacy=True)
        except (OSError, ValueError, TypeError) as exc:
            raise HomeError('home_invalid_preferences') from exc


def save_home_preferences(path: Path, value: object) -> dict:
    validated = _validated(value)
    with SETTINGS_LOCK:
        # Rewrite the authoritative TOML with atomic replace and fsync.
        original = path.read_text(encoding='utf-8')
        raw = tomlkit.parse(original)
        if 'home' not in raw:
            raw['home'] = tomlkit.table()
        raw['home']['order'] = validated['order']
        raw['home']['hidden'] = validated['hidden']
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                    prefix='.pharos-home-', suffix='.toml', delete=False) as output:
                temporary = Path(output.name)
                output.write(tomlkit.dumps(raw))
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
            temporary = None
            try:
                descriptor = os.open(str(path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
            except OSError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return validated
