"""Read-only TOML settings. No application setting is read from environment variables."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


class ConfigError(ValueError):
    """Configuration is incomplete or invalid."""


def _table(source: dict, name: str) -> dict:
    value = source.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _string(table: dict, name: str, default: str = "") -> str:
    value = table.get(name, default)
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def _boolean(table: dict, name: str, default: bool) -> bool:
    value = table.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


def _integer(table: dict, name: str, default: int, low: int, high: int) -> int:
    value = table.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f"{name} must be an integer between {low} and {high}")
    return value


def _path(base: Path, text: str) -> Path:
    value = Path(text).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class SecurityConfig:
    secrets_file: Path


@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path
    database_dir: Path
    cache_dir: Path
    marker_id: str
    require_marker: bool
    require_mount: bool


@dataclass(frozen=True)
class AIConfig:
    enabled: bool
    provider: str
    base_url: str
    default_model: str
    request_timeout_seconds: int
    max_output_tokens: int
    management_url: str = ""
    npu_policy: str = "protected"
    protected_models: str = ""
    queue_limit: int = 16
    npu_load_timeout_seconds: int = 120


@dataclass(frozen=True)
class RAGConfig:
    enabled: bool
    embedding_model: str
    qdrant_url: str
    chunk_chars: int
    overlap_chars: int
    max_document_mb: int
    search_limit: int


@dataclass(frozen=True)
class KiwixConfig:
    server_url: str = "http://127.0.0.1:8081"
    browser_url: str = ""


@dataclass(frozen=True)
class UpdatesConfig:
    enabled: bool = True
    check_interval_hours: int = 24


@dataclass(frozen=True)
class Config:
    source: Path
    language: str
    server: ServerConfig
    security: SecurityConfig
    storage: StorageConfig
    ai: AIConfig
    rag: RAGConfig
    kiwix: KiwixConfig = KiwixConfig()
    updates: UpdatesConfig = UpdatesConfig()


def load_config(path: str | Path) -> Config:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"Configuration file not found: {source}")
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Unable to read TOML configuration: {exc}") from exc
    general = _table(raw, "general")
    server = _table(raw, "server")
    security = _table(raw, "security")
    storage = _table(raw, "storage")
    ai = _table(raw, "ai")
    rag = _table(raw, "rag")
    kiwix = _table(raw, "kiwix")
    updates = _table(raw, "updates")
    language = _string(general, "language", "it")
    data_dir = _path(source.parent, _string(storage, "data_dir", "./pharos-data"))
    database_dir = _path(source.parent, _string(storage, "database_dir", "./pharos-db"))
    cache_dir = _path(source.parent, _string(storage, "cache_dir", "./pharos-cache"))
    # A custom locale can temporarily disappear when the NAS is offline.
    # Keep the Core bootable; settings writes verify available languages,
    # while the browser falls back to the bundled English catalog.
    if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language):
        raise ConfigError("general.language must be a language code")
    for one, another in [(data_dir, database_dir), (data_dir, cache_dir), (database_dir, cache_dir)]:
        if one == another or one in another.parents or another in one.parents:
            raise ConfigError("Storage directories must be separate, not nested within each other")
    marker = _string(storage, "marker_id")
    if _boolean(storage, "require_marker", True) and not re.fullmatch(r"[0-9a-fA-F-]{36}", marker):
        raise ConfigError("storage.marker_id must be a UUID generated with 'pharos init'")
    base_url = _string(ai, "base_url", "http://127.0.0.1:9876").rstrip("/")
    from urllib.parse import urlsplit
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigError("ai.base_url must be a bare http(s) service URL without credentials or path")
    management_url = _string(ai, "management_url", "").rstrip("/")
    if management_url:
        mgmt = urlsplit(management_url)
        if (mgmt.scheme not in ("http", "https") or not mgmt.netloc or mgmt.username or mgmt.password
            or mgmt.path not in ("", "/") or mgmt.query or mgmt.fragment):
            raise ConfigError("ai.management_url must be a bare http(s) service URL")
    provider = _string(ai, "provider", "tiiny_direct")
    if provider not in ("tiiny_direct", "tiiny_gateway", "openai_compatible"):
        raise ConfigError("ai.provider must be tiiny_direct, tiiny_gateway or openai_compatible")
    npu_policy = _string(ai, "npu_policy", "protected")
    if npu_policy not in ("manual", "protected", "managed"):
        raise ConfigError("ai.npu_policy must be manual, protected or managed")
    protected_models = _string(ai, "protected_models", "")
    if len(protected_models) > 2048:
        raise ConfigError("ai.protected_models is too long")
    rag_url = _string(rag, "qdrant_url", "http://127.0.0.1:6333").rstrip("/")
    rag_parsed = urlsplit(rag_url)
    if (rag_parsed.scheme not in ("http", "https") or not rag_parsed.netloc
        or rag_parsed.username or rag_parsed.password or rag_parsed.path not in ("", "/")
        or rag_parsed.query or rag_parsed.fragment):
        raise ConfigError("rag.qdrant_url must be a bare http(s) service URL")
    chunk_chars = _integer(rag, "chunk_chars", 1100, 400, 4000)
    overlap_chars = _integer(rag, "overlap_chars", 150, 0, 1000)
    if overlap_chars >= chunk_chars // 2:
        raise ConfigError("rag.overlap_chars must be less than half of rag.chunk_chars")
    rag_enabled = _boolean(rag, "enabled", False)
    embedding_model = _string(rag, "embedding_model", "").strip()
    if rag_enabled and not embedding_model:
        raise ConfigError("rag.embedding_model must be configured when RAG is enabled")
    port = _integer(server, "port", 8080, 1, 65535)
    # Network access is restricted to the URL explicitly selected by the local administrator.
    # Both URLs may be on trusted LAN/VPN and differ if the server uses loopback.
    def kiwix_url(name, default):
        value = _string(kiwix, name, default).strip().rstrip('/')
        if not value and name == 'browser_url':
            return value
        parts = urlsplit(value)
        if (parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(ord(ch)<32 for ch in value)
            or (parts.path and ('//' in parts.path or '/..' in parts.path))):
            raise ConfigError('kiwix.'+name+' must be an HTTP(S) URL without credentials/query/fragment')
        return value
    kiwix_server = kiwix_url('server_url','http://127.0.0.1:8081')
    kiwix_browser = kiwix_url('browser_url','')
    return Config(
        source=source,
        language=language,
        server=ServerConfig(_string(server, "host", "127.0.0.1"), port),
        security=SecurityConfig(
            secrets_file=_path(source.parent, _string(security, "secrets_file", "./secrets.toml")),
        ),
        storage=StorageConfig(
            data_dir=data_dir,
            database_dir=database_dir,
            cache_dir=cache_dir,
            marker_id=marker,
            require_marker=_boolean(storage, "require_marker", True),
            require_mount=_boolean(storage, "require_mount", False),
        ),
        rag=RAGConfig(
            enabled=rag_enabled,
            embedding_model=embedding_model,
            qdrant_url=rag_url,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            max_document_mb=_integer(rag, "max_document_mb", 12, 1, 40),
            search_limit=_integer(rag, "search_limit", 5, 1, 12),
        ),
        kiwix=KiwixConfig(server_url=kiwix_server,browser_url=kiwix_browser),
        updates=UpdatesConfig(enabled=_boolean(updates, "enabled", True),
                              check_interval_hours=_integer(updates, "check_interval_hours", 24, 1, 168)),
        ai=AIConfig(
            enabled=_boolean(ai, "enabled", False),
            provider=provider,
            base_url=base_url,
            default_model=_string(ai, "default_model"),
            request_timeout_seconds=_integer(ai, "request_timeout_seconds", 120, 5, 900),
            max_output_tokens=_integer(ai, "max_output_tokens", 1200, 1, 16384),
            management_url=management_url,
            npu_policy=npu_policy,
            protected_models=protected_models,
            queue_limit=_integer(ai, "queue_limit", 16, 1, 128),
            npu_load_timeout_seconds=_integer(ai, "npu_load_timeout_seconds", 120, 5, 900),
        ),
    )
