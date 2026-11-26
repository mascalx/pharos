"""Regression: kiwix-serve content names, safe ZIM deletion, edition metadata."""
from __future__ import annotations
import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.kiwix.library import KiwixLibrary, parse_books
from pharos.kiwix_runtime import KiwixRuntimeError, KiwixManagedService

HEAD={'X-Pharos-Request':'1'}


def _local_feed():
    return b'''<feed xmlns="http://www.w3.org/2005/Atom">
       <entry><id>urn:uuid:123</id><title>Wikipedia medicina</title>
         <name>wikipedia_it_medicine</name><summary>Enciclopedia medica</summary>
         <link type="text/html" href="/content/wikipedia_it_medicine_nopic_2026-10"/>
       </entry></feed>'''


def test_content_link_overrides_descriptive_opds_name():
    book=parse_books(_local_feed())[0]
    assert book['title']=='Wikipedia medicina'
    assert book['name']=='wikipedia_it_medicine_nopic_2026-10'


def test_managed_local_zim_names_override_bad_catalog_name(monkeypatch):
    def transport(_request):
        return httpx.Response(200,text='''<feed><entry><title>Wikipedia medicina</title>
            <name>wikipedia_it_medicine</name></entry></feed>''')
    original=httpx.AsyncClient
    monkeypatch.setattr('pharos.kiwix.library.httpx.AsyncClient',
                        lambda **kwargs:original(transport=httpx.MockTransport(transport)))
    lib=KiwixLibrary('http://127.0.0.1:8081','/kiwix')
    result=asyncio.run(lib.books(installed_filenames=[
        'wikipedia_it_medicine_nopic_2026-10.zim']))
    assert result['items'][0]['title']=='Wikipedia medicina'
    assert result['items'][0]['filename']=='wikipedia_it_medicine_nopic_2026-10.zim'
    assert result['items'][0]['name']=='wikipedia_it_medicine_nopic_2026-10'
    assert result['items'][0]['url']=='/kiwix/content/wikipedia_it_medicine_nopic_2026-10'


def test_two_editions_same_title_have_independent_links(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr('pharos.kiwix.library.httpx.AsyncClient',lambda **kwargs:
                        original(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=_local_feed()))))
    lib=KiwixLibrary('http://127.0.0.1:8081','/kiwix')
    result=asyncio.run(lib.books(installed_filenames=[
        'wikipedia_it_medicine_nopic_2026-10.zim',
        'wikipedia_it_medicine_maxi_2026-10.zim']))
    assert len(result['items'])==2
    assert {r['name'] for r in result['items']}=={
        'wikipedia_it_medicine_nopic_2026-10','wikipedia_it_medicine_maxi_2026-10'}
    assert all(r['url'].endswith(r['name']) for r in result['items'])
    assert all(r['filename']==r['name']+'.zim' for r in result['items'])


def test_local_manifest_pagination_filters_without_edition_collision(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr('pharos.kiwix.library.httpx.AsyncClient',lambda **kwargs:
                        original(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=_local_feed()))))
    lib=KiwixLibrary('http://127.0.0.1:8081','/kiwix')
    names=['wikipedia_it_medicine_nopic_2026-10.zim',
           'wikipedia_it_medicine_maxi_2026-10.zim']
    first=asyncio.run(lib.books(start=0,count=1,installed_filenames=names))
    second=asyncio.run(lib.books(start=1,count=1,installed_filenames=names))
    assert first['more'] is True
    assert second['more'] is False
    assert first['items'][0]['filename']!=second['items'][0]['filename']


def test_delete_exact_zim_leaves_other_zims_and_settings(prepared,monkeypatch):
    cfg=load_config(prepared);service=KiwixManagedService(cfg)
    service.prepare_data()
    target=service.zim_dir/'wikipedia_it_medicine_nopic_2026-10.zim'
    other=service.zim_dir/'wikipedia_en_all_maxi_2026-10.zim'
    target.write_bytes(b'original archive');other.write_bytes(b'other archive')
    class DummyProcess:
        def poll(self):return None
    service._process=DummyProcess()
    actions=[]
    def fake_stop():
        assert target.is_file()
        actions.append('stop');service._process=None
    def fake_restart(server_url):
        assert not target.exists() and other.exists()
        actions.append('restart');return True
    monkeypatch.setattr(service,'stop',fake_stop)
    monkeypatch.setattr(service,'binary_installed',lambda:True)
    monkeypatch.setattr(service,'ensure_running',fake_restart)
    result=service.delete_zim(target.name,'http://127.0.0.1:8081')
    assert result=={'deleted':target.name,'restart_error':''}
    assert actions==['stop','restart']
    assert other.read_bytes()==b'other archive'
    assert cfg.storage.data_dir.exists()


@pytest.mark.parametrize('filename',[
    '../secret.zim','../../secret.zim','/etc/passwd','../a.zim',
    'a.zim/another','a.zim.part','file.ZIM','a%2Fb.zim','bad\\name.zim',
    'a..b.zim', 'b.zim.sha256', '.hidden.zim'
])
def test_delete_rejects_unsafe_target_names(prepared,filename):
    service=KiwixManagedService(load_config(prepared))
    with pytest.raises(KiwixRuntimeError,match='kiwix_zim_filename_invalid'):
        service.delete_zim(filename,'http://127.0.0.1:8081')


def test_delete_rejects_symlink_and_missing(prepared,tmp_path):
    service=KiwixManagedService(load_config(prepared));service.prepare_data()
    dest=tmp_path/'outside';dest.write_bytes(b'valuable')
    symlink=service.zim_dir/'evil.zim'
    try: symlink.symlink_to(dest)
    except (NotImplementedError,OSError):pytest.skip('symlink privilege not available')
    for name in ('evil.zim','not_found.zim'):
        with pytest.raises(KiwixRuntimeError,match='kiwix_zim_not_found'):
            service.delete_zim(name,'http://127.0.0.1:8081')
    assert dest.read_bytes()==b'valuable'


def test_delete_api_requires_guard_and_not_active_download(prepared,monkeypatch):
    with TestClient(create_app(load_config(prepared))) as c:
        service=c.app.state.kiwix_service
        service.prepare_data()
        zim=service.zim_dir/'wikipedia_it_medicine_nopic_2026-10.zim'
        zim.write_bytes(b'test')
        path='/api/extensions/kiwix/zims/'+zim.name
        assert c.delete(path).status_code==403
        assert c.delete(path,headers={**HEAD,'Origin':'http://elsewhere.invalid'}).status_code==403
        assert c.delete('/extensions/kiwix/zims/'+zim.name,headers=HEAD).status_code==404
        monkeypatch.setattr(c.app.state.jobs,'has_active_zim_download',lambda:True)
        denied=c.delete(path,headers=HEAD)
        assert denied.status_code==409 and denied.json()['detail']=='kiwix_download_in_progress'
        assert zim.exists()
        monkeypatch.setattr(c.app.state.jobs,'has_active_zim_download',lambda:False)
        ok=c.delete(path,headers=HEAD)
        assert ok.status_code==200 and ok.json()['deleted']==zim.name
        assert not zim.exists()
        assert c.delete(path,headers=HEAD).status_code==404


def test_managed_books_api_uses_local_stem(prepared,monkeypatch):
    import pharos.kiwix.library as library
    original=httpx.AsyncClient
    monkeypatch.setattr(library.httpx,'AsyncClient',lambda **kwargs:
                        original(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=_local_feed()))))
    with TestClient(create_app(load_config(prepared))) as c:
        service=c.app.state.kiwix_service
        service.prepare_data()
        filename='wikipedia_it_medicine_nopic_2026-10.zim'
        (service.zim_dir/filename).write_bytes(b'test')
        monkeypatch.setattr(service,'ensure_running',lambda url:True)
        response=c.get('/api/extensions/kiwix/books')
        assert response.status_code==200
        item=response.json()['items'][0]
        assert item['filename']==filename
        assert item['url']=='/kiwix/content/wikipedia_it_medicine_nopic_2026-10'


def test_browser_shows_variants_and_deletion_controls():
    content=(Path(__file__).resolve().parents[1]/'src/pharos/web/kiwix.js').read_text(encoding='utf-8')
    assert "parts.includes('mini')" in content
    assert "parts.includes('nopic')" in content
    assert "parts.includes('maxi')" in content
    assert "method:'DELETE'" in content
    assert "window.confirm(t('kiwixDeleteConfirm')" in content


def test_deletion_refused_for_external_kiwix_server(prepared):
    service=KiwixManagedService(load_config(prepared));service.prepare_data()
    archive=service.zim_dir/'wikipedia_it_medicine_nopic_2026-10.zim'
    archive.write_bytes(b'data')
    with pytest.raises(KiwixRuntimeError,match='kiwix_managed_service_required'):
        service.delete_zim(archive.name,'http://example.org:8081')
    assert archive.read_bytes()==b'data'
