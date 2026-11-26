"""LAN proxy tests: generic IP/DNS routing, safe nginx and Kiwix behavior.

No sudo, apt, NGINX or live network calls are executed in these tests.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from pharos import lan_proxy as lp
from pharos.app import create_app
from pharos.config import load_config
from pharos.kiwix.library import KiwixLibrary


def test_nginx_site_accepts_any_lan_ip_or_dns_without_fixed_name():
    cfg = lp.nginx_site(8080)
    assert 'server_name _;' in cfg
    assert 'listen 80 default_server;' in cfg
    assert 'listen [::]:80 default_server;' in cfg
    assert 'pharos.local' not in cfg
    assert 'proxy_pass http://127.0.0.1:8080;' in cfg
    assert 'proxy_pass http://127.0.0.1:8080/;' not in cfg
    assert 'proxy_set_header Host $http_host;' in cfg
    assert 'client_max_body_size 128m' in cfg
    assert 'location / {' in cfg
    assert 'location /kiwix' not in cfg  # PHAROS owns the second hop


@pytest.mark.parametrize('port', [0, 80, 443, 65536, True, '8080'])
def test_nginx_rejects_invalid_internal_port(port):
    with pytest.raises(lp.LANProxyError):
        lp.nginx_site(port)


# Managing Nginx and creating Unix-style symlinks is Linux-specific. Keep the
# portable site-rendering/Kiwix HTTP tests running on every CI platform.
unix_nginx_only = pytest.mark.skipif(
    sys.platform == 'win32', reason='Nginx system installation and Unix symlink simulation require Linux'
)

def _paths(tmp_path, monkeypatch):
    available = tmp_path / 'available'
    enabled = tmp_path / 'enabled'
    available.mkdir(); enabled.mkdir()
    for attr, path in [('SITE_PATH', available / 'pharos'),
                       ('ENABLED_PATH', enabled / 'pharos'),
                       ('DEFAULT_AVAILABLE', available / 'default'),
                       ('DEFAULT_ENABLED', enabled / 'default')]:
        monkeypatch.setattr(lp, attr, path)
    monkeypatch.setattr(lp.os, 'geteuid', lambda: 1000, raising=False)
    monkeypatch.setattr(lp.shutil, 'which', lambda cmd: '/usr/bin/' + cmd)
    return available, enabled


@unix_nginx_only
def test_nginx_never_overwrites_foreign_site(tmp_path, monkeypatch):
    available, _ = _paths(tmp_path, monkeypatch)
    path = available / 'pharos'
    path.write_text('server { listen 80; server_name other.local; }')
    monkeypatch.setattr(lp, '_privileged', lambda *args:
                        (_ for _ in ()).throw(AssertionError('Must not modify foreign site')))
    with pytest.raises(lp.LANProxyError, match='altro servizio'):
        lp.configure_nginx(8080)
    assert path.read_text().startswith('server {')


@unix_nginx_only
def test_nginx_refuses_modified_ubuntu_default(tmp_path, monkeypatch):
    available, enabled = _paths(tmp_path, monkeypatch)
    (available / 'default').write_text('server { listen 80 default_server; root /srv/custom; }')
    (enabled / 'default').symlink_to(available / 'default')
    monkeypatch.setattr(lp, '_privileged', lambda *args:
                        (_ for _ in ()).throw(AssertionError('Must not modify customized default')))
    with pytest.raises(lp.LANProxyError, match='personalizzato'):
        lp.configure_nginx(8080)
    assert (enabled / 'default').is_symlink()


@unix_nginx_only
def test_nginx_refuses_other_sites(tmp_path, monkeypatch):
    _, enabled = _paths(tmp_path, monkeypatch)
    (enabled / 'another-site').write_text('server { listen 80; }')
    with pytest.raises(lp.LANProxyError, match='Altri siti'):
        lp.configure_nginx(8080)


@unix_nginx_only
def test_nginx_recognizes_untouched_ubuntu_default(tmp_path, monkeypatch):
    available, enabled = _paths(tmp_path, monkeypatch)
    (available / 'default').write_text('''# Default server configuration
server { listen 80 default_server; root /var/www/html; server_name _; }
''')
    (enabled / 'default').symlink_to(available / 'default')
    assert lp._stock_default_enabled()


@unix_nginx_only
def test_nginx_disables_only_untouched_ubuntu_default(tmp_path, monkeypatch):
    available, enabled = _paths(tmp_path, monkeypatch)
    default = available / 'default'
    default.write_text('''# Default server configuration\nserver { listen 80 default_server; root /var/www/html; server_name _; }\n''')
    (enabled / 'default').symlink_to(default)
    monkeypatch.setattr(lp, '_port_occupied', lambda: True)
    monkeypatch.setattr(lp, '_nginx_running', lambda: True)
    monkeypatch.setattr(lp, '_run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='test-user\n' if args[0] == 'id' else 'yes\n'))
    monkeypatch.setenv('USER', 'test-user')
    commands = []
    def fake_privileged(*cmd):
        commands.append(cmd)
        if cmd[:1] == ('install',) and '-m' in cmd and '644' in cmd:
            Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        elif cmd[0] == 'mv':
            Path(cmd[-2]).replace(Path(cmd[-1]))
        elif cmd[0] == 'rm':
            Path(cmd[-1]).unlink(missing_ok=True)
        elif cmd[0] == 'ln':
            Path(cmd[-1]).symlink_to(Path(cmd[-2]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lp, '_privileged', fake_privileged)
    result = lp.configure_nginx(8080)
    assert result['changed']
    assert not (enabled / 'default').exists()
    assert (enabled / 'pharos').is_symlink()
    assert 'listen 80 default_server;' in (available / 'pharos').read_text()
    assert default.exists()  # distro file untouched, only symlink disabled
    assert ('nginx', '-t') in commands


@unix_nginx_only
def test_nginx_rolls_back_default_on_validation_error(tmp_path, monkeypatch):
    available, enabled = _paths(tmp_path, monkeypatch)
    default = available / 'default'
    default.write_text('''# Default server configuration\nserver { listen 80 default_server; root /var/www/html; server_name _; }\n''')
    (enabled / 'default').symlink_to(default)
    monkeypatch.setattr(lp, '_port_occupied', lambda: False)
    monkeypatch.setattr(lp, '_nginx_running', lambda: True)
    def fake_privileged(*cmd):
        if cmd[:2] == ('nginx', '-t'):
            raise lp.LANProxyError('invalid nginx config')
        if cmd[:1] == ('install',) and '-m' in cmd and '644' in cmd:
            Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
        elif cmd[0] == 'mv':
            Path(cmd[-2]).replace(Path(cmd[-1]))
        elif cmd[0] == 'rm':
            Path(cmd[-1]).unlink(missing_ok=True)
        elif cmd[0] == 'ln':
            Path(cmd[-1]).symlink_to(Path(cmd[-2]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lp, '_privileged', fake_privileged)
    with pytest.raises(lp.LANProxyError, match='invalid nginx config'):
        lp.configure_nginx(8080)
    assert (enabled / 'default').is_symlink()
    assert not (enabled / 'pharos').exists()
    assert not (available / 'pharos').exists()


def test_home_centered_without_modifying_user_notification_css():
    from importlib.resources import files
    css = files('pharos').joinpath('web/layout-dev2.css').read_text(encoding='utf-8')
    assert '#view-home .home-grid{display:flex;flex-wrap:wrap;justify-content:center' in css
    assert '.notification-indicator{position:absolute;right:1%;top:5px;' in css
    assert 'width:98%;height:23px' in css


@unix_nginx_only
def test_nginx_reuses_managed_identical_site_without_sudo(tmp_path, monkeypatch):
    available, enabled = _paths(tmp_path, monkeypatch)
    path = available / 'pharos'
    path.write_text(lp.nginx_site(8080))
    (enabled / 'pharos').symlink_to(path)
    monkeypatch.setattr(lp, '_port_occupied', lambda: True)
    monkeypatch.setattr(lp, '_nginx_running', lambda: True)
    def fake_run(*cmd, **kwargs):
        if cmd[0] == 'id':
            return SimpleNamespace(returncode=0, stdout='test-user\n')
        if cmd[0] == 'loginctl':
            return SimpleNamespace(returncode=0, stdout='yes\n')
        raise AssertionError(f'Unexpected command: {cmd}')
    monkeypatch.setattr(lp, '_run', fake_run)
    monkeypatch.setattr(lp, '_privileged', lambda *args:
                        (_ for _ in ()).throw(AssertionError('sudo not needed')))
    monkeypatch.setenv('USER', 'test-user')
    result = lp.configure_nginx(8080)
    assert result['changed'] is False
    assert 'IP-o-DNS' in result['url_hint']


def test_kiwix_catalog_uses_same_origin_root_and_upstream_prefix(monkeypatch):
    requests = []
    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(200, text='''<?xml version="1.0"?>
            <feed><entry><title>Wikipedia</title><name>wikipedia_it</name></entry></feed>''')
    real_client = httpx.AsyncClient
    monkeypatch.setattr('pharos.kiwix.library.httpx.AsyncClient', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(transport)))
    lib = KiwixLibrary('http://127.0.0.1:8081', '/kiwix')
    data = asyncio.run(lib.books())
    assert requests and '/kiwix/catalog/v2/entries' in requests[0]
    assert data['items'][0]['url'] == '/kiwix/content/wikipedia_it'
    assert data['browser_url'] == '/kiwix'


def test_kiwix_catalog_link_fallback_supports_managed_prefix():
    from pharos.kiwix.library import parse_books
    xml = b'<feed><entry><title>Italiano</title><link href="/kiwix/content/wikipedia_it/" /></entry></feed>'
    books = parse_books(xml)
    assert books[0]['name'] == 'wikipedia_it'


def test_kiwix_proxy_forwards_path_range_and_local_redirect(prepared, monkeypatch):
    upstream = []
    def transport(req):
        upstream.append(req)
        if req.url.path.endswith('/article'):
            return httpx.Response(302, stream=httpx.ByteStream(b''), headers={'Location':'/kiwix/content/wikipedia_it/article2'})
        return httpx.Response(206, stream=httpx.ByteStream(b'segment'), headers={'Content-Range':'bytes 0-6/100'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr('pharos.kiwix_proxy.httpx.AsyncClient', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(transport)))
    with TestClient(create_app(load_config(prepared))) as c:
        monkeypatch.setattr(c.app.state.kiwix_service, 'ensure_running', lambda server_url: True)
        got = c.get('/kiwix/content/wikipedia_it/article', follow_redirects=False)
        assert got.status_code == 302
        assert got.headers['location'] == '/kiwix/content/wikipedia_it/article2'
        data = c.get('/kiwix/content/wikipedia_it/photo', headers={'Range':'bytes=0-6'})
        assert data.status_code == 206
        assert data.content == b'segment'
        assert data.headers.get('content-range') == 'bytes 0-6/100'
    assert upstream[0].url.path == '/kiwix/content/wikipedia_it/article'
    assert upstream[1].headers['range'] == 'bytes=0-6'


def test_settings_retry_only_safe_reads():
    from importlib.resources import files
    code = files('pharos').joinpath('web/settings.js').read_text(encoding='utf-8')
    assert 'for(let attempt=0;attempt<4;attempt++)' in code
    assert "const result=await api('settings')" in code
    assert "api('settings',{method:'PUT'" in code  # PUT is separate, no automatic retry
