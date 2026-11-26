"""Dev7: durable ingestion jobs, unified notifications, inference prioritization."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.ai import Gateway, GatewayError, background_inference
from pharos.app import create_app
from pharos.config import load_config
from pharos.download_jobs import DownloadJobs
from pharos.knowledge.rag import RAGEngine
from pharos.kiwix_runtime import KiwixManagedService
from pharos.tiiny_core import FIFOCoordinator
from test_rag import rag_ready, stub_services

HEAD={'X-Pharos-Request':'1'}


def test_gateway_sends_only_inference_priority_and_default_is_high(prepared,monkeypatch):
    configfile=rag_ready(prepared)
    raw=configfile.read_text(encoding='utf-8').replace('provider="openai_compatible"','provider="tiiny_gateway"')
    configfile.write_text(raw,encoding='utf-8')
    cfg=load_config(configfile)
    seen=[]
    def handler(request):
        seen.append((request.url.path,request.headers.get('X-Tiiny-Priority')))
        if request.url.path.endswith('embeddings'):
            body=json.loads(request.content)
            return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.0,0.0]} for i in range(len(body['input']))]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]})
    monkeypatch.setattr(Gateway,'_client',lambda self,timeout_seconds=None:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    gateway=Gateway(cfg.ai)
    async def run():
        await gateway.complete('chat',[{'role':'user','content':'ciao'}])
        with background_inference():
            await gateway.embeddings('stub-embed',['one','two'])
        await gateway.embeddings('stub-embed',['query'])
    asyncio.run(run())
    assert seen==[('/v1/chat/completions','10'),('/v1/embeddings','90'),('/v1/embeddings','10')]


def test_generic_openai_does_not_get_tiiny_header(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));seen=[]
    def handler(request):
        seen.append(request.headers.get('X-Tiiny-Priority'))
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]})
    monkeypatch.setattr(Gateway,'_client',lambda self,timeout_seconds=None:
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with background_inference():
        assert asyncio.run(Gateway(cfg.ai).complete('chat',[{'role':'user','content':'ciao'}]))=='OK'
    assert seen==[None]


def test_tiiny_direct_queue_allows_chat_to_overtake_waiting_embedding():
    async def scenario():
        fifo=FIFOCoordinator(limit=4,wait_seconds=4)
        current=asyncio.Event();release=asyncio.Event();done=[]
        async def busy():current.set();await release.wait();done.append('busy')
        async def work(label):done.append(label)
        first=asyncio.create_task(fifo.run(busy, priority=90));await current.wait()
        background=asyncio.create_task(fifo.run(lambda:work('embedding'),priority=90));await asyncio.sleep(0)
        interactive=asyncio.create_task(fifo.run(lambda:work('chat'),priority=10));await asyncio.sleep(0)
        assert fifo.status()['waiting']==2
        release.set();await asyncio.gather(first,background,interactive)
        assert done==['busy','chat','embedding']
    asyncio.run(scenario())


def test_background_kb_upload_202_then_notification_and_read(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));stub_services(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        kb=client.post('/api/kb',headers=HEAD,json={'name':'Progetti'}).json()
        payload={'file':('memo.md',b'Memo progetto operativo ' * 120,'text/markdown')}
        result=client.post(f"/api/kb/{kb['id']}/documents",headers=HEAD,files=payload)
        assert result.status_code==202,result.text
        jobid=result.json()['id']
        assert len(jobid)==32
        assert client.get('/api/jobs').json()['items'][0]['kind']=='ingest-document'
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            jobs=client.get('/api/jobs').json()['items']
            job=next(j for j in jobs if j['id']==jobid)
            if job['status'] in ('completed','failed'):break
            time.sleep(.05)
        assert job['status']=='completed',job
        assert job['progress']==100 and job['read_at']==0
        assert job['target_view']=='knowledge' and job['kb_id']==kb['id']
        assert len(client.get(f"/api/kb/{kb['id']}/documents").json()['documents'])==1
        assert client.post(f'/api/jobs/{jobid}/read',headers=HEAD).status_code==200
        assert next(j for j in client.get('/api/jobs').json()['items'] if j['id']==jobid)['read_at']>0
    # New process preserves terminal notification; no reindexing or deletion.
    with TestClient(create_app(cfg)) as restarted:
        alljobs=restarted.get('/api/jobs').json()['items']
        assert any(j['id']==jobid and j['status']=='completed' for j in alljobs)


def test_queued_ingest_survives_restart_and_resumes(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));stub_services(monkeypatch)
    with TestClient(create_app(cfg)) as client:
        kb=client.post('/api/kb',headers=HEAD,json={'name':'Persistenza'}).json()
    # Submit directly without worker scheduling (simulates abrupt restart after durable upload).
    from pharos.knowledge_jobs import KnowledgeJobs
    from pharos.ai import Gateway
    jobs=DownloadJobs(cfg,KiwixManagedService(cfg))
    engine=RAGEngine(cfg,Gateway(cfg.ai))
    ingester=KnowledgeJobs(cfg,engine,jobs)
    jobid=jobs.create('ingest-document','persist.md',{'kb_id':kb['id'],'filename':'persist.md',
        'sha256':__import__('hashlib').sha256(b'doc content').hexdigest()})
    ingester.spool.mkdir(parents=True,exist_ok=True)
    ingester._path(jobid).write_bytes(b'doc content')
    with TestClient(create_app(cfg)) as client:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            item=next(x for x in client.get('/api/jobs').json()['items'] if x['id']==jobid)
            if item['status'] in ('completed','failed','interrupted'):break
            time.sleep(.05)
        assert item['status']=='completed',item
    assert not ingester._path(jobid).exists()


def test_nginx_kiwix_and_notification_panel_templates_still_present():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    index=(root/'src/pharos/web/index.html').read_text(encoding='utf-8')
    script=(root/'src/pharos/web/jobs.js').read_text(encoding='utf-8')
    assert 'id="notification-indicator"' in index
    assert 'id="chat-background-hint"' in index
    assert "showView('knowledge')" in script
    assert "showView('kiwix')" in script
    assert 'has-unread' in script
