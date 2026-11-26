"""Gateway management inventory: use the real SDK endpoint, never TiinyOS."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.gateway_inventory import GatewayInventoryError, parse_gateway_inventory
from pharos.secrets import write_secrets
from test_rag import rag_ready


def test_parse_gateway_management_status_downloaded_and_running():
    raw = {
        'preload_strict': False,
        'npu': {'total': 100, 'used': 98, 'available': 2},
        'running': ['ASR/Whisper', {'id': 'Qwen/Qwen3-Embedding-0.6B'}],
        'pending': [],
        'models': [
            {'id': 'ASR/Whisper', 'name': 'Whisper', 'type':'Speech-to-Text',
             'status':'running', 'npu_usage':7, 'capabilities':['main']},
            {'id': 'Qwen/Qwen3-Embedding-0.6B', 'name':'Embedding', 'type':'Text Embedding',
             'status':'downloaded', 'npu_usage':20, 'capabilities':['embedding']},
            {'id': 'BAAI/bge-m3', 'name':'BGE', 'type':'Text Embedding',
             'status':'downloaded', 'npu_usage':16},
            {'id': 'Qwen/chat', 'name':'Chat', 'type':'Text Generation', 'status':'downloaded',
             'desc': 'Language model'},
            {'id':'incomplete-embed','type':'Text Embedding','status':'downloading'},
        ],
    }
    result = parse_gateway_inventory(raw)
    assert result['complete'] is True and result['source'] == 'tiiny_gateway_management'
    assert len(result['models']) == 4
    by_id = {m['id']:m for m in result['models']}
    assert by_id['Qwen/Qwen3-Embedding-0.6B']['loaded'] is True
    assert by_id['Qwen/Qwen3-Embedding-0.6B']['downloaded'] is True
    assert by_id['BAAI/bge-m3']['loaded'] is False
    assert by_id['ASR/Whisper']['loaded'] is True
    assert by_id['Qwen/chat']['downloaded'] is True
    assert 'incomplete-embed' not in by_id
    assert 'npu' not in result  # avoid returning unrelated diagnostic data


def test_embedding_capability_is_recognized_even_without_name_hint():
    from pharos.embedding_catalog import embedding_candidates
    data = parse_gateway_inventory({'models': [
        {'id':'vendor/custom-vector','type':'General Purpose',
         'capabilities':['embedding'], 'status':'downloaded'},
        {'id':'vendor/chat','type':'Text Generation',
         'capabilities':['main'], 'status':'downloaded'},
    ],'running':[]})
    result=embedding_candidates(data)
    assert result['models']==['vendor/custom-vector']
    assert result['items'][0]['candidate_basis']=='capability'
    assert result['items'][0]['loaded'] is False


def test_gateway_catalog_invalid_schema_not_treated_as_complete():
    for value in ({'data':[{'id':'model'}]}, {'models':{}}, [],
                  {'models':[{'type':'Embedding'}]}, {'models':[42]}):
        with pytest.raises(GatewayInventoryError):
            parse_gateway_inventory(value)


def test_gateway_kb_lists_downloaded_and_npu_status_without_probe(prepared: Path, monkeypatch):
    path = rag_ready(prepared)
    path.write_text(path.read_text().replace('provider="openai_compatible"',
        'provider="tiiny_gateway"\nmanagement_url="http://device-must-not-be-contacted.local"').replace(
        'embedding_model="stub-embed"', 'embedding_model="Qwen/Qwen3-Embedding-4b"'))
    # The API key is sent to the external Gateway; the historic separate native
    # management key is deliberately never sent.
    cfg = load_config(path)
    write_secrets(cfg.security.secrets_file, {'ai':{
        'api_key':'gateway-key', 'management_api_key':'legacy-native-secret'}})
    requests = []
    raw = {'npu':{'total':100,'used':23,'available':77},
        'running':[{'id':'Qwen/Qwen3-Embedding-4b'}], 'pending':[],
        'models':[
            {'id':'Qwen/Qwen3-Embedding-4b','status':'running','type':'Text Embedding'},
            {'id':'Qwen/Qwen3-Embedding-0.6B','status':'downloaded','type':'Text Embedding'},
            {'id':'BAAI/bge-m3','status':'downloaded','type':'Text Embedding'},
            {'id':'Qwen/chat','status':'downloaded','type':'Text Generation'},
        ]}
    original = httpx.AsyncClient
    def handler(request):
        requests.append((request.method, request.url.host, request.url.path,
                         request.headers.get('authorization')))
        assert request.url.host=='127.0.0.1'
        assert request.url.path=='/gateway/v1/models'
        assert request.headers.get('authorization')=='Bearer gateway-key'
        return httpx.Response(200, json=raw)
    monkeypatch.setattr(httpx, 'AsyncClient',lambda *args,**kw:
        original(*args,**kw,transport=httpx.MockTransport(handler)))
    with TestClient(create_app(cfg)) as client:
        result=client.get('/api/embedding-models')
        assert result.status_code==200, result.text
        data=result.json()
        assert data['catalog_complete'] is True
        assert data['catalog_source']=='tiiny_gateway_management'
        assert data['models']==[
            'Qwen/Qwen3-Embedding-4b','Qwen/Qwen3-Embedding-0.6B','BAAI/bge-m3'
        ]
        assert data['items'][0]['configured_only'] is False
        assert data['items'][0]['loaded'] is True
        assert data['items'][1]['loaded'] is False
        assert data['items'][1]['downloaded'] is True
        assert client.get('/api/ai/catalog').json()['models'][1]['status']=='downloaded'
    assert len(requests)==1  # shared short-lived inventory cache
    assert all(method=='GET' and path=='/gateway/v1/models' for method,_,path,_ in requests)
    assert all('legacy-native-secret' not in str(r) for r in requests)


def test_gateway_ext_schema_invalid_falls_back_without_false_npu_claim(prepared: Path, monkeypatch):
    path=rag_ready(prepared)
    path.write_text(path.read_text().replace('provider="openai_compatible"','provider="tiiny_gateway"'))
    original=httpx.AsyncClient
    paths=[]
    def handler(req):
        paths.append(req.url.path)
        if req.url.path=='/gateway/v1/models':
            return httpx.Response(200,json={'unknown':[]})
        if req.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'Qwen/Qwen3-Embedding-4b'}]})
        return httpx.Response(404)
    monkeypatch.setattr(httpx,'AsyncClient',lambda *args,**kwargs:
        original(*args,**kwargs,transport=httpx.MockTransport(handler)))
    with TestClient(create_app(load_config(path))) as client:
        result=client.get('/api/embedding-models').json()
        assert result['catalog_complete'] is False
        assert result['catalog_management_error_code']=='gateway_inventory_invalid'
        assert result['catalog_reason']=='gateway_catalog_not_exposed'
        assert result['models']==['stub-embed','Qwen/Qwen3-Embedding-4b']
        assert result['items'][1]['loaded'] is None
    assert paths==['/gateway/v1/models','/v1/models']


def test_generic_provider_never_calls_gateway_management(prepared: Path, monkeypatch):
    prepared.write_text(prepared.read_text().replace('enabled=false','enabled=true'))
    original=httpx.AsyncClient
    def handler(req):
        assert req.url.path=='/v1/models'
        return httpx.Response(200,json={'data':[{'id':'some-embed'}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda *args,**kwargs:
        original(*args,**kwargs,transport=httpx.MockTransport(handler)))
    with TestClient(create_app(load_config(prepared))) as client:
        assert client.get('/api/ai/catalog').json()['models'][0]['id']=='some-embed'
