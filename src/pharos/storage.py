"""Storage separation and fail-closed data-volume identification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from .config import StorageConfig

MARKER_FILE = ".pharos-volume"


@dataclass(frozen=True)
class StorageStatus:
    available: bool
    reason: str
    mounted: bool


def check_data_volume(config: StorageConfig) -> StorageStatus:
    root = config.data_dir
    if not root.is_dir():
        return StorageStatus(False, "data_dir_missing", False)
    mounted = os.path.ismount(root)
    if config.require_mount and not mounted:
        return StorageStatus(False, "mount_missing", False)
    if config.require_marker:
        marker = root / MARKER_FILE
        try:
            if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != config.marker_id:
                return StorageStatus(False, "marker_missing_or_wrong", mounted)
        except (OSError, UnicodeError):
            return StorageStatus(False, "marker_unreadable", mounted)
    if not os.access(root, os.R_OK | os.W_OK):
        return StorageStatus(False, "data_dir_not_writable", mounted)
    return StorageStatus(True, "ok", mounted)


def initialize_volume(config: StorageConfig, confirmed_path: str) -> Path:
    """Explicitly bind data storage to this configuration. Never create mountpoints."""
    root = config.data_dir
    if Path(confirmed_path).expanduser().resolve() != root:
        raise ValueError(f"Confirmation does not match configured data_dir: {root}")
    if not root.is_dir():
        raise ValueError(f"Directory does not exist: {root}; create/mount it first")
    if config.require_mount and not os.path.ismount(root):
        raise ValueError(f"Directory is not a mount point: {root}")
    marker_path = root / MARKER_FILE
    if marker_path.exists():
        current = marker_path.read_text(encoding="utf-8").strip()
        if current != config.marker_id:
            raise ValueError("Volume already has a different PHAROS marker; refusing to replace")
        return marker_path
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(marker_path, flags, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(config.marker_id + "\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        marker_path.unlink(missing_ok=True)
        raise
    return marker_path
