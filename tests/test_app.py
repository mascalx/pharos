from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from pharos.ai import Gateway, GatewayError
from pharos.app import create_app
from pharos.config import load_config


def test_anonymous_dashboard_and_assets(prepared: Path):
    with TestClient(create_app(load_config(prepared))) as client:
        home = client.get('/')
        assert home.status_code == 200
        assert 'PHAROS' in home.text
        assert home.headers['content-security-policy'].startswith("default-src 'self'")
        assert home.headers['cache-control'] == 'no-store'
        assert client.get('/static/app.js').status_code == 200
        assert client.get('/healthz').json()['status'] == 'ok'
        assert client.get('/api/config').json()['ai']['enabled'] is False
        assert client.get('/api/models').json() == {'models': []}


def test_block_cross_origin_mutations(prepared: Path):
    with TestClient(create_app(load_config(prepared))) as client:
        payload={'model':'test','messages':[{'role':'user','content':'hello'}]}
        assert client.post('/api/chat', json=payload).status_code == 403
        assert client.post('/api/chat', json=payload, headers={'X-Pharos-Request':'1','Origin':'https://evil.example'}).status_code == 403


def test_chat_validation(prepared: Path):
    with TestClient(create_app(load_config(prepared))) as client:
        headers={'X-Pharos-Request':'1'}
        assert client.post('/api/chat', json={'model':'','messages':[{'role':'user','content':'Hi'}]}, headers=headers).status_code == 422
        assert client.post('/api/chat', json={'model':'a','messages':[{'role':'system','content':'danger'}]}, headers=headers).status_code == 422
        assert client.post('/api/chat', json={'model':'a','messages':[{'role':'assistant','content':'Hi'}]}, headers=headers).status_code == 422
        assert client.post('/api/chat', json={'model':'a','messages':[{'role':'user','content':'Hi'}]}, headers=headers).status_code == 503


def test_ai_models_and_chat(prepared: Path, monkeypatch):
    prepared.write_text(prepared.read_text().replace('enabled=false','enabled=true'))
    async def mock_models(self, timeout_seconds=None):
        return ['test-model', 'test-two']
    async def mock_complete(self, model, messages):
        assert model == 'test-model'
        assert messages == [{'role':'user','content':'hello'}]
        return '<b>safe text in browser</b>'
    monkeypatch.setattr(Gateway, 'models', mock_models)
    monkeypatch.setattr(Gateway, 'complete', mock_complete)
    with TestClient(create_app(load_config(prepared))) as client:
        assert client.get('/api/models').json()['models'] == ['test-model','test-two']
        assert client.get('/api/status').json()['ai'] == 'available'
        result = client.post('/api/chat', json={'model':'test-model','messages':[{'role':'user','content':'hello'}]}, headers={'X-Pharos-Request':'1'})
        assert result.status_code == 200
        assert result.json()['answer'] == '<b>safe text in browser</b>'


def test_unavailable_ai_keeps_core_running(prepared: Path, monkeypatch):
    prepared.write_text(prepared.read_text().replace('enabled=false','enabled=true'))
    async def down(self, timeout_seconds=None):
        raise GatewayError('gateway_unavailable')
    monkeypatch.setattr(Gateway, 'models', down)
    with TestClient(create_app(load_config(prepared))) as client:
        # A failed model-catalog endpoint does not prove chat inference is down.
        assert client.get('/api/status').json()['ai'] == 'unknown'
        assert client.get('/api/models').status_code == 502
        assert client.get('/').status_code == 200


def test_missing_volume_still_diagnostic(prepared: Path):
    cfg=load_config(prepared)
    (cfg.storage.data_dir/'.pharos-volume').unlink()
    with TestClient(create_app(cfg)) as client:
        data=client.get('/api/status').json()
        assert data['storage']['available'] is False
        assert not (cfg.storage.data_dir/'.pharos-volume').exists()
