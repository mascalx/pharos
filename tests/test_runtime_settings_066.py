"""Dev6 runtime settings: user changes take effect without process restart.

No external Qdrant or Tiiny device required; all edits use the real web API and
revision-guarded config.toml instead of directly patching in-memory globals.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from pharos.ai import Gateway
from pharos.app import create_app
from pharos.config import load_config
from pharos.runtime_settings import live_snapshot, pending_restart_fields

HEAD = {'X-Pharos-Request': '1'}


def values(c):
    res = c.get('/api/settings')
    assert res.status_code == 200, res.text
    return res.json()


def save(c, snapshot, changes):
    changed = snapshot['values']
    for section, props in changes.items():
        changed[section].update(props)
    return c.put('/api/settings', headers=HEAD,
                 json={'revision': snapshot['revision'], 'values': changed})


def test_ai_max_tokens_timeout_are_effective_immediately(prepared, monkeypatch):
    original = prepared.read_text().replace('enabled=false', 'enabled=true', 1)
    prepared.write_text(original)
    observed = []

    async def fake_complete(self, model, messages):
        observed.append((self.config.max_output_tokens, self.config.request_timeout_seconds,
                         self.config.default_model))
        return 'Risposta dal modello'

    monkeypatch.setattr(Gateway, 'complete', fake_complete)
    app = create_app(load_config(prepared))
    with TestClient(app) as c:
        body = {'messages': [{'role': 'user', 'content': 'Ciao'}]}
        assert c.post('/api/chat', headers=HEAD, json={**body, 'model': 'original'}).status_code == 200
        before = values(c)
        response = save(c, before, {'ai': {'max_output_tokens': 8192,
                                            'request_timeout_seconds': 300,
                                            'default_model': 'nuovo-modello'}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False
        assert response.json()['restart_fields'] == []
        assert response.json()['applied_live'] is True
        assert c.post('/api/chat', headers=HEAD, json={**body, 'model': ''}).status_code == 200
        assert observed == [(100, 10, ''), (8192, 300, 'nuovo-modello')]
        assert app.state.gateway.gateway.config.max_output_tokens == 8192
        assert app.state.config.ai.request_timeout_seconds == 300
        assert values(c)['restart_required'] is False
        assert load_config(prepared).ai.max_output_tokens == 8192


def test_ai_runtime_fifo_queue_configuration_keeps_same_coordinator(prepared):
    app = create_app(load_config(prepared))
    queue = app.state.gateway.fifo
    with TestClient(app) as c:
        response = save(c, values(c), {'ai': {'queue_limit': 11,
                                             'npu_load_timeout_seconds': 140,
                                             'npu_policy': 'manual',
                                             'enabled': True}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False
        assert app.state.gateway.fifo is queue
        assert app.state.gateway.queue_status()['capacity'] == 11
        assert app.state.gateway.fifo._wait_seconds == 10 + 140
        assert app.state.gateway.config.npu_policy == 'manual'
        assert app.state.gateway.gateway.config.enabled is True


def test_rag_tuning_live_without_rebuilding_indexes(prepared):
    prepared.write_text(prepared.read_text() + '\n[rag]\nenabled=true\nembedding_model="embed-test"\n')
    app = create_app(load_config(prepared))
    engine = app.state.rag
    assert engine is not None
    with TestClient(app) as c:
        initial = c.get('/api/kb')
        assert initial.status_code == 200 and initial.json()['items'] == []
        response = save(c, values(c), {'rag': {'search_limit': 8,
                                              'max_document_mb': 25,
                                              'chunk_chars': 1400,
                                              'overlap_chars': 175}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False
        assert app.state.rag is engine
        assert engine.config.rag.search_limit == 8
        assert engine.config.rag.max_document_mb == 25
        assert engine.config.rag.chunk_chars == 1400
        assert c.get('/api/kb').status_code == 200


def test_language_updates_live(prepared):
    with TestClient(create_app(load_config(prepared))) as c:
        response = save(c, values(c), {'general': {'language': 'en'}})
        assert response.status_code == 200, response.text
        assert response.json()['language'] == 'en'
        assert response.json()['restart_required'] is False
        assert c.get('/api/config').json()['language'] == 'en'


def test_server_change_is_pending_and_remains_pending_across_edits(prepared):
    app = create_app(load_config(prepared))
    with TestClient(app) as c:
        response = save(c, values(c), {'server': {'port': 8089},
                                          'ai': {'max_output_tokens': 8192}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is True
        assert 'server.port' in response.json()['restart_fields']
        assert app.state.config.server.port == 8080
        assert app.state.config.ai.max_output_tokens == 8192
        pending = values(c)
        assert pending['restart_required'] is True
        assert pending['restart_fields'] == ['server.port']
        response = save(c, pending, {'ai': {'request_timeout_seconds': 180}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is True
        assert app.state.gateway.config.request_timeout_seconds == 180
        assert app.state.config.server.port == 8080
        # Restoring the actual live value removes the restart requirement.
        response = save(c, values(c), {'server': {'port': 8080}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False


def test_idle_provider_and_url_change_preserves_fifo(prepared):
    app = create_app(load_config(prepared))
    original_queue = app.state.gateway.fifo
    with TestClient(app) as c:
        response = save(c, values(c), {'ai': {'provider': 'tiiny_gateway',
                                             'base_url': 'http://127.0.0.1:9988',
                                             'max_output_tokens': 8192}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False
        assert response.json()['restart_fields'] == []
        assert app.state.gateway.config.provider == 'tiiny_gateway'
        assert app.state.gateway.config.base_url == 'http://127.0.0.1:9988'
        assert app.state.gateway.config.max_output_tokens == 8192
        assert app.state.gateway.fifo is original_queue
        assert values(c)['restart_required'] is False


def test_embedded_model_and_rag_mode_only_follow_verified_workflow(prepared):
    prepared.write_text(prepared.read_text() + '\n[rag]\nenabled=true\nembedding_model="original-embed"\n')
    app = create_app(load_config(prepared))
    with TestClient(app) as c:
        denied = save(c, values(c), {'rag': {'embedding_model': 'other-embed'}})
        assert denied.status_code == 409
        assert 'embedding_change_requires_confirmation' in denied.text
        response = save(c, values(c), {'rag': {'enabled': False}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is True
        assert response.json()['restart_fields'] == ['rag.enabled']
        assert app.state.rag is not None
        assert app.state.config.rag.enabled is True
        assert values(c)['restart_required'] is True


def test_kiwix_web_configuration_is_live(prepared):
    app = create_app(load_config(prepared))
    with TestClient(app) as c:
        response = save(c, values(c), {'kiwix': {'server_url': 'http://127.0.0.1:8085',
                                                 'browser_url': ''}})
        assert response.status_code == 200, response.text
        assert response.json()['restart_required'] is False
        assert app.state.config.kiwix.server_url == 'http://127.0.0.1:8085'
        assert load_config(prepared).kiwix.server_url == 'http://127.0.0.1:8085'


def test_revision_rejects_stale_update_without_touching_runtime(prepared):
    app = create_app(load_config(prepared))
    with TestClient(app) as c:
        baseline = values(c)
        assert save(c, baseline, {'ai': {'max_output_tokens': 4096}}).status_code == 200
        failed = save(c, baseline, {'ai': {'max_output_tokens': 8192}})
        assert failed.status_code == 409
        assert 'config_changed_reload' in failed.text
        assert app.state.config.ai.max_output_tokens == 4096
        assert load_config(prepared).ai.max_output_tokens == 4096


def test_pure_live_snapshot_only_merges_safe_fields(prepared):
    old = load_config(prepared)
    candidate = replace(old, server=replace(old.server, host='0.0.0.0'),
                        ai=replace(old.ai, base_url='http://10.0.0.9',max_output_tokens=8192))
    effective = live_snapshot(old, candidate, rag_active=False)
    assert effective.server == old.server
    assert effective.ai.base_url == old.ai.base_url
    assert effective.ai.max_output_tokens == 8192
    assert pending_restart_fields(effective, candidate) == ['server.host','ai.base_url']
