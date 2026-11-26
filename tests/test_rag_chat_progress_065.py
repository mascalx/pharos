"""Dev5 regressions: references-only answers, citation provenance, live KB progress."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.ai import Gateway, GatewayError
from pharos.app import create_app
from pharos.config import load_config
from pharos.knowledge.rag import RAGEngine, RAGError
from test_rag import rag_ready, stub_services


def evidence():
    return [
        {'kb_id':'kb', 'kb_name':'Test', 'document_id':'d1','filename':'manual.pdf',
         'page':2,'text':'La risposta vera e documentata', 'score':0.9},
        {'kb_id':'kb', 'kb_name':'Test', 'document_id':'d1','filename':'manual.pdf',
         'page':99,'text':'Una sezione su un argomento diverso', 'score':0.7},
        {'kb_id':'kb', 'kb_name':'Test', 'document_id':'d2','filename':'notes.pdf',
         'page':7,'text':'Anche questo è un argomento diverso', 'score':0.6},
    ]


def test_rag_only_returns_sources_actually_cited(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared))
    engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'): return evidence()
    async def complete(self, model, messages):
        assert 'NEVER just a source number' in messages[0]['content']
        return 'Il manuale descrive una risposta verificabile [1].'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(engine.answer(['kb'],'domanda?','chat'))
    assert [r['page'] for r in result['sources']]==[2]
    assert result['citations_missing'] is False
    assert result['answer'].startswith('Il manuale')


def test_rag_uncited_text_is_flagged_and_no_arbitrary_pages(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'): return evidence()
    async def complete(self, model, messages): return 'La risposta è nel manuale ma senza numeri.'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(engine.answer(['kb'],'domanda','chat'))
    assert not result['sources'] and result['citations_missing'] is True


def test_rag_reference_only_retried_once_with_informative_answer(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'):return evidence()
    calls=[]
    async def complete(self, model, messages):
        calls.append(messages[0]['content'])
        return '[1] [2]' if len(calls)==1 else 'Il testo spiega la procedura [1].'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(engine.answer(['kb'],'domanda','chat'))
    assert len(calls)==2 and 'previous output' in calls[1]
    assert result['answer']=='Il testo spiega la procedura [1].'
    assert [r['page'] for r in result['sources']]==[2]


def test_rag_empty_model_output_is_error_not_empty_assistant_turn(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'):return evidence()
    async def complete(self, model, messages):raise GatewayError('gateway_empty_completion')
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    with pytest.raises(RAGError,match='rag_answer_empty'):
        asyncio.run(engine.answer(['kb'],'domanda','chat'))


def test_http_chat_empty_rag_output_is_a_named_502(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared))
    async def answer(self,*args,**kwargs):raise RAGError('rag_answer_empty')
    monkeypatch.setattr(RAGEngine,'answer',answer)
    with TestClient(create_app(cfg)) as client:
        response=client.post('/api/chat',headers={'X-Pharos-Request':'1'},json={
            'model':'chat-model','messages':[{'role':'user','content':'domanda'}],
            'kb_ids':['kb-test']})
        assert response.status_code==502
        assert response.json()['detail']=='rag_answer_empty'


def test_http_rag_chat_exposes_citations_missing(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared))
    async def answer(self,*args,**kwargs):
        return {'answer':'Una spiegazione.', 'sources': [], 'citations_missing':True,
                'insufficient_evidence':False}
    monkeypatch.setattr(RAGEngine,'answer',answer)
    with TestClient(create_app(cfg)) as client:
        response=client.post('/api/chat',headers={'X-Pharos-Request':'1'},json={
            'model':'chat-model','messages':[{'role':'user','content':'domanda'}],
            'kb_ids':['kb-test']})
        assert response.status_code==200
        assert response.json()['citations_missing'] is True
        assert response.json()['sources']==[]


def test_gateway_empty_completion_is_named_failure(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));gateway=Gateway(cfg.ai)
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':'  '}}]})
    monkeypatch.setattr(Gateway,'_client',lambda self, timeout_seconds=None:
        httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='http://127.0.0.1'))
    with pytest.raises(GatewayError,match='gateway_empty_completion'):
        asyncio.run(gateway.complete('chat',[{'role':'user','content':'test'}]))


def test_ingest_progress_is_persisted_while_embedding_in_flight(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));stub_services(monkeypatch)
    engine=RAGEngine(cfg,Gateway(cfg.ai));kb=engine.create_kb('Progress')
    async def scenario():
        start=asyncio.Event();release=asyncio.Event()
        async def embed(self,model,texts):
            start.set();await release.wait()
            return [[1.0,0.0,0.0] for _ in texts]
        monkeypatch.setattr(Gateway,'embeddings',embed)
        task=asyncio.create_task(engine.ingest(kb['id'],'very-long.txt',b'frammento ' * 1100))
        await asyncio.wait_for(start.wait(),2)
        [doc]=engine.documents(kb['id'])
        assert doc['status']=='indexing'
        assert doc['progress_stage']=='embedding'
        assert doc['progress_done']==0
        assert doc['progress_total']>8
        release.set()
        result=await asyncio.wait_for(task,5)
        [final]=engine.documents(kb['id'])
        assert result['status']=='indexed'
        assert final['status']=='indexed' and final['progress_stage']=='done'
        assert final['progress_done']==final['progress_total']==result['chunks']
    asyncio.run(scenario())


def test_additive_schema_migration_and_interrupted_job_recovery(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared))
    db_path=cfg.storage.database_dir/'rag.sqlite3'
    db_path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript('''
            CREATE TABLE documents(
                id TEXT PRIMARY KEY,kb_id TEXT NOT NULL, filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,status TEXT NOT NULL,chunks INTEGER DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(kb_id,sha256));
            INSERT INTO documents(id,kb_id,filename,sha256,status)
                VALUES ('old-id','old-kb','legacy.txt','abcd','indexing');
        ''')
    engine=RAGEngine(cfg,Gateway(cfg.ai))
    with engine._db() as db:
        row=db.execute('SELECT * FROM documents WHERE id=?',('old-id',)).fetchone()
    assert row['filename']=='legacy.txt'
    assert row['status']=='failed' and row['error']=='ingest_interrupted'
    assert row['progress_stage']=='failed' and row['progress_done']==0


def test_rag_only_references_twice_is_named_failure(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'):return evidence()
    calls=[]
    async def complete(self,model,messages):
        calls.append(1)
        return '[1], [3]'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    with pytest.raises(RAGError,match='rag_answer_empty'):
        asyncio.run(engine.answer(['kb'],'domanda','chat'))
    assert len(calls)==2


def test_blank_string_answer_retries_once(prepared, monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'):return evidence()
    calls=[]
    async def complete(self,model,messages):
        calls.append(1)
        return '' if len(calls)==1 else 'La fonte [2] contiene la risposta.'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(engine.answer(['kb'],'domanda','chat'))
    assert len(calls)==2
    assert [r['page'] for r in result['sources']]==[99]


def test_source_label_only_is_not_an_informative_answer(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));engine=RAGEngine(cfg,Gateway(cfg.ai))
    async def search(self, ids, question, limit=None, *, retrieval_mode='vector'):return evidence()
    calls=[]
    async def complete(self,model,messages):
        calls.append(1)
        return 'Fonte: [1]' if len(calls)==1 else 'Il documento descrive una risposta [1].'
    monkeypatch.setattr(RAGEngine,'search',search)
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(engine.answer(['kb'],'domanda','chat'))
    assert len(calls)==2 and result['sources'][0]['page']==2


def test_cancelled_upload_releases_document_for_retry(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));stub_services(monkeypatch)
    engine=RAGEngine(cfg,Gateway(cfg.ai));kb=engine.create_kb('Cancel')
    async def scenario():
        started=asyncio.Event()
        async def embed(self,model,texts):
            started.set()
            await asyncio.Future()
        monkeypatch.setattr(Gateway,'embeddings',embed)
        task=asyncio.create_task(engine.ingest(kb['id'],'cancel.txt',b'prova '*1000))
        await asyncio.wait_for(started.wait(),2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [doc]=engine.documents(kb['id'])
        assert doc['status']=='failed' and doc['error']=='ingest_cancelled'
        assert doc['progress_stage']=='failed'
    asyncio.run(scenario())
