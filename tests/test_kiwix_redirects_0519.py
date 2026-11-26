"""Dynamic Kiwix mirror redirects: HTTPS/public DNS, never a fixed mirror list."""
from __future__ import annotations

import hashlib
import io
import zipfile
import socket
import tarfile
import urllib.request

import pytest

from pharos.config import load_config
from pharos.kiwix_runtime import (
    ARCHIVES, PUBLISHER, KiwixManagedService,
    KiwixRuntimeError, _PublisherRedirects, platform_asset,
)


def _public_dns(monkeypatch):
    def public(host, port, **kwargs):
        assert port == 443
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 443, 0, 0))]
    monkeypatch.setattr('pharos.kiwix_runtime.socket.getaddrinfo', public)


def test_publisher_redirects_accept_dynamic_public_https_mirrors(monkeypatch):
    _public_dns(monkeypatch)
    filename = ARCHIVES[('linux','x86_64')]
    start = PUBLISHER + filename
    handler = _PublisherRedirects()
    assert handler.max_redirections <= 8
    first = 'https://mirror-new-2027.example.org/a/b/' + filename
    second = 'https://cdn-any-provider.example.net/kiwix/dl/' + filename + '?download=1'
    redirect1 = handler.redirect_request(urllib.request.Request(start), None, 302, 'Found', {}, first)
    assert redirect1.full_url == first
    redirect2 = handler.redirect_request(redirect1, None, 302, 'Found', {}, second)
    assert redirect2.full_url == second


@pytest.mark.parametrize('url', [
    'http://public.example.org/file.tar.gz',
    'https://example.org:8080/file.tar.gz',
    'https://user:token@example.org/file.tar.gz',
    'https://example.org/file.tar.gz#fragment',
    'file:///etc/passwd',
    'https://127.0.0.1/file.tar.gz',
])
def test_rejects_insecure_redirects(url, monkeypatch):
    def fake_dns(host, port, **kwargs):
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(host if host == '127.0.0.1' else '8.8.8.8',443))]
    monkeypatch.setattr('pharos.kiwix_runtime.socket.getaddrinfo', fake_dns)
    with pytest.raises(KiwixRuntimeError, match='kiwix_untrusted_download_redirect'):
        _PublisherRedirects().redirect_request(urllib.request.Request(PUBLISHER + 'a.tar.gz'), None, 302, 'Found', {}, url)


def test_rejects_private_dns_answers_and_unresolvable_host(monkeypatch):
    def spoofed(*_args, **_kw):
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),
                (socket.AF_INET,socket.SOCK_STREAM,6,'',('192.168.0.2',443))]
    monkeypatch.setattr('pharos.kiwix_runtime.socket.getaddrinfo', spoofed)
    with pytest.raises(KiwixRuntimeError, match='kiwix_untrusted_download_redirect'):
        _PublisherRedirects().redirect_request(urllib.request.Request(PUBLISHER+'a.tar.gz'), None, 302, 'Found', {}, 'https://public-looking.example.org/archive')
    def unknown(*_args, **_kw):
        raise socket.gaierror('host not found')
    monkeypatch.setattr('pharos.kiwix_runtime.socket.getaddrinfo', unknown)
    with pytest.raises(KiwixRuntimeError, match='kiwix_untrusted_download_redirect'):
        _PublisherRedirects().redirect_request(urllib.request.Request(PUBLISHER+'a.tar.gz'), None, 302, 'Found', {}, 'https://missing.example.org/archive')


def test_kiwix_tool_install_starts_at_publisher_and_checks_checksum(prepared, monkeypatch):
    # The fixture is a Linux tar.gz. Simulate Linux also on Windows runners: 
    # otherwise the service correctly looks for kiwix-serve.exe and the fixture
    # wrongly fails, testing host detection rather than publisher validation.
    monkeypatch.setattr('pharos.kiwix_runtime.platform.system', lambda: 'Linux')
    cfg=load_config(prepared)
    service=KiwixManagedService(cfg)
    filename=platform_asset('linux','x86_64')
    monkeypatch.setattr('pharos.kiwix_runtime.platform_asset', lambda *args, **kwargs: filename)
    body=b'kiwix-serve-executable-stub'
    content=io.BytesIO()
    with tarfile.open(fileobj=content,mode='w:gz') as output:
        info=tarfile.TarInfo('kiwix-tools/kiwix-serve')
        info.size=len(body)
        output.addfile(info, io.BytesIO(body))
    archive=content.getvalue()
    checksum=hashlib.md5(archive).hexdigest().encode()+b'  '+filename.encode()
    visited=[]
    class FakeReply(io.BytesIO):
        def __init__(self,payload):
            super().__init__(payload)
            self.headers={'Content-Length':str(len(payload))}
    class FakeOpener:
        def open(self,request,timeout=45):
            visited.append(request.full_url)
            return FakeReply(checksum if request.full_url.endswith('.md5') else archive)
    monkeypatch.setattr('pharos.kiwix_runtime.urllib.request.build_opener',lambda *_:FakeOpener())
    service.install()
    assert service.binary_installed()
    assert service.binary.read_bytes()==body
    assert visited==[PUBLISHER+filename+'.md5',PUBLISHER+filename]
    with pytest.raises(KiwixRuntimeError, match='kiwix_download_source_invalid'):
        service._download(PUBLISHER+'other.tar.gz',cfg.storage.database_dir/'unsafe',8192)



def test_kiwix_windows_zip_install_mocked_on_any_os(prepared, monkeypatch):
    """Validate Windows .zip and .exe selection with no Windows installation."""
    monkeypatch.setattr('pharos.kiwix_runtime.platform.system', lambda: 'Windows')
    monkeypatch.setattr('pharos.kiwix_runtime.platform.machine', lambda: 'AMD64')
    cfg = load_config(prepared)
    service = KiwixManagedService(cfg)
    filename = platform_asset()
    assert filename == ARCHIVES[('windows', 'amd64')]
    assert filename == 'kiwix-tools_win-x86_64-3.8.1.zip'
    assert platform_asset('linux', 'x86_64') == 'kiwix-tools_linux-x86_64-3.8.2.tar.gz'
    assert service.binary.name == 'kiwix-serve.exe'
    binary = b'PHAROS-WINDOWS-EXE-STUB'
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('kiwix-tools/kiwix-serve.exe', binary)
        z.writestr('kiwix-tools/icuuc74.dll', b'fixture-dll')
    archive = data.getvalue()
    checksum = hashlib.md5(archive).hexdigest().encode() + b'  ' + filename.encode()
    calls = []

    class FakeReply(io.BytesIO):
        def __init__(self, payload):
            super().__init__(payload)
            self.headers = {'Content-Length': str(len(payload))}

    class FakeOpener:
        def open(self, request, timeout=45):
            calls.append(request.full_url)
            return FakeReply(checksum if request.full_url.endswith('.md5') else archive)

    monkeypatch.setattr('pharos.kiwix_runtime.urllib.request.build_opener', lambda *_: FakeOpener())
    service.install()
    assert service.binary_installed()
    assert service.binary.read_bytes() == binary
    import json
    assert json.loads(service.manifest.read_text())['version'] == '3.8.1'
    dll = service.code_dir / 'icuuc74.dll'
    assert dll.read_bytes() == b'fixture-dll'
    dll.unlink()
    assert not service.binary_installed()
    service.install()
    assert service.binary_installed()
    assert calls == [PUBLISHER + filename + '.md5', PUBLISHER + filename] * 2


def test_kiwix_tool_install_checksum_mismatch_fails_closed(prepared, monkeypatch):
    service=KiwixManagedService(load_config(prepared))
    class FakeReply(io.BytesIO):
        headers={}
    class FakeOpener:
        def open(self,request,timeout=45):
            return FakeReply((b'0'*32) if request.full_url.endswith('.md5') else b'untrusted-archive')
    monkeypatch.setattr('pharos.kiwix_runtime.urllib.request.build_opener', lambda *_:FakeOpener())
    with pytest.raises(KiwixRuntimeError,match='kiwix_archive_checksum_mismatch'):
        service.install()
    assert not service.binary_installed()
