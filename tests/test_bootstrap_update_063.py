"""Cold RAG activation and conservative Ubuntu update regression tests."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.config_edit import revision
from pharos.tiiny_core import CoreAI
from pharos import ubuntu_installer as installer
from pharos.kiwix_runtime import KiwixManagedService

HEAD={'X-Pharos-Request': '1'}


def enable_ai(path: Path):
    data=path.read_text()
    assert 'enabled=false' in data
    path.write_text(data.replace('enabled=false', 'enabled=true', 1))


def test_cold_rag_embedding_catalog_and_activation_without_restart(prepared, monkeypatch):
    enable_ai(prepared)
    async def catalog(self):
        return {'mode': 'openai_compatible', 'complete': False, 'models': [
            {'id': 'nomic-embed-text', 'kind': '', 'loaded': None, 'downloaded': None}]}
    async def embedding(self, model, texts):
        assert model == 'nomic-embed-text'
        return [[.4, .8, .1]]
    monkeypatch.setattr(CoreAI, 'catalog', catalog)
    monkeypatch.setattr(CoreAI, 'embeddings', embedding)
    with TestClient(create_app(load_config(prepared))) as c:
        assert c.get('/api/config').json()['rag']['enabled'] is False
        # This was a 503 in dev2: discovery incorrectly required enabled RAG.
        r=c.get('/api/embedding-models')
        assert r.status_code==200
        assert r.json()['items'][0]['id']=='nomic-embed-text'
        assert c.get('/api/rag/reindex').json()=={'active': False, 'job': None}
        assert c.get('/api/kb').json()=={'enabled': False, 'items': []}
        rev=revision(prepared)
        assert c.post('/api/rag/activate', headers=HEAD,
                      json={'model':'nomic-embed-text', 'revision': '0'*64}).status_code==409
        assert c.post('/api/rag/activate',
                      json={'model':'nomic-embed-text', 'revision': rev}).status_code==403
        r=c.post('/api/rag/activate', headers=HEAD,
                 json={'model':'nomic-embed-text', 'revision': rev})
        assert r.status_code==200, r.text
        assert r.json()['dimensions']==3
        assert c.get('/api/config').json()['rag']=={'enabled':True, 'embedding_model':'nomic-embed-text'}
        assert c.get('/api/kb').json()['enabled'] is True
        assert load_config(prepared).rag.embedding_model=='nomic-embed-text'
        assert load_config(prepared).rag.enabled is True
        assert c.post('/api/rag/activate', headers=HEAD,
                      json={'model':'nomic-embed-text', 'revision':revision(prepared)}).status_code==409
    # A service restart must retain the activated model.
    with TestClient(create_app(load_config(prepared))) as c:
        assert c.get('/api/config').json()['rag']['enabled'] is True
        assert c.get('/api/kb').json()['enabled'] is True


def test_invalid_probe_leaves_config_and_rag_disabled(prepared,monkeypatch):
    enable_ai(prepared)
    async def invalid(self, model, texts):
        return [[float('nan'), 3.0]]
    monkeypatch.setattr(CoreAI,'embeddings',invalid)
    before=prepared.read_bytes()
    with TestClient(create_app(load_config(prepared))) as c:
        result=c.post('/api/rag/activate', headers=HEAD,
                      json={'model':'nomic-embed-text', 'revision':revision(prepared)})
        assert result.status_code==422
        assert result.json()['detail']=='embedding_vector_invalid'
        assert c.get('/api/kb').json()['enabled'] is False
    assert prepared.read_bytes()==before


def test_existing_index_cannot_be_silently_migrated_on_activation(prepared,monkeypatch):
    enable_ai(prepared)
    async def embedding(self, model, texts):
        return [[0.8, 0.1]]
    monkeypatch.setattr(CoreAI, 'embeddings', embedding)
    db=prepared.parent/'db';db.mkdir()
    with sqlite3.connect(db/'rag.sqlite3') as con:
        con.executescript('''
        CREATE TABLE kb (id TEXT PRIMARY KEY, name TEXT NOT NULL,
            description TEXT NOT NULL, embedding_model TEXT NOT NULL,
            dimensions INTEGER, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active_collection TEXT NOT NULL DEFAULT '');
        CREATE TABLE documents (id TEXT PRIMARY KEY, kb_id TEXT NOT NULL,
            filename TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
            chunks INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kb_id,sha256));
        INSERT INTO kb (id, name, description, embedding_model, active_collection)
            VALUES ('b', 'Old KB', '', 'old-embed', 'pharos_existing');
        INSERT INTO documents (id,kb_id,filename,sha256,status)
            VALUES ('d','b','file.txt','foo','indexed');
        ''')
    with TestClient(create_app(load_config(prepared))) as c:
        r=c.post('/api/rag/activate', headers=HEAD,
                 json={'model':'new-embed', 'revision':revision(prepared)})
        assert r.status_code==409
        assert r.json()['detail']=='existing_indexes_require_reindex'
        assert c.get('/api/kb').json()['enabled'] is False
    assert load_config(prepared).rag.enabled is False
    with sqlite3.connect(db/'rag.sqlite3') as con:
        assert con.execute('SELECT active_collection FROM kb').fetchone()[0]=='pharos_existing'


def test_custom_css_is_isolated_from_config_and_static_package(prepared,tmp_path):
    app=create_app(load_config(prepared))
    with TestClient(app) as c:
        assert c.get('/custom.css').status_code==200
        assert c.get('/custom.css').text==''
        local=prepared.parent/'custom.css'
        local.write_text('.notification-indicator { top: 5px; }')
        assert c.get('/custom.css').text == '.notification-indicator { top: 5px; }'
        if os.name != 'nt':
            local.unlink()
            local.symlink_to(prepared)
            assert c.get('/custom.css').text==''
        assert '/custom.css' in c.get('/').text


def test_setup_reports_update_without_touching_existing_settings(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',staticmethod(lambda:tmp_path))
    virtual=tmp_path/'venv/bin';virtual.mkdir(parents=True)
    (virtual/'pharos').write_text('stub')
    monkeypatch.setattr(installer.sys,'executable',str(virtual/'python'))
    monkeypatch.setattr(installer, '_is_network_storage',lambda _:False)
    monkeypatch.setattr(installer.QdrantInstaller,'install',lambda self:(self.directory.mkdir(parents=True,exist_ok=True),self.binary.write_text('stub')))
    monkeypatch.setattr(KiwixManagedService,'install',lambda self:(self.code_dir.mkdir(parents=True,exist_ok=True),self.binary.write_text('stub')))
    args=installer.parser().parse_args(['--no-start'])
    first=installer.setup(args,check_platform=False)
    assert first['mode']=='install'
    config=Path(first['config'])
    with config.open('a') as f:
        f.write('\n# user custom settings must survive any update\n')
    before=config.read_bytes()
    second=installer.setup(args,check_platform=False)
    assert second['mode']=='update'
    assert second['config_preserved'] is True
    assert config.read_bytes()==before


def test_installer_script_has_update_detection_backups_and_digest_lock():
    root=Path(__file__).resolve().parents[1]
    script=(root/'scripts/install-ubuntu.sh').read_text(encoding='utf-8')
    assert "if [[ -f \"$CONFIG\" ]]" in script
    assert 'config-backups' in script
    assert 'flock -n 9' in script
    assert 'SOURCE_HASH' in script
    assert 'PHAROS, ma il TOML indicato non è stato trovato' in script


def test_update_script_never_forces_git_or_deletes_local_css():
    root=Path(__file__).resolve().parents[1]
    script=(root/'scripts/update-ubuntu.sh').read_text(encoding='utf-8')
    assert 'pull --ff-only' in script
    assert 'css-backups' in script
    assert 'custom.css' in script
    assert 'reset --hard' not in script


def test_activation_allows_correcting_initial_nonindexed_model(prepared,monkeypatch):
    enable_ai(prepared)
    with prepared.open('a') as output:
        output.write('\n[rag]\nenabled=false\nembedding_model="wrong-initial-model"\n')
    async def embedding(self, model, texts):
        return [[1.0, 2.0]]
    monkeypatch.setattr(CoreAI,'embeddings',embedding)
    with TestClient(create_app(load_config(prepared))) as c:
        r=c.post('/api/rag/activate',headers=HEAD,json={'model':'correct-embed','revision':revision(prepared)})
        assert r.status_code==200, r.text
    assert load_config(prepared).rag.embedding_model=='correct-embed'
