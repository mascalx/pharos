"""0.5.0.dev8: native Tiiny Core, FIFO, legacy migration, security and provider modes."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.ai import GatewayError
from pharos.app import create_app
from pharos.config import AIConfig, ConfigError, load_config
from pharos.secrets import read_secrets
from pharos.tiiny_core import CoreAI, FIFOCoordinator

H = {'X-Pharos-Request': '1'}


def direct(prepared: Path, policy: str = 'manual') -> Path:
    text = prepared.read_text().replace('provider="openai_compatible"', 'provider="tiiny_direct"')
    text = text.replace('enabled=false', 'enabled=true')
    text = text.replace('max_output_tokens=100', f'max_output_tokens=100\nnpu_policy="{policy}"\nnpu_load_timeout_seconds=5')
    prepared.write_text(text)
    return prepared


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kw: original(*args, **kw, transport=transport))


def test_fresh_install_defaults_direct_and_legacy_config_kept(prepared):
    original = load_config(prepared)
    assert original.ai.provider == 'openai_compatible'
    text = prepared.read_text().replace('provider="openai_compatible"\n', '')
    prepared.write_text(text)
    assert load_config(prepared).ai.provider == 'tiiny_direct'
    assert load_config(prepared).ai.queue_limit == 16
    assert load_config(prepared).ai.npu_policy == 'protected'


def test_bad_provider_and_management_urls_rejected(prepared):
    prepared.write_text(prepared.read_text().replace('provider="openai_compatible"', 'provider="unknown"'))
    with pytest.raises(ConfigError, match='ai.provider'):
        load_config(prepared)
    prepared.write_text(prepared.read_text().replace('provider="unknown"',
        'provider="tiiny_gateway"\nmanagement_url="file:///etc/passwd"'))
    with pytest.raises(ConfigError, match='ai.management_url'):
        load_config(prepared)


def test_direct_catalog_includes_unloaded_models_and_load_unload(prepared, monkeypatch):
    direct(prepared)
    active = {'local/qwen'}
    calls = []
    def handler(request):
        calls.append((request.method, request.url.raw_path.decode()))
        if request.url.path == '/api/v1/models/':
            return httpx.Response(200, json={'data': [
                {'id':'local/qwen','status':'downloaded','type':'Text Generation','npu_usage':30},
                {'id':'local/embedding','status':'downloaded','type':'Text Embedding','npu_usage':4},
            ]})
        if request.url.path == '/api/v1/models/running':
            return httpx.Response(200, json=[{'model_id':m} for m in active])
        if request.url.path.endswith('/launch'):
            active.add('local/embedding');return httpx.Response(200, json={'ok':True})
        if request.url.path.endswith('/stop'):
            active.remove('local/embedding');return httpx.Response(200, json={'ok':True})
        if request.url.path == '/v1/chat/completions':
            return httpx.Response(200, json={'choices':[{'message':{'content':'offline local'}}]})
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        catalog=client.get('/api/ai/catalog').json()
        assert catalog['complete'] is True
        assert {item['id'] for item in catalog['models']} == {'local/qwen','local/embedding'}
        assert next(x for x in catalog['models'] if x['id']=='local/embedding')['loaded'] is False
        assert set(client.get('/api/models').json()['models']) == {'local/qwen','local/embedding'}
        request={'model':'local/embedding','messages':[{'role':'user','content':'Hi'}]}
        result=client.post('/api/chat', headers=H, json=request)
        assert result.status_code == 502 and result.json()['detail'] == 'tiiny_model_not_loaded'
        assert client.post('/api/ai/models/launch',json={'model':'local/embedding'}).status_code==403
        assert client.post('/api/ai/models/launch',json={'model':'local/embedding'},headers=H).status_code==200
        assert client.post('/api/chat', headers=H, json=request).json()['answer']=='offline local'
        assert client.post('/api/ai/models/stop',headers=H,json={'model':'local/embedding'}).status_code==200
        assert not next(x for x in client.get('/api/ai/catalog').json()['models'] if x['id']=='local/embedding')['loaded']
        assert all('/delete' not in path for method,path in calls)
        assert ('POST','/api/v1/models/local%2Fembedding/launch') in calls


def test_direct_automatic_loads_unloaded_model_without_eviction(prepared,monkeypatch):
    direct(prepared, policy='protected')
    active = set()
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path == '/api/v1/models/':
            return httpx.Response(200,json=[{'id':'qwen','status':'downloaded'}])
        if req.url.path == '/api/v1/models/running':
            return httpx.Response(200,json=[{'id':model} for model in active])
        if req.url.path.endswith('/launch'):
            active.add('qwen'); return httpx.Response(202,json={'launched':True})
        if req.url.path == '/v1/chat/completions':
            return httpx.Response(200,json={'choices':[{'message':{'content':'worked'}}]})
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        result=client.post('/api/chat',headers=H,
                           json={'model':'qwen','messages':[{'role':'user','content':'test'}]})
        assert result.status_code==200, result.text
        assert result.json()['answer']=='worked'
    assert '/api/v1/models/qwen/launch' in calls
    assert not any(x.endswith('/stop') or 'delete' in x for x in calls)


def test_native_unavailable_must_not_fallback_to_loaded_only(prepared, monkeypatch):
    direct(prepared)
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data':[{'id':'qwen-loaded'}]})
        return httpx.Response(404)
    mock_http(monkeypatch, handler)
    with TestClient(create_app(load_config(prepared))) as client:
        resp=client.get('/api/ai/catalog')
        assert resp.status_code==502 and resp.json()['detail']=='tiiny_native_api_unavailable'
        assert client.get('/api/models').status_code==502
    assert '/v1/models' not in calls


def test_gateway_and_generic_degrade_to_truthful_incomplete_catalog(prepared, monkeypatch):
    prepared.write_text(prepared.read_text().replace('enabled=false','enabled=true').replace('provider="openai_compatible"', 'provider="tiiny_gateway"'))
    def handler(request):
        if request.url.path=='/v1/models':return httpx.Response(200,json={'data':[{'id':'one-loaded'}]})
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get('/api/ai/catalog').json()
        assert data['complete'] is False and data['reason']=='gateway_catalog_not_exposed'
        assert data['models'][0]['loaded'] is None
        assert client.post('/api/ai/models/stop',headers=H,json={'model':'one-loaded'}).status_code==409
    prepared.write_text(prepared.read_text().replace('provider="tiiny_gateway"','provider="openai_compatible"'))
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get('/api/ai/catalog').json()
        assert data['complete'] is False and data['reason']=='generic_provider'
        assert client.get('/api/models').json()['models']==['one-loaded']


def test_gateway_never_calls_tiinyos_even_with_legacy_management_url_and_key(prepared, monkeypatch):
    # Older configurations may contain native device URLs and separate keys.
    # They must not be used in Gateway mode after the architecture correction.
    prepared.write_text(prepared.read_text().replace('enabled=false', 'enabled=true').replace(
        'provider="openai_compatible"', 'provider="tiiny_gateway"\nmanagement_url="http://device.local"'))
    requests = []
    def handler(request):
        requests.append((request.method, request.url.host, request.url.path,
                         request.headers.get('authorization')))
        assert request.url.host == '127.0.0.1', 'PHAROS bypassed the external Gateway'
        assert request.url.path.startswith(('/v1/', '/gateway/v1/')), 'native TiinyOS endpoint must not be called'
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id':'loaded-embedding'}]})
        if request.url.path == '/v1/embeddings':
            return httpx.Response(200, json={'data':[{'index':0, 'embedding':[0.1,0.2,0.3]}]})
        if request.url.path == '/v1/chat/completions':
            return httpx.Response(200, json={'choices':[{'message': {'content': 'ok'}}]})
        return httpx.Response(404)
    mock_http(monkeypatch, handler)
    with TestClient(create_app(load_config(prepared))) as client:
        assert client.put('/api/settings/management-key', headers=H,
                          json={'api_key':'legacy-secret-device'}).status_code == 200
        data = client.get('/api/ai/catalog').json()
        assert data['complete'] is False and data['reason'] == 'gateway_catalog_not_exposed'
        assert [item['id'] for item in data['models']] == ['loaded-embedding']
        assert client.get('/api/models').json()['models'] == ['loaded-embedding']
        assert client.post('/api/ai/models/launch', headers=H,
                           json={'model':'loaded-embedding'}).status_code == 409
        assert client.post('/api/chat', headers=H, json={
            'model':'loaded-embedding','messages':[{'role':'user','content':'Hi'}]
        }).json()['answer'] == 'ok'
        assert client.get('/api/settings').json()['management_api_key_configured'] is True
        assert 'legacy-secret-device' not in str(client.get('/api/settings').json())
    assert all(host == '127.0.0.1' for _,host,_,_ in requests)
    assert read_secrets(load_config(prepared).security.secrets_file)['ai']['management_api_key'] == 'legacy-secret-device'


def test_fifo_strict_order_full_and_cancellation_cleanup():
    async def exercise():
        fifo=FIFOCoordinator(limit=2,wait_seconds=10)
        release=asyncio.Event()
        started=asyncio.Event()
        order=[]
        async def first():
            order.append(1);started.set();await release.wait();return 1
        async def later():
            order.append(2);return 2
        task1=asyncio.create_task(fifo.run(first));await started.wait()
        task2=asyncio.create_task(fifo.run(later));await asyncio.sleep(0)
        assert fifo.status()['active'] and fifo.status()['waiting']==1
        with pytest.raises(GatewayError,match='ai_queue_full'):
            await fifo.run(later)
        release.set()
        assert await task1==1 and await task2==2 and order==[1,2]
        assert fifo.status()['waiting']==0 and fifo.status()['active'] is False
        release.clear();started.clear()
        task3=asyncio.create_task(fifo.run(first));await started.wait()
        blocked=asyncio.create_task(fifo.run(later));await asyncio.sleep(0)
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):await blocked
        assert fifo.status()['waiting']==0
        release.set();await task3
        assert fifo.status()['waiting']==0 and fifo.status()['active'] is False
    asyncio.run(exercise())


def test_native_model_endpoints_require_guarded_mutations(prepared):
    with TestClient(create_app(load_config(prepared))) as client:
        assert client.get('/api/ai/queue').status_code==200
        assert client.post('/api/ai/models/launch',json={'model':'x'}).status_code==403


def test_installed_catalog_with_failed_running_endpoint_cannot_guess_residency(prepared, monkeypatch):
    direct(prepared, policy='protected')
    def handler(request):
        if request.url.path=='/api/v1/models/':
            return httpx.Response(200,json=[{'id':'unknown-residency','status':'downloaded'}])
        if request.url.path=='/api/v1/models/running':
            return httpx.Response(404)
        if request.url.path=='/v1/chat/completions':
            raise AssertionError('inference must not run without verified residency')
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get('/api/ai/catalog').json()
        assert data['complete'] is True and data['residency_verified'] is False
        assert data['models'][0]['loaded'] is None
        response=client.post('/api/chat',headers=H,json={'model':'unknown-residency',
                            'messages':[{'role':'user','content':'test'}]})
        assert response.status_code==502 and response.json()['detail']=='tiiny_residency_unverified'


def test_running_endpoint_is_authoritative_even_if_inventory_says_ready(prepared, monkeypatch):
    direct(prepared)
    def handler(request):
        if request.url.path == '/api/v1/models/':
            return httpx.Response(200, json=[{'id':'not-running','status':'ready'}])
        if request.url.path == '/api/v1/models/running':
            return httpx.Response(200, json=[])
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get('/api/ai/catalog').json()
        assert data['residency_verified'] is True
        assert data['models'][0]['loaded'] is False


def test_kb_embedding_catalog_uses_complete_tiiny_inventory(prepared, monkeypatch):
    from test_rag import rag_ready
    prepared = rag_ready(prepared)
    prepared.write_text(prepared.read_text().replace('provider="openai_compatible"', 'provider="tiiny_direct"'))
    def handler(request):
        if request.url.path=='/api/v1/models/':
            return httpx.Response(200,json=[
                {'id':'Qwen/Qwen3-Embedding-4B','status':'downloaded','type':'Text Embedding'},
                {'id':'chat-only-model','status':'downloaded','type':'Text Generation'},
                {'id':'bge-m3','status':'downloaded','type':'Text Embedding'},
            ])
        if request.url.path=='/api/v1/models/running':
            return httpx.Response(200,json=[{'id':'chat-only-model'}])
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'chat-only-model'}]})
        return httpx.Response(404)
    mock_http(monkeypatch,handler)
    with TestClient(create_app(load_config(prepared))) as client:
        data=client.get('/api/embedding-models').json()
        assert data['catalog_complete'] is True
        assert data['models']==['stub-embed','Qwen/Qwen3-Embedding-4B','bge-m3']


def test_kb_embedding_probe_gateway_is_delegated_and_never_autoloads_directly(prepared, monkeypatch):
    from test_rag import rag_ready
    prepared = rag_ready(prepared)
    prepared.write_text(prepared.read_text().replace('provider="openai_compatible"',
                        'provider="tiiny_gateway"\nmanagement_url="http://device.local"'))
    calls = []
    def handler(request):
        calls.append((request.method, request.url.host, request.url.path))
        assert request.url.host == '127.0.0.1'
        if request.url.path == '/v1/embeddings':
            return httpx.Response(200, json={'data':[{'index':0,'embedding':[0.1,0.2,0.3]}]})
        return httpx.Response(404)
    mock_http(monkeypatch, handler)
    with TestClient(create_app(load_config(prepared))) as client:
        result=client.post('/api/embedding-models/probe',headers=H,
                           json={'model':'Qwen/Qwen3-Embedding-4B'})
        assert result.status_code == 200, result.text
        assert result.json()['supported'] is True and result.json()['dimensions'] == 3
    assert calls == [('POST','127.0.0.1','/v1/embeddings')]
