"""Core-integrated migration regression tests, no external service required."""
from __future__ import annotations

import asyncio
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient

from pharos import __version__
from pharos.app import create_app
from pharos.config import load_config
from pharos.home import FEATURES, HomeError, home_preferences, save_home_preferences
from pharos.kiwix import make_router
from pharos.kiwix.library import safe_browser_url
from pharos.kiwix_runtime import KiwixManagedService
from pharos.download_jobs import DownloadJobs, JobError

HEAD = {'X-Pharos-Request': '1'}


def test_integrated_version_and_no_dynamic_modules(prepared):
    import tomllib
    project = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
    assert __version__ == project['project']['version']
    with TestClient(create_app(load_config(prepared))) as c:
        assert c.get('/').status_code == 200
        assert c.get('/api/modules').status_code == 404
        assert c.post('/api/modules/knowledge/uninstall', headers=HEAD).status_code == 404
        assert c.get('/login').status_code == 404
        assert c.post('/api/auth/login', headers=HEAD).status_code == 404
        assert c.get('/extensions/kiwix/status').status_code == 200
        assert c.get('/api/extensions/kiwix/status').status_code == 200
        assert c.get('/static/home.js').status_code == 200
        assert c.get('/api/config').json()['security']['login_required'] is False


def test_home_defaults_and_atomic_roundtrip(prepared):
    config = load_config(prepared)
    assert home_preferences(config.source) == {'order': list(FEATURES), 'hidden': []}
    changed = {'order': list(reversed(FEATURES)), 'hidden': ['kiwix', 'chat']}
    result = save_home_preferences(config.source, changed)
    assert result == changed
    assert home_preferences(config.source) == changed
    assert load_config(config.source) == config
    assert '[home]' in config.source.read_text()


@pytest.mark.parametrize('data', [
    {'order': ['chat'], 'hidden': []},
    {'order': list(FEATURES), 'hidden': ['modules']},
    {'order': list(FEATURES), 'hidden': ['chat', 'chat']},
    {'order': list(FEATURES), 'hidden': 'chat'},
    {'order': list(FEATURES), 'hidden': [], 'extra': 1},
])
def test_home_rejects_unexpected_preferences(prepared, data):
    before = prepared.read_bytes()
    with pytest.raises(HomeError):
        save_home_preferences(prepared, data)
    assert prepared.read_bytes() == before


def test_home_api_guard_and_persistence(prepared):
    with TestClient(create_app(load_config(prepared))) as c:
        home = c.get('/api/home').json()
        assert home['order'] == list(FEATURES)
        home['hidden'] = ['knowledge']
        assert c.put('/api/home', json=home).status_code == 403
        assert c.put('/api/home', json=home, headers={**HEAD, 'Origin': 'https://evil.example'}).status_code == 403
        assert c.put('/api/home', json=home, headers=HEAD).status_code == 200
    with TestClient(create_app(load_config(prepared))) as c:
        assert c.get('/api/home').json()['hidden'] == ['knowledge']
        # Hiding the Home tile does NOT disable the RAG endpoint.
        assert c.get('/api/kb').status_code == 200


def test_kiwix_static_routes_without_binary(prepared):
    with TestClient(create_app(load_config(prepared))) as c:
        status = c.get('/extensions/kiwix/status').json()
        assert status['installed'] is False
        for prefix in ('/api/extensions/kiwix', '/extensions/kiwix'):
            # A missing binary returns a controlled API error, not a missing route.
            reply = c.get(prefix+'/books')
            assert reply.status_code == 502
            assert reply.json()['detail'] == 'kiwix_runtime_missing'
            link = c.get(prefix+'/search-link', params={
                'pattern': 'italia', 'name': 'wikipedia_it'})
            assert link.status_code == 200
            assert link.json()['url'].startswith('/kiwix/search?')
        assert c.get('/kiwix/content/name').status_code == 503


def test_zim_jobs_have_no_runtime_install_switch(prepared):
    cfg = load_config(prepared)
    service = KiwixManagedService(cfg)
    jobs = DownloadJobs(cfg, service)
    with pytest.raises(JobError, match='job_kind_invalid'):
        jobs.submit('install-kiwix')
    with pytest.raises(JobError, match='zim_official_url_required'):
        jobs.submit('download-zim', 'http://127.0.0.1/test.zim')


def test_installed_knowledge_source_is_core_package():
    from pharos.knowledge import RAGEngine, ReindexService
    assert RAGEngine.__module__ == 'pharos.knowledge.rag'
    assert ReindexService.__module__ == 'pharos.knowledge.reindex'


def test_bundled_source_has_no_legacy_dynamic_loader():
    import pharos
    path = Path(pharos.__file__).parent
    assert not (path / 'module_manager.py').exists()
    assert not (path / 'module_packages.py').exists()
    assert not (path / 'offline_modules').exists()
