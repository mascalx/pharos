"""Kiwix official catalog, without a live upstream/server or large ZIM files."""
from __future__ import annotations

import asyncio
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.kiwix.catalog import (
    CATALOG_URL, OnlineKiwixCatalog, _official_zim_url, parse_online_catalog,
)
from pharos.kiwix.library import KiwixError
from pharos.download_jobs import validate_zim_url


def _feed(*, first='https://download.kiwix.org/zim/wikipedia/wikipedia_it_all_2026-11.zim.meta4',
          include_extra=False, total=1):
    # Mimics OPDS v2 XML published by the Kiwix catalog; URNs and Atom namespaces.
    xmlns='xmlns="http://www.w3.org/2005/Atom" xmlns:os="http://a9.com/-/spec/opensearch/1.1/"'
    second = '''<entry><title>English Medicine</title><summary>Medical reference</summary>
        <language>eng</language><category>other</category>
        <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
              href="https://lb.download.kiwix.org/zim/other/medical_en_all_2026-10.zim" length="1024" />
        </entry>''' if include_extra else ''
    text=f'''<?xml version="1.0" encoding="UTF-8"?>
    <feed {xmlns}><os:totalResults>{total}</os:totalResults>
      <entry><id>urn:uuid:11111111-1111-1111-1111-111111111111</id>
        <title>Wikipedia in italiano</title><summary>Enciclopedia libera</summary>
        <language>ita</language><category>wikipedia</category><updated>2026-11-01T00:00:00Z</updated>
        <link type="text/html" href="/content/wikipedia_it_all_2026-11" />
        <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
              href="{first}" length="8589934592" />
      </entry>
      {second}
    </feed>'''
    return text.encode('utf-8')


def test_meta4_becomes_official_direct_zim_link():
    result=parse_online_catalog(_feed())
    assert result['total']==1
    item=result['items'][0]
    assert item['title']=='Wikipedia in italiano'
    assert item['category']=='wikipedia'
    assert item['language']=='ita'
    assert item['filename']=='wikipedia_it_all_2026-11.zim'
    assert item['url']=='https://download.kiwix.org/zim/wikipedia/wikipedia_it_all_2026-11.zim'
    assert item['size']==8589934592
    assert validate_zim_url(item['url'])==item['filename']


def test_direct_zim_link_from_new_kiwix_load_balancer_is_canonicalised():
    result=parse_online_catalog(_feed(include_extra=True,total=2))
    assert len(result['items'])==2
    assert result['items'][1]['url']=='https://download.kiwix.org/zim/other/medical_en_all_2026-10.zim'


@pytest.mark.parametrize('bad', [
    'http://download.kiwix.org/zim/a.zim',
    'https://download.kiwix.org.evil.test/zim/a.zim',
    'https://download.kiwix.org:9999/zim/a.zim',
    'https://download.kiwix.org/zim/../a.zim.meta4',
    'https://download.kiwix.org/zim/a.zim.meta4?test=1',
    'https://download.kiwix.org/zim/a.zim.torrent',
    'https://127.0.0.1/zim/a.zim',
    'file:///etc/passwd',
    'https://download.kiwix.org/zim/a%2fb.zim',
    'https://download.kiwix.org/zim/a.zim.sha256',
])
def test_catalog_rejects_untrusted_download_links(bad):
    assert _official_zim_url(bad)==''
    assert parse_online_catalog(_feed(first=bad))['items']==[]


def test_no_doctype_entities_or_unbounded_xml():
    for raw in [b'<!DOCTYPE feed [ <!ENTITY e "x"> ]><feed/>',b'<body/>',b'<feed>',b' '*(2*1024*1024+1)]:
        with pytest.raises(KiwixError,match='kiwix_online_catalog_invalid'):
            parse_online_catalog(raw)


class FakeResponse:
    def __init__(self, data: bytes):
        self.data=data
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    def raise_for_status(self): pass
    async def aiter_bytes(self):
        yield self.data[:40]
        yield self.data[40:]


class FakeClient:
    def __init__(self, data: bytes, calls: list):
        self.data=data
        self.calls=calls
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    def stream(self, verb, url, *, params, headers):
        self.calls.append((verb,url,params,headers))
        return FakeResponse(self.data)


def test_online_filter_pagination_and_bounded_cache(tmp_path,monkeypatch):
    from pharos.kiwix import catalog
    calls=[]
    payload=_feed(include_extra=True,total=50)
    monkeypatch.setattr(catalog.httpx,'AsyncClient',lambda **kwargs:FakeClient(payload,calls))
    browser=OnlineKiwixCatalog(tmp_path/'kiwix-cache')
    first=asyncio.run(browser.browse(start=24,count=24,q='medicina',lang='ita',category='wikipedia'))
    assert len(first['items'])==2
    assert first['more'] is True
    assert first['cached'] is False
    assert calls[0]==('GET',CATALOG_URL,{'start':24,'count':24,'q':'medicina','lang':'ita','category':'wikipedia'},
                       {'Accept':'application/atom+xml'})
    second=asyncio.run(browser.browse(start=24,count=24,q='medicina',lang='ita',category='wikipedia'))
    assert second==first
    assert len(calls)==1
    assert len(list((tmp_path/'kiwix-cache').glob('*.json')))==1


def test_offline_fallback_returns_cached_results(tmp_path,monkeypatch):
    from pharos.kiwix import catalog
    import httpx
    browser=OnlineKiwixCatalog(tmp_path/'kiwix-cache')
    monkeypatch.setattr(catalog.httpx,'AsyncClient',lambda **kwargs:FakeClient(_feed(),[]))
    first=asyncio.run(browser.browse())
    assert first['cached'] is False
    # A dead network plus stale cache must not erase the previously loaded catalog.
    monkeypatch.setattr(catalog,'CACHE_MAX_AGE',-1)
    class OfflineClient:
        async def __aenter__(self):raise httpx.ConnectError('offline')
        async def __aexit__(self,*_):return False
    monkeypatch.setattr(catalog.httpx,'AsyncClient',lambda **kwargs:OfflineClient())
    stale=asyncio.run(browser.browse())
    assert stale['cached'] is True
    assert stale['items']==first['items']
    with pytest.raises(KiwixError, match='kiwix_online_catalog_unavailable'):
        asyncio.run(browser.browse(q='uncached'))


def test_catalog_independent_of_local_server_and_readonly(prepared,monkeypatch):
    from pharos.kiwix import catalog
    monkeypatch.setattr(catalog.httpx,'AsyncClient',lambda **kwargs:FakeClient(_feed(),[]))
    with TestClient(create_app(load_config(prepared))) as client:
        # The web api() helper uses /api/; both old and new routes must work.
        for prefix in ('/api/extensions/kiwix', '/extensions/kiwix'):
            status=client.get(prefix+'/status')
            assert status.status_code == 200
            assert status.json()['installed'] is False
            response=client.get(prefix+'/catalog',params={'q':'wikipedia','lang':'ita'})
            assert response.status_code==200
            item=response.json()['items'][0]
            assert item['installed'] is False
            assert item['url'].startswith('https://download.kiwix.org/zim/')
            assert client.get(prefix+'/catalog',params={'lang':'../../localhost'}).status_code==422
            assert client.get(prefix+'/catalog',params={'category':'%evil'}).status_code==422
        # The download job remains a guarded, separate mutation.
        assert client.post('/api/jobs/kiwix/zim',json={'url':item['url']}).status_code==403



def test_empty_local_library_is_distinct_from_missing_api_route(prepared, monkeypatch):
    """A fresh installed server with zero ZIMs reports empty, not HTTP 404."""
    with TestClient(create_app(load_config(prepared))) as client:
        service = client.app.state.kiwix_service
        monkeypatch.setattr(service, 'binary_installed', lambda: True)
        monkeypatch.setattr(service, '_zims', lambda: [])
        monkeypatch.setattr(service, 'ensure_running', lambda url: False)
        status = client.get('/api/extensions/kiwix/status')
        assert status.status_code == 200
        assert status.json()['waiting_for_zim'] is True
        response = client.get('/api/extensions/kiwix/books')
        assert response.status_code == 200
        assert response.json() == {'items': [], 'start': 0, 'count': 30,
                                   'more': False, 'browser_url': '/kiwix'}


def test_frontend_local_and_online_are_separate_and_safe():
    root=Path(__file__).resolve().parents[1]
    html=(root/'src/pharos/web/index.html').read_text(encoding='utf-8')
    js=(root/'src/pharos/web/kiwix.js').read_text(encoding='utf-8')
    assert 'id="kiwix-catalog-list"' in html
    assert 'id="kiwix-book-list"' in html
    assert '<details class="kiwix-manual-download">' in html
    assert 'kiwixWaitingZim' in js
    assert 'extensions/kiwix/catalog?' in js
    assert 'jobs/kiwix/zim' in js
    assert '.textContent' in js
    assert '.innerHTML' not in js
