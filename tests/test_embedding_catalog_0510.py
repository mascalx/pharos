"""Regression tests: downloaded Tiiny models must never be hidden by eager probing."""
from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.embedding_catalog import embedding_candidates
from test_rag import rag_ready


def test_embedding_candidates_include_unloaded_and_unknown_model_types():
    installed = [
        {'id': f'Qwen/Qwen3-Embedding-{n}', 'kind': 'Text Embedding', 'downloaded': True,
         'loaded': n == 0} for n in range(27)
    ]
    installed += [
        {'id': 'local/unnamed-model', 'kind': '', 'downloaded': True, 'loaded': False},
        {'id': 'local/chat-model', 'kind': 'Text Generation', 'downloaded': True, 'loaded': True},
    ]
    result = embedding_candidates({'mode': 'tiiny_direct', 'complete': True, 'models': installed}, 'former-embedding')
    assert len(result['models']) == 29  # 27 embeddings + unknown + configured-only
    assert 'Qwen/Qwen3-Embedding-26' in result['models']
    assert 'local/unnamed-model' in result['models']
    assert 'local/chat-model' not in result['models']
    assert result['items'][0]['configured_only'] is True
    assert next(x for x in result['items'] if x['id'] == 'Qwen/Qwen3-Embedding-26')['loaded'] is False
    assert next(x for x in result['items'] if x['id'] == 'local/unnamed-model')['candidate_basis'] == 'unknown'


def test_embedding_candidates_generic_incomplete_never_claims_downloaded():
    result = embedding_candidates({'mode': 'tiiny_gateway', 'complete': False,
        'source': 'openai_compatible', 'reason': 'gateway_catalog_not_exposed',
        'models': [
            {'id': 'nomic-embed-text', 'downloaded': None, 'loaded': None},
            {'id': 'ordinary-chat', 'downloaded': None, 'loaded': None},
        ]}, '')
    assert result['catalog_complete'] is False
    assert result['models'] == ['nomic-embed-text']
    assert result['catalog_reason'] == 'gateway_catalog_not_exposed'


def test_kb_api_lists_all_native_models_without_inference(prepared: Path, monkeypatch):
    cfg_path = rag_ready(prepared)
    cfg_path.write_text(cfg_path.read_text().replace('provider="openai_compatible"',
        'provider="tiiny_direct"'))
    native_models = [{'id': f'Qwen/embedding-{n}', 'status': 'downloaded', 'type': 'Text Embedding'}
                     for n in range(25)]
    native_models += [{'id': 'chat', 'type': 'Text Generation', 'status': 'downloaded'}]
    requests = []
    original = httpx.AsyncClient
    def handler(request):
        requests.append((request.method, request.url.host, request.url.path))
        if request.url.path == '/api/v1/models/':
            return httpx.Response(200, json={'data': native_models})
        if request.url.path == '/api/v1/models/running':
            return httpx.Response(200, json=[{'id': 'chat'}])
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'chat'}]})
        if request.url.path in ('/v1/embeddings', '/api/v1/models/launch'):
            raise AssertionError('catalog retrieval may not trigger inference or NPU action')
        return httpx.Response(404)
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'AsyncClient',
        lambda *args, **kwargs: original(*args, **kwargs, transport=transport))
    with TestClient(create_app(load_config(cfg_path))) as client:
        result = client.get('/api/embedding-models')
        assert result.status_code == 200, result.text
        catalog = result.json()
        assert catalog['catalog_complete'] is True
        assert len(catalog['models']) == 26  # configured model and all 25 downloaded embedding models
        assert 'Qwen/embedding-24' in catalog['models']
        assert 'chat' not in catalog['models']
        assert next(x for x in catalog['items'] if x['id'] == 'Qwen/embedding-24')['loaded'] is False
    assert not any(method == 'POST' for method, _, _ in requests)
    assert not any(path == '/v1/models' for _, _, path in requests)


def test_kb_gateway_uses_only_standard_models_when_extended_inventory_unavailable(prepared: Path, monkeypatch):
    cfg_path = rag_ready(prepared)
    cfg_path.write_text(cfg_path.read_text().replace('provider="openai_compatible"',
        'provider="tiiny_gateway"'))
    original = httpx.AsyncClient
    def handler(request):
        if request.url.path == '/gateway/v1/models':
            return httpx.Response(404)  # older Gateway without management API
        assert request.url.path == '/v1/models'
        return httpx.Response(200, json={'data': [{'id': 'loaded-embed'}]})
    monkeypatch.setattr(httpx, 'AsyncClient',
        lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(load_config(cfg_path))) as client:
        catalog = client.get('/api/embedding-models').json()
        assert catalog['catalog_complete'] is False
        assert catalog['catalog_reason'] == 'gateway_catalog_not_exposed'
        assert catalog['mode'] == 'tiiny_gateway'
        assert catalog['models'] == ['stub-embed', 'loaded-embed']


def test_kb_frontend_never_probes_every_model_while_listing():
    source = (Path(__file__).resolve().parents[1] / 'src/pharos/web/knowledge.js').read_text(encoding='utf-8')
    listing = source.split('async function loadEmbeddingModels(refresh=false){', 1)[1].split(
        'async function refreshReindex(){', 1)[0]
    assert "api('embedding-models'+(refresh?'?refresh=true':''))" in listing
    assert "api('embedding-models/probe'" not in listing
    assert '.slice(0,20)' not in listing
    assert "const entries=Array.isArray(catalog.items)" in listing
    assert "kbModelCatalogGatewayLimited" in source


def test_kb_gateway_model_discovery_failure_preserves_configured_choice(prepared: Path, monkeypatch):
    cfg_path = rag_ready(prepared)
    cfg_path.write_text(cfg_path.read_text().replace('provider="openai_compatible"',
        'provider="tiiny_gateway"\nmanagement_url="http://device.local"'))
    requests=[]
    original=httpx.AsyncClient
    def handler(req):
        requests.append((req.url.host, req.url.path))
        assert req.url.host == '127.0.0.1' and req.url.path in ('/gateway/v1/models','/v1/models')
        return httpx.Response(503, text='service unavailable')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(load_config(cfg_path))) as client:
        response=client.get('/api/embedding-models')
        assert response.status_code == 200, response.text
        result=response.json()
        assert result['models'] == ['stub-embed']
        assert result['items'][0]['configured_only'] is True
        assert result['items'][0]['loaded'] is None
        assert result['catalog_complete'] is False
        assert result['catalog_reason'] == 'gateway_models_unavailable'
        assert result['catalog_error_code'] == 'gateway_unavailable'
        assert result['mode'] == 'tiiny_gateway'
        catalog=client.get('/api/ai/catalog').json()
        assert catalog['reason'] == 'gateway_models_unavailable'
        assert catalog['error_code'] == 'gateway_unavailable'
        # Other provider endpoints still report real connection failures.
        assert client.get('/api/models').status_code == 502
    assert requests and all(host == '127.0.0.1' for host,_ in requests)


def test_kb_gateway_auth_error_is_not_reported_as_success(prepared: Path, monkeypatch):
    cfg_path = rag_ready(prepared)
    cfg_path.write_text(cfg_path.read_text().replace('provider="openai_compatible"',
                                                       'provider="tiiny_gateway"'))
    original=httpx.AsyncClient
    def handler(request):
        assert request.url.path in ('/gateway/v1/models','/v1/models')
        return httpx.Response(401, json={'detail':'no access'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kw:
        original(*args, **kw, transport=httpx.MockTransport(handler)))
    with TestClient(create_app(load_config(cfg_path))) as client:
        result=client.get('/api/embedding-models')
        assert result.status_code == 200
        assert result.json()['catalog_reason'] == 'gateway_models_unavailable'
        assert result.json()['catalog_error_code'] == 'gateway_auth_failed'
        assert client.get('/api/ai/catalog').json()['error_code'] == 'gateway_auth_failed'
        assert client.get('/api/models').status_code == 502
