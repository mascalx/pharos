"""RAG contract tests: no live Tiiny or Qdrant needed, no external networking."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pharos.ai import Gateway, GatewayError
from pharos.app import create_app
from pharos.config import ConfigError, load_config
from pharos.knowledge.rag import RAGEngine, RAGError, chunk_pages, extract_pages


def rag_ready(prepared: Path) -> Path:
    with prepared.open('a',encoding='utf8') as out:
        out.write('''\n[rag]\nenabled=true\nembedding_model="stub-embed"\nqdrant_url="http://127.0.0.1:6333"\n''')
    text=prepared.read_text()
    text=text.replace('[ai]\nenabled=false','[ai]\nenabled=true')
    prepared.write_text(text)
    return prepared


def stub_services(monkeypatch):
    calls = {'embeddings':0,'completions':0,'upserts':0}
    collections = {}
    async def embed(self, model, texts):
        assert model == 'stub-embed'
        calls['embeddings']+=1
        return [[1.0, 0.0, 0.0] for _ in texts]
    async def complete(self, model, messages):
        calls['completions']+=1
        assert 'untrusted' in messages[1]['content'].lower()
        return 'La fonte contiene la risposta [1].'
    async def qdrant(self, method, endpoint, data=None, missing_ok=False):
        slug=endpoint.split('/')[2].split('?')[0]
        if method=='GET':
            if slug not in collections: return None
            return {'result':{'config':{'params':{'vectors':{'size':3}}}}}
        if method=='PUT' and endpoint.endswith('/points?wait=true'):
            assert slug in collections
            collections[slug].extend(data['points'])
            calls['upserts'] += 1
            return {'status':'ok'}
        if method=='PUT':
            collections[slug]=[]
            return {'status':'ok'}
        if method=='POST':
            return {'status':'ok', 'result': {'points':[
                {'score': 0.9, 'payload':p['payload']} for p in collections.get(slug, [])
            ]}}
        raise AssertionError((method,endpoint))
    monkeypatch.setattr(Gateway, 'embeddings', embed)
    monkeypatch.setattr(Gateway, 'complete', complete)
    monkeypatch.setattr(RAGEngine, '_qdrant', qdrant)
    return calls,collections


def test_text_chunking_and_source_pages():
    pieces=chunk_pages([(3, 'uno ' * 270), (7,'due due due')], 500, 50)
    assert all(page==3 for page,_ in pieces[:-1])
    assert pieces[-1] == (7,'due due due')
    assert extract_pages('NOTE.MD',b'\xef\xbb\xbfciao\n   mondo') == [(1,'ciao mondo')]
    with pytest.raises(RAGError,match='invalid_utf8'):
        extract_pages('test.txt',b'\xff')
    with pytest.raises(RAGError,match='unsupported_file_type'):
        extract_pages('test.docx',b'data')


def test_kb_create_import_search_answer(prepared,monkeypatch):
    config=load_config(rag_ready(prepared))
    calls, _=stub_services(monkeypatch)
    engine=RAGEngine(config, Gateway(config.ai))
    a=engine.create_kb('Tecnica', 'Documentazione')
    b=engine.create_kb('Note')
    assert len(engine.list_kbs()) == 2
    file=b'PHAROS can operate without internet. ' * 48
    first=asyncio.run(engine.ingest(a['id'],'manual.txt',file))
    assert first['chunks'] > 1
    assert first['status']=='indexed'
    assert len(engine.documents(a['id']))==1
    assert (config.storage.data_dir/'knowledge'/a['id']/'originals'/(first['id']+'.txt')).read_bytes() == file
    before=calls['upserts']
    same=asyncio.run(engine.ingest(a['id'],'manual.txt',file))
    assert same['id'] == first['id']
    assert calls['upserts']==before
    hits=asyncio.run(engine.search([a['id'],b['id']],'offline?'))
    assert hits and hits[0]['filename']=='manual.txt' and hits[0]['page']==1
    assert calls['completions']==0
    answer=asyncio.run(engine.answer([a['id']],'Come funziona?',model='test-chat'))
    assert '[1]' in answer['answer'] and answer['sources'][0]['reference']==1
    assert calls['completions']==1


def test_rag_fail_closed_without_volume(prepared,monkeypatch):
    config=load_config(rag_ready(prepared))
    stub_services(monkeypatch)
    engine=RAGEngine(config,Gateway(config.ai))
    kb=engine.create_kb('Knowledge')
    (config.storage.data_dir/'.pharos-volume').unlink()
    with pytest.raises(RAGError,match='data_volume_unavailable'):
        asyncio.run(engine.ingest(kb['id'],'x.md',b'Data'))
    assert engine.documents(kb['id']) == []


def test_embedding_model_change_keeps_old_index_readable(prepared,monkeypatch):
    config=load_config(rag_ready(prepared))
    stub_services(monkeypatch)
    engine=RAGEngine(config,Gateway(config.ai))
    kb=engine.create_kb('Test')
    asyncio.run(engine.ingest(kb['id'],'test.md',b'A document about offline software.'))
    cfg=prepared.read_text().replace('stub-embed','changed-model')
    prepared.write_text(cfg)
    new_cfg=load_config(prepared)
    engine2=RAGEngine(new_cfg,Gateway(new_cfg.ai))
    hits = asyncio.run(engine2.search([kb['id']], 'question'))
    assert hits and hits[0]['filename'] == 'test.md'
    with pytest.raises(RAGError, match='embedding_model_changed_reindex_required'):
        asyncio.run(engine2.ingest(kb['id'], 'more.md', b'More content'))


def test_rag_rest_stays_open_but_mutations_are_guarded(prepared,monkeypatch):
    config=load_config(rag_ready(prepared))
    stub_services(monkeypatch)
    with TestClient(create_app(config)) as client:
        assert client.get('/api/kb').status_code==200
        assert client.post('/api/rag/search',json={'kb_ids':['x'],'question':'hi'}).status_code==403
        assert client.post('/api/rag/search',headers={'X-Pharos-Request':'1'},json={'kb_ids':['x'],'question':'hi'}).status_code==422

def test_rag_unavailable_does_not_affect_core(prepared):
    cfg=load_config(prepared)
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/kb').json()=={'enabled':False,'items':[]}
        assert client.post('/api/rag/search',headers={'X-Pharos-Request':'1'},json={'kb_ids':['x'],'question':'ok'}).status_code==503
        assert client.get('/api/status').status_code==200
        assert client.get('/').status_code==200


def test_config_rejects_invalid_chunking(prepared):
    with prepared.open('a') as stream:
        stream.write('\n[rag]\nchunk_chars=500\noverlap_chars=300\n')
    with pytest.raises(ConfigError,match='overlap'):
        load_config(prepared)


def test_embed_adapter_uses_bearer_key_and_correct_index_order(prepared, monkeypatch):
    import httpx
    cfg=load_config(rag_ready(prepared))
    gateway=Gateway(cfg.ai,'protected-test-secret')
    def handler(request):
        assert request.url.path=='/v1/embeddings'
        assert request.headers['Authorization']=='Bearer protected-test-secret'
        import json
        body=json.loads(request.content)
        assert body['model']=='embed-model'
        assert body['input']==['first','second']
        return httpx.Response(200,json={'data':[
            {'index':1,'embedding':[0.0,1.0]},
            {'index':0,'embedding':[1.0,0.0]}
        ]})
    monkeypatch.setattr(Gateway, '_client', lambda self, timeout_seconds=None:
         httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='http://127.0.0.1',headers=self._headers()))
    assert asyncio.run(gateway.embeddings('embed-model',['first','second'])) == [[1.0,0.0],[0.0,1.0]]


def test_embed_adapter_rejects_malformed_response(prepared, monkeypatch):
    import httpx
    cfg=load_config(rag_ready(prepared))
    gateway=Gateway(cfg.ai)
    monkeypatch.setattr(Gateway, '_client', lambda self, timeout_seconds=None:
         httpx.AsyncClient(transport=httpx.MockTransport(
             lambda request: httpx.Response(200,content=b'{"data":[{"index":0,"embedding":[1.0,NaN]}]}',headers={'Content-Type':'application/json'}))))
    with pytest.raises(GatewayError,match='invalid_embedding_response'):
        asyncio.run(gateway.embeddings('model',['test']))


def test_blank_pdf_has_no_extractable_text(prepared,monkeypatch):
    import io
    from pypdf import PdfWriter
    out=io.BytesIO()
    writer=PdfWriter()
    writer.add_blank_page(width=200,height=300)
    writer.write(out)
    assert extract_pages('scanned.pdf',out.getvalue())==[]
    config=load_config(rag_ready(prepared))
    stub_services(monkeypatch)
    engine=RAGEngine(config,Gateway(config.ai))
    kb=engine.create_kb('Scan')
    with pytest.raises(RAGError,match='no_extractable_text'):
        asyncio.run(engine.ingest(kb['id'],'scanned.pdf',out.getvalue()))
    assert engine.documents(kb['id']) == []


def test_no_results_does_not_invoke_llm(prepared,monkeypatch):
    config=load_config(rag_ready(prepared))
    calls,_=stub_services(monkeypatch)
    engine=RAGEngine(config,Gateway(config.ai))
    kb=engine.create_kb('Empty')
    result=asyncio.run(engine.answer([kb['id']],'question','chat-model'))
    assert result['insufficient_evidence'] is True
    assert result['sources']==[] and result['answer']==''
    assert calls['completions']==0
