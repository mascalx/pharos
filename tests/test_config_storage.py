from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import pytest

from pharos.cli import main
from pharos.config import ConfigError, load_config
from pharos.storage import check_data_volume, initialize_volume, MARKER_FILE


def test_init_creates_config_with_unique_marker(tmp_path: Path):
    assert main(["init", "--directory", str(tmp_path)]) == 0
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.language == "en"
    assert cfg.storage.marker_id != "REPLACE_ON_INIT"
    assert cfg.storage.data_dir == tmp_path / "pharos-data"


def test_init_never_overwrites(tmp_path: Path):
    assert main(["init", "--directory", str(tmp_path)]) == 0
    original = (tmp_path / "config.toml").read_text()
    assert main(["init", "--directory", str(tmp_path)]) == 1
    assert (tmp_path / "config.toml").read_text() == original


def test_storage_mount_guard(prepared: Path):
    cfg = load_config(prepared)
    assert check_data_volume(cfg.storage).available
    strict = replace(cfg.storage, require_mount=True)
    assert check_data_volume(strict).reason == "mount_missing"
    with pytest.raises(ValueError, match="not a mount point"):
        initialize_volume(strict, str(strict.data_dir))


def test_wrong_path_refused(prepared: Path):
    cfg = load_config(prepared)
    with pytest.raises(ValueError, match="Confirmation does not match"):
        initialize_volume(cfg.storage, str(cfg.storage.cache_dir))


def test_bad_marker_fail_closed(prepared: Path):
    cfg = load_config(prepared)
    (cfg.storage.data_dir / MARKER_FILE).write_text("unexpected", encoding="utf-8")
    assert check_data_volume(cfg.storage).reason == "marker_missing_or_wrong"


def test_storage_no_automatic_mkdir(prepared: Path):
    cfg = load_config(prepared)
    (cfg.storage.data_dir / MARKER_FILE).unlink()
    cfg.storage.data_dir.rmdir()
    assert not check_data_volume(cfg.storage).available
    assert not cfg.storage.data_dir.exists()


def test_marker_does_not_overwrite_another_volume(prepared: Path):
    cfg = load_config(prepared)
    (cfg.storage.data_dir / MARKER_FILE).write_text(str(uuid4()), encoding="utf-8")
    with pytest.raises(ValueError, match="different PHAROS marker"):
        initialize_volume(cfg.storage, str(cfg.storage.data_dir))


def test_repeated_storage_init_idempotent(prepared: Path):
    cfg = load_config(prepared)
    marker = initialize_volume(cfg.storage, str(cfg.storage.data_dir))
    assert marker.read_text().strip() == cfg.storage.marker_id


def test_invalid_bool_type(prepared: Path):
    prepared.write_text(prepared.read_text().replace('require_mount=false', 'require_mount="false"'))
    with pytest.raises(ConfigError, match="true or false"):
        load_config(prepared)


def test_reject_nested_storage(prepared: Path):
    prepared.write_text(prepared.read_text().replace('database_dir="./db"', 'database_dir="./storage/db"'))
    with pytest.raises(ConfigError, match="separate"):
        load_config(prepared)


def test_reject_credential_in_ai_url(prepared: Path):
    prepared.write_text(prepared.read_text().replace('http://127.0.0.1:9876', 'http://user:pass@127.0.0.1:9876'))
    with pytest.raises(ConfigError, match="bare http"):
        load_config(prepared)


def test_check_reports_success(prepared: Path):
    assert main(['check','--config',str(prepared)]) == 0


def test_missing_file_fails():
    with pytest.raises(ConfigError, match="not found"):
        load_config('/nonexistent/path/config.toml')


def test_rag_requires_explicit_embedding_model(prepared: Path):
    with prepared.open('a', encoding='utf-8') as stream:
        stream.write('\n[rag]\nenabled=true\n')
    with pytest.raises(ConfigError, match='rag.embedding_model'):
        load_config(prepared)
    config = prepared.read_text().replace('enabled=true\n', 'enabled=true\nembedding_model="Qwen3-Embedding-4B"\n')
    prepared.write_text(config)
    assert load_config(prepared).rag.embedding_model == 'Qwen3-Embedding-4B'
