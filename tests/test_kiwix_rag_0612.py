"""Federated on-demand ZIM→Knowledge RAG without copying or indexing whole archives."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.ai import Gateway
from pharos.config import load_config
from pharos.knowledge.rag import RAGEngine, RAGError
from pharos.kiwix.rag_source import (KiwixRAGSource, parse_search_results,trimmed_query)
from test_rag import rag_ready

FILE='wikipedia_it_medicine_nopic_2026-10.zim'
HEAD={'X-Pharos-Request':'1'}


def zim_path(cfg):
    root=cfg.storage.data_dir/'kiwix'/'ZIM';root.mkdir(parents=True,exist_ok=True)
    target=root/FILE;target.write_bytes(b'test zim metadata only')
    return target


def rss():
    return ('''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>results</title><item><title>Scoperta della penicillina</title>
<link>/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Penicillina</link>
<description>La &lt;b&gt;penicillina&lt;/b&gt; è un antibiotico scoperto da Fleming.</description>
</item></channel></rss>''').encode()


def test_search_parser_accepts_official_rss_and_drops_cross_book_links():
    records=parse_search_results(rss(),FILE)
    assert len(records)==1 and records[0]['title']=='Scoperta della penicillina'
    assert records[0]['url']=='/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Penicillina'
    assert '<b>' not in records[0]['snippet']
    bad=rss().replace(b'wikipedia_it_medicine_nopic_2026-10/A/Penicillina',b'other_book/A/Penicillina')
    assert parse_search_results(bad,FILE)==[]
    for dangerous in (b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>',
                      b'<rss><channel><item><link>https://bad.example/evil</link></item></channel></rss>'):
        assert parse_search_results(dangerous,FILE)==[]
    assert trimmed_query('quali sono i pro del progetto Diario di campo vocale?')=='pro progetto Diario campo vocale'


def test_link_only_local_zim_without_duplicate_storage(prepared):
    cfg=load_config(rag_ready(prepared))
    engine=RAGEngine(cfg,Gateway(cfg.ai));kb=engine.create_kb('Reference')
    path=zim_path(cfg);before=path.read_bytes()
    result=engine.link_zim(kb['id'],FILE)
    assert result['filename']==FILE
    assert engine.link_zim(kb['id'],FILE)==result  # idempotent
    assert engine.zim_sources(kb['id'])==[{'filename':FILE,'available':True}]
    assert engine.list_kbs()[0]['zim_sources_count']==1
    assert engine.list_kbs()[0]['total_documents']==0
    assert path.read_bytes()==before
    with pytest.raises(RAGError,match='zim_filename_invalid'):
        engine.link_zim(kb['id'],'../other.zim')
    assert engine.unlink_zim(kb['id'],FILE)['unlinked']==FILE
    assert engine.zim_sources(kb['id'])==[]
    assert path.exists(), 'Unlink must never delete a Kiwix ZIM file'


def test_kiwix_kb_links_summary_reflects_add_and_remove(prepared):
    cfg=load_config(rag_ready(prepared))
    with TestClient(create_app(cfg)) as client:
        first=client.post('/api/kb',headers=HEAD,json={'name':'First KB'}).json()
        second=client.post('/api/kb',headers=HEAD,json={'name':'Second KB'}).json()
        filename=FILE
        zim_path(cfg)
        endpoint='/api/kiwix/kb-links'
        assert client.get(endpoint).json()=={'items':[]}
        for kb in (first,second):
            linked=client.post(f"/api/kb/{kb['id']}/zim-sources",
                               headers=HEAD,json={'filename':filename})
            assert linked.status_code==201
        links=client.get(endpoint).json()['items']
        assert len(links)==2
        assert {item['kb_name'] for item in links}=={'First KB','Second KB'}
        assert all(item['filename']==filename for item in links)
        assert {item['kb_id'] for item in links}=={first['id'],second['id']}
        client.delete(f"/api/kb/{first['id']}/zim-sources/{filename}",headers=HEAD)
        remaining=client.get(endpoint).json()['items']
        assert len(remaining)==1 and remaining[0]['kb_id']==second['id']


def test_linked_archive_missing_is_skipped_and_unlinkable(prepared):
    cfg=load_config(rag_ready(prepared));e=RAGEngine(cfg,Gateway(cfg.ai))
    kb=e.create_kb('ZIM KB');path=zim_path(cfg)
    e.link_zim(kb['id'],FILE)
    path.unlink()
    assert e.zim_sources(kb['id'])==[{'filename':FILE,'available':False}]
    assert asyncio.run(e.search([kb['id']],'penicillina'))==[]
    assert e.unlink_zim(kb['id'],FILE)['unlinked']==FILE


def test_one_zim_rag_question_retrieves_article_and_cites_original(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));e=RAGEngine(cfg,Gateway(cfg.ai))
    kb=e.create_kb('Medicina');zim_path(cfg);e.link_zim(kb['id'],FILE)
    calls=[]
    real=httpx.AsyncClient
    def handler(request):
        calls.append((request.url.path,dict(request.url.params)))
        if request.url.path=='/kiwix/search':return httpx.Response(200,content=rss())
        if request.url.path.endswith('/A/Penicillina'):
            return httpx.Response(200,text='''<html><body><main><p>
            Alexander Fleming scoprì la penicillina, il primo grande antibiotico naturale.
            </p><script>ignore content</script></main></body></html>''')
        raise AssertionError('Unexpected endpoint '+str(request.url))
    monkeypatch.setattr('pharos.kiwix.rag_source.httpx.AsyncClient',lambda **kw:
                        real(transport=httpx.MockTransport(handler),trust_env=False))
    async def complete(self,model,messages):
        assert 'Alexander Fleming' in messages[-1]['content']
        assert 'Kiwix ZIM' in messages[-1]['content']
        assert 'ignore content' not in messages[-1]['content']
        return 'Fleming scoprì la penicillina [1].'
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(e.answer([kb['id']],'Quando è stata scoperta la penicillina?','chat'))
    assert result['sources'][0]['source_type']=='zim'
    assert result['sources'][0]['url'].endswith('/A/Penicillina')
    assert result['sources'][0]['document_id']=='zim:'+FILE
    assert 'Fleming' in result['answer']
    assert calls[0][0]=='/kiwix/search'
    assert calls[0][1]['books.name']=='wikipedia_it_medicine_nopic_2026-10'
    assert calls[0][1]['format']=='xml'


def test_kb_zim_api_is_additive_and_mutations_guarded(prepared):
    cfg=load_config(rag_ready(prepared));zim_path(cfg)
    with TestClient(create_app(cfg)) as client:
        kb=client.post('/api/kb',headers=HEAD,json={'name':'ZIM KB'}).json()
        path=f"/api/kb/{kb['id']}/zim-sources"
        assert client.get(path).json()=={'items':[]}
        assert client.post(path,json={'filename':FILE}).status_code==403
        linked=client.post(path,headers=HEAD,json={'filename':FILE})
        assert linked.status_code==201,linked.text
        assert client.get(path).json()['items'][0]['available']
        listed=client.get('/api/kb').json()['items']
        assert listed[0]['zim_sources_count']==1
        assert client.delete(path+'/'+FILE,headers=HEAD).json()['unlinked']==FILE
        assert client.get(path).json()=={'items':[]}
        assert (cfg.storage.data_dir/'kiwix'/'ZIM'/FILE).exists()


def test_zim_search_does_not_fetch_or_click_off_host_links(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared))
    data=rss().replace(b'/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Penicillina',
                       b'https://evil.example/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Penicillina')
    observed=[];original=httpx.AsyncClient
    def handler(req):
        observed.append(req.url.path)
        return httpx.Response(200,content=data)
    monkeypatch.setattr('pharos.kiwix.rag_source.httpx.AsyncClient',lambda **kw:
                        original(transport=httpx.MockTransport(handler)))
    result=asyncio.run(KiwixRAGSource(cfg).search_one({'id':'kb','name':'Test'},FILE,'penicillina'))
    assert result==[]
    assert observed==['/kiwix/search']


def test_distinct_articles_same_zim_are_kept_as_separate_citations(prepared,monkeypatch):
    cfg=load_config(rag_ready(prepared));e=RAGEngine(cfg,Gateway(cfg.ai))
    kb=e.create_kb('Medicina');zim_path(cfg);e.link_zim(kb['id'],FILE)
    one={'kb_id':kb['id'],'kb_name':kb['name'],'document_id':'zim:'+FILE,
         'filename':FILE,'page':None,'chunk_index':None,'score':None,
         'source_type':'zim','title':'Penicillina','url':'/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Penicillina',
         'text':'La penicillina è un antibiotico.'}
    two={**one,'title':'Antibiotici','url':'/kiwix/content/wikipedia_it_medicine_nopic_2026-10/A/Antibiotici',
         'text':'Gli antibiotici agiscono sui batteri.'}
    async def search(self,books,question,limit):return [one,two]
    monkeypatch.setattr(KiwixRAGSource,'search',search)
    async def complete(self,model,messages):
        assert 'Penicillina' in messages[-1]['content']
        assert 'Antibiotici' in messages[-1]['content']
        return 'La penicillina è un antibiotico [1]. Gli antibiotici agiscono sui batteri [2].'
    monkeypatch.setattr(Gateway,'complete',complete)
    result=asyncio.run(e.answer([kb['id']],'Parlami della penicillina e degli antibiotici','chat'))
    assert len(result['sources'])==2
    assert {x['title'] for x in result['sources']}=={'Penicillina','Antibiotici'}
