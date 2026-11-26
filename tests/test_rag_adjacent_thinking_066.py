"""Dev6: paragraph continuation across chunks and Qwen3 thinking opt-in.

Synthetic PDFs/source passages only: do not distribute users' personal uploads.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4, uuid5, NAMESPACE_URL

from fastapi.testclient import TestClient
import httpx
import pytest

from pharos.ai import Gateway, GatewayError, supports_thinking_switch
from pharos.app import create_app
from pharos.config import load_config
from pharos.knowledge.rag import RAGEngine, chunk_pages
from test_rag import rag_ready


HEAD = {'X-Pharos-Request': '1'}


def test_exact_pro_boundary_is_preserved_in_later_chunk():
    # The PDF supplied by the user has this exact problem: the first chunk
    # stops after 'Pro:', the second contains the listed advantages.
    text = ('A'*1086) + ' Pro: Arricchisce il catalogo fotografico e costa quasi nulla.'
    chunks = chunk_pages([(1, text)], 1100, 150)
    assert len(chunks) == 2
    assert chunks[0][1].endswith('Pro:')
    assert 'Arricchisce il catalogo' in chunks[1][1]


def test_neighbour_recovers_missing_pro_and_cites_its_real_page(prepared, monkeypatch):
    cfg = load_config(rag_ready(prepared))
    engine = RAGEngine(cfg, Gateway(cfg.ai))
    kb = engine.create_kb('Diario')
    doc_id = str(uuid4())
    hit = {'kb_id': kb['id'], 'kb_name':'Diario', 'document_id':doc_id,
           'filename':'progetti.pdf','page':1,'chunk_index':0,
           'text':'Diario di campo vocale. Modelli: STT, G. Pro:', 'score':0.94}
    requested = []

    async def fake_search(self,ids,question,limit=None,*,retrieval_mode='vector'):
        return [hit]

    async def fake_qdrant(self,method,endpoint,data=None,missing_ok=False):
        assert method == 'POST' and endpoint.endswith('/points')
        assert data['with_vector'] is False and data['with_payload'] is True
        wanted_id = str(uuid5(NAMESPACE_URL, f'{doc_id}:1'))
        requested.extend(data['ids'])
        return {'result': [{'id': wanted_id, 'payload': {
            'document_id':doc_id,'kb_id':kb['id'],'filename':'progetti.pdf',
            'page':2,'chunk_index':1,
            'text':'Pro: arricchisce il catalogo foto; costo marginale quasi nullo.'
        }}]}

    async def fake_complete(self, model, messages):
        passage = messages[-1]['content']
        assert 'arricchisce il catalogo foto' in passage
        assert 'costo marginale quasi nullo' in passage
        assert 'page 2' in passage
        return 'I pro sono il contesto e il costo quasi nullo [2].'

    monkeypatch.setattr(RAGEngine,'search',fake_search)
    monkeypatch.setattr(RAGEngine,'_qdrant',fake_qdrant)
    monkeypatch.setattr(Gateway,'complete',fake_complete)
    result = asyncio.run(engine.answer([kb['id']], 'quali sono i pro?', 'chat'))
    assert [x['page'] for x in result['sources']] == [2]
    assert [x['reference'] for x in result['sources']] == [2]
    assert str(uuid5(NAMESPACE_URL, f'{doc_id}:1')) in requested


def test_neighbour_must_belong_to_same_document(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    kb=engine.create_kb('Pro')
    doc_id=str(uuid4())
    hits=[{'kb_id':kb['id'],'kb_name':'Pro','document_id':doc_id,
           'filename':'a.pdf','page':1,'chunk_index':0,'text':'Pro:', 'score':0.9}]
    async def fake_qdrant(self,method,endpoint,data=None,missing_ok=False):
        return {'result':[{'id':data['ids'][0], 'payload':{
            'document_id':str(uuid4()), 'kb_id':kb['id'], 'chunk_index':1,
            'page':1,'text':'Unrelated private document'}}]}
    monkeypatch.setattr(RAGEngine,'_qdrant',fake_qdrant)
    assert asyncio.run(engine._expand_context(hits))==hits


def test_no_neighbour_lookup_on_legacy_payload_without_chunk_index(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    hits=[{'kb_id':'dummy','document_id':'dummy','text':'Legacy source'}]
    async def fail(*args,**kwargs):
        raise AssertionError('No network lookup expected')
    monkeypatch.setattr(RAGEngine,'_qdrant',fail)
    assert asyncio.run(engine._expand_context(hits))==hits


def test_thinking_can_be_requested_for_other_chat_models_but_not_embedders(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared))
    assert supports_thinking_switch('Qwen/Qwen3.6-35B-A3B')
    assert supports_thinking_switch('qwen3.6-35b-a3b')
    assert supports_thinking_switch('Qwen3-32B')
    assert supports_thinking_switch('gpt-oss-20b')
    assert not supports_thinking_switch('qwen3-embedding-4b')
    with pytest.raises(GatewayError,match='thinking_control_unsupported_model'):
        asyncio.run(Gateway(cfg.ai).complete('qwen3-embedding-4b',[{'role':'user','content':'ciao'}],thinking=False))


def test_qwen_thinking_flag_passes_to_openai_request_only_when_set(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared))
    captured=[]
    def handler(request):
        payload=__import__('json').loads(request.content)
        captured.append(payload)
        return httpx.Response(200,json={'choices':[{'message':{'content':'Pronto'}}]})
    monkeypatch.setattr(Gateway,'_client',lambda self, timeout_seconds=None:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    gw=Gateway(cfg.ai)
    async def calls():
        for flag in (None,False,True):
            assert await gw.complete('Qwen/Qwen3.6-35B-A3B',[{'role':'user','content':'ciao'}],thinking=flag)=='Pronto'
    asyncio.run(calls())
    assert 'chat_template_kwargs' not in captured[0]
    assert captured[1]['chat_template_kwargs']=={'enable_thinking':False}
    assert captured[2]['chat_template_kwargs']=={'enable_thinking':True}


def test_provider_refusal_is_reported_not_silently_ignored(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared))
    def handler(request):return httpx.Response(400,json={'detail':'unsupported extra args'})
    monkeypatch.setattr(Gateway,'_client',lambda self, timeout_seconds=None:
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(GatewayError,match='thinking_control_upstream_rejected'):
        asyncio.run(Gateway(cfg.ai).complete('Qwen3.6-35B-A3B', [{'role':'user','content':'ciao'}],thinking=False))


def test_chat_api_thinking_is_forwarded_and_old_clients_are_unchanged(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));observed=[]
    async def complete(self,model,messages,thinking=None):
        observed.append(thinking)
        return 'OK'
    monkeypatch.setattr(Gateway,'complete',complete)
    with TestClient(create_app(cfg)) as client:
        body={'model':'Qwen3.6-35B-A3B','messages':[{'role':'user','content':'ciao'}]}
        for setting in ({'thinking':False},{'thinking':True},{}):
            r=client.post('/api/chat',headers=HEAD,json={**body,**setting})
            assert r.status_code==200,r.text
        invalid=client.post('/api/chat',headers=HEAD,json={**body,'model':'qwen3-embedding-4b','thinking':False})
        assert invalid.status_code==422
    assert observed==[False,True,None]
