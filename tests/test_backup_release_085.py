"""Release qualification for non-destructive PHAROS backup and restore.

Tests use tiny *synthetic* SQLite/Qdrant/ZIM/PMTiles fixtures, never the
production VM or NAS. CLI still requires both systemd services stopped.
"""
import importlib.util
from pathlib import Path
import shutil
import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'pharos-backup.py'
SPEC = importlib.util.spec_from_file_location('pharos_backup_cli', SOURCE)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


@pytest.fixture
def sample(tmp_path):
    for name in ('cfg', 'db', 'nas', 'cache', 'backups'):
        (tmp_path / name).mkdir()
    cfg = tmp_path / 'cfg' / 'config.toml'
    cfg.write_text(f'''[security]
secrets_file="./secrets.toml"
[storage]
data_dir="{tmp_path / 'nas'}"
database_dir="{tmp_path / 'db'}"
cache_dir="{tmp_path / 'cache'}"
require_mount=false
''', encoding='utf-8')
    (tmp_path / 'cfg' / 'secrets.toml').write_text('key="secret"')
    (tmp_path / 'db' / 'rag.sqlite3').write_bytes(b'synthetic sqlite data')
    (tmp_path / 'db' / 'qdrant').mkdir()
    (tmp_path / 'db' / 'qdrant' / 'collection.bin').write_bytes(b'qdrant fixture')
    (tmp_path / 'nas' / 'kiwix').mkdir()
    (tmp_path / 'nas' / 'kiwix' / 'test.zim').write_bytes(b'zim archive')
    (tmp_path / 'nas' / 'maps').mkdir()
    (tmp_path / 'nas' / 'maps' / 'test.pmtiles').write_bytes(b'map archive')
    (tmp_path / 'cache' / 'file.bin').write_bytes(b'cached asset')
    return tmp_path, cfg


def test_full_roundtrip_and_no_in_place_overwrite(sample):
    root, cfg = sample
    destination = root / 'backups' / 'snapshot'
    backup.create_backup(cfg, destination, check_services=False)
    manifest = backup.verify_backup(destination)
    names = {entry['path'] for entry in manifest['files']}
    assert 'database/qdrant/collection.bin' in names
    assert 'data/kiwix/test.zim' in names
    assert 'data/maps/test.pmtiles' in names
    assert 'configuration/secrets.toml' in names
    with pytest.raises(ValueError):
        backup.restore_empty(destination, check_services=False)
    for name in ('cfg', 'db', 'nas', 'cache'):
        shutil.rmtree(root / name)
    backup.restore_empty(destination, check_services=False)
    assert (root / 'nas' / 'kiwix' / 'test.zim').read_bytes() == b'zim archive'
    assert (root / 'db' / 'qdrant' / 'collection.bin').read_bytes() == b'qdrant fixture'
    assert (root / 'cfg' / 'secrets.toml').read_text() == 'key="secret"'


def test_corrupted_backup_refused(sample):
    root, cfg = sample
    destination = root / 'backups' / 'snapshot'
    backup.create_backup(cfg, destination, check_services=False)
    (destination / 'database' / 'rag.sqlite3').write_bytes(b'corrupted sqlite data')
    with pytest.raises(ValueError):
        backup.verify_backup(destination)


def test_no_backup_inside_source(sample):
    root, cfg = sample
    with pytest.raises(ValueError):
        backup.create_backup(cfg, root / 'nas' / 'backup', check_services=False)


def test_symlink_cannot_escape_data_source(sample):
    root, cfg = sample
    (root / 'nas' / 'escape').symlink_to(root / 'db' / 'rag.sqlite3')
    with pytest.raises(ValueError):
        backup.create_backup(cfg, root / 'backups' / 'snapshot', check_services=False)


def test_restore_rejects_nonempty_targets(sample):
    root, cfg = sample
    destination = root / 'backups' / 'snapshot'
    backup.create_backup(cfg, destination, check_services=False)
    with pytest.raises(ValueError):
        backup.restore_empty(destination, check_services=False)
