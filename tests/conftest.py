from __future__ import annotations
from pathlib import Path
from uuid import uuid4
import pytest
from pharos.config import load_config
from pharos.storage import initialize_volume

@pytest.fixture
def prepared(tmp_path: Path):
    config = tmp_path / "config.toml"
    marker = str(uuid4())
    config.write_text(f'''[general]
language="it"
[server]
host="127.0.0.1"
port=8080
[security]
secrets_file="./secrets.toml"
[storage]
data_dir="./storage"
database_dir="./db"
cache_dir="./cache"
marker_id="{marker}"
require_marker=true
require_mount=false
[ai]
enabled=false
provider="openai_compatible"
base_url="http://127.0.0.1:9876"
default_model=""
request_timeout_seconds=10
max_output_tokens=100
''' ,encoding="utf-8")
    (tmp_path / "storage").mkdir()
    cfg = load_config(config)
    initialize_volume(cfg.storage, str(tmp_path / "storage"))
    return config


@pytest.fixture(autouse=True)
def isolated_updater_state(tmp_path, monkeypatch):
    """Never read/write a real installation marker or updater state in tests."""
    from pharos import web_updater, update_check
    state = tmp_path / "isolated-updater"
    state.mkdir()
    monkeypatch.setattr(web_updater, "STATE_DIR", state)
    monkeypatch.setattr(web_updater, "STATE_FILE", state / "web-update.json")
    # Explicit marker arguments in updater tests still take precedence.
    original = update_check.UpdateChecker.__init__

    def init(self, config, marker_path=None):
        original(self, config, marker_path if marker_path is not None
                 else state / "update-source.json")

    monkeypatch.setattr(update_check.UpdateChecker, "__init__", init)
    return state
