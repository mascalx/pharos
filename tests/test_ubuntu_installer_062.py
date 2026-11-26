"""Ubuntu installer offline regression: publisher integrity, idempotence, no data loss.

No real systemd, network, or executable launches in this test module.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pharos import ubuntu_installer as u
from pharos.config import load_config
from pharos.kiwix_runtime import KiwixManagedService


def test_platform_ubuntu_only(monkeypatch):
    monkeypatch.setattr(u.os, 'geteuid', lambda: 1000, raising=False)
    u.require_platform('linux', 'x86_64', 'ID=ubuntu\n')
    for osname, arch, release in [('linux', 'aarch64', 'ID=ubuntu\n'),
                                   ('linux', 'x86_64', 'ID=debian\n'),
                                   ('darwin', 'arm64', 'ID=ubuntu\n')]:
        with pytest.raises(u.InstallError):
            u.require_platform(osname, arch, release)


def test_qdrant_release_digest_requires_official_exact_name_and_tag(monkeypatch):
    class FakeResponse:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n=None): return self.data
    class FakeOpener:
        def __init__(self, data): self.data = data
        def open(self, request, timeout=35): return FakeResponse(json.dumps(self.data).encode())
    asset = {'name': u.QDRANT_ASSET, 'browser_download_url': u.QDRANT_SOURCE,
             'digest': 'sha256:' + u.QDRANT_SHA256}
    assert u.release_asset_digest(FakeOpener({'tag_name': u.QDRANT_TAG, 'assets':[asset]})) == u.QDRANT_SHA256
    for altered in [dict(asset, browser_download_url='https://evil.invalid/asset'),
                    dict(asset, digest='sha256:' + 'f'*64)]:
        with pytest.raises(u.InstallError):
            u.release_asset_digest(FakeOpener({'tag_name': u.QDRANT_TAG, 'assets':[altered]}))
    with pytest.raises(u.InstallError):
        u.release_asset_digest(FakeOpener({'tag_name': 'other-tag', 'assets':[asset]}))


def _tarball(path, *, name='qdrant', link=False):
    with tarfile.open(path, 'w:gz') as tar:
        member = tarfile.TarInfo(name)
        member.mode = 0o755
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = '/bin/sh'
            member.size = 0
            tar.addfile(member)
        else:
            payload = b'PHAROS-FAKE-QDRANT'
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))


def test_qdrant_archive_rejects_links_and_paths(tmp_path):
    archive = tmp_path/'asset.tar.gz'
    for name, link in [('qdrant', True),('../qdrant', False)]:
        _tarball(archive, name=name, link=link)
        with pytest.raises(u.InstallError):
            u.extract_qdrant(archive, tmp_path/'qdrant')
        assert not (tmp_path/'qdrant').exists()
    _tarball(archive)
    u.extract_qdrant(archive, tmp_path/'qdrant')
    assert (tmp_path/'qdrant').read_bytes() == b'PHAROS-FAKE-QDRANT'
    assert os.access(tmp_path/'qdrant', os.X_OK)


def test_qdrant_install_requires_pinned_check_and_never_overwrites(tmp_path, monkeypatch):
    target = tmp_path/'qdrant-install'
    installer = u.QdrantInstaller(target)
    called = 0
    def fake_download(archive):
        nonlocal called
        called += 1
        _tarball(archive)
    monkeypatch.setattr(u, 'download_qdrant', fake_download)
    assert installer.install() is True
    assert installer.installed()
    assert installer.install() is False
    assert called == 1
    assert installer.manifest.exists()
    installer.binary.write_bytes(b'tampered')
    with pytest.raises(u.InstallError):
        installer.install()  # no destructive replacement of unknown binaries


def test_configuration_generation_does_not_replace_original(tmp_path):
    cfg = tmp_path/'config.toml'
    data, db, cache = [tmp_path/x for x in ('data', 'db', 'cache')]
    args = (cfg, data, db, cache, 'http://192.168.0.100', 'tiiny_direct', '', 'it', '0.0.0.0', 8080, False)
    assert u.config_template(*args)
    original = cfg.read_bytes()
    assert not u.config_template(*args)
    assert cfg.read_bytes() == original
    conf = load_config(cfg)
    assert conf.storage.data_dir == data
    assert conf.storage.database_dir == db
    assert conf.ai.enabled
    assert not conf.rag.enabled  # no embedding model chosen
    assert conf.server.host == '0.0.0.0'


def test_yaml_config_local_and_idempotent(tmp_path):
    cfg = tmp_path/'qdrant.yaml'
    assert u.yaml_config(cfg, tmp_path/'db data', tmp_path/'snaps')
    body = cfg.read_text()
    assert 'host: 127.0.0.1' in body
    assert 'grpc_port: null' in body
    assert 'enable_cors: false' in body
    assert not u.yaml_config(cfg, tmp_path/'db data', tmp_path/'snaps')


def test_systemd_unit_has_local_service_and_restart():
    qdrant = u.unit_content('qdrant', ['/my path/qdrant', '--config-path', '/test/q.yaml'], qdrant=True)
    pharos = u.unit_content('pharos', ['/my path/pharos', 'serve', '--config', '/test/config.toml'], qdrant=False)
    assert 'ExecStart="/my path/qdrant"' in qdrant
    assert 'Requires=pharos-qdrant.service' in pharos
    assert 'NoNewPrivileges=true' in pharos
    assert 'Restart=on-failure' in qdrant
    assert u.systemd_escape('/name%') == '"/name%%"'


def test_existing_config_and_volume_survive_repeated_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    fake_venv = tmp_path/'venv'/'bin'
    fake_venv.mkdir(parents=True)
    (fake_venv/'pharos').write_text('#!/bin/sh\n')
    monkeypatch.setattr(u.sys, 'executable', str(fake_venv/'python'))
    monkeypatch.setattr(u, '_is_network_storage', lambda path: False)
    downloads = []
    def fake_qdrant(self):
        downloads.append('qdrant')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.binary.write_text('fake')
    def fake_kiwix(self, progress=None):
        downloads.append('kiwix')
        self.code_dir.mkdir(parents=True, exist_ok=True)
        self.binary.write_text('fake')
    monkeypatch.setattr(u.QdrantInstaller, 'install', fake_qdrant)
    monkeypatch.setattr(KiwixManagedService, 'install', fake_kiwix)
    args = u.parser().parse_args(['--data-dir', str(tmp_path/'store'), '--ai-url', 'http://192.168.0.11', '--no-start'])
    result = u.setup(args, check_platform=False)
    assert result['services_started'] is False
    cfg = Path(result['config'])
    data = Path(result['data'])
    marker = (data/'.pharos-volume').read_bytes()
    config_content = cfg.read_bytes()
    sentinel = data/'keep-this-data.zim'
    sentinel.write_text('content')
    u.setup(args, check_platform=False)
    assert cfg.read_bytes() == config_content
    assert (data/'.pharos-volume').read_bytes() == marker
    assert sentinel.read_text() == 'content'
    assert downloads == ['qdrant','kiwix','qdrant','kiwix']  # mock only; real downloader is idempotent
    with pytest.raises(u.InstallError):
        u.setup(u.parser().parse_args(['--data-dir',str(tmp_path/'elsewhere'),'--no-start']), check_platform=False)


def test_existing_volume_without_config_is_never_claimed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    data = tmp_path/'existing'
    data.mkdir()
    (data/'.pharos-volume').write_text('other-installation')
    with pytest.raises(u.InstallError, match='già inizializzato'):
        u.setup(u.parser().parse_args(['--data-dir',str(data),'--no-start']), check_platform=False)


def test_existing_qdrant_yaml_refuses_silent_rewrite(tmp_path):
    file = tmp_path/'qdrant.yaml'
    assert u.yaml_config(file, tmp_path/'data', tmp_path/'snapshots')
    with pytest.raises(u.InstallError, match='non corrisponde'):
        u.yaml_config(file, tmp_path/'moved-data', tmp_path/'snapshots')
    assert 'storage_path: ' + json.dumps(str(tmp_path/'data')) in file.read_text(encoding='utf-8')


def test_nonempty_data_dir_without_marker_requires_original_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    data = tmp_path/'existing'
    data.mkdir()
    (data/'preserve.pdf').write_text('valuable')
    with pytest.raises(u.InstallError, match='non vuota'):
        u.setup(u.parser().parse_args(['--data-dir',str(data),'--no-start']), check_platform=False)
    assert (data/'preserve.pdf').read_text() == 'valuable'


def test_systemctl_start_and_readiness_order_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    fake_bin = tmp_path/'venv'/'bin'
    fake_bin.mkdir(parents=True)
    (fake_bin/'pharos').write_text('stub')
    monkeypatch.setattr(u.sys, 'executable', str(fake_bin/'python'))
    monkeypatch.setattr(u, '_is_network_storage', lambda path: False)
    monkeypatch.setattr(u.QdrantInstaller, 'install', lambda self: (self.directory.mkdir(parents=True, exist_ok=True), self.binary.write_text('stub')))
    monkeypatch.setattr(KiwixManagedService, 'install', lambda self: (self.code_dir.mkdir(parents=True, exist_ok=True), self.binary.write_text('stub')))
    calls = []
    monkeypatch.setattr(u, '_run_systemctl', lambda *args: calls.append(args))
    monkeypatch.setattr(u, '_http_ready', lambda *args: True)
    result = u.setup(u.parser().parse_args([]), check_platform=False)
    assert result['services_started']
    assert calls == [('daemon-reload',), ('enable','--now','pharos-qdrant.service'),
                     ('is-active','--quiet','pharos-qdrant.service'),
                     ('enable','pharos.service'), ('restart','pharos.service'),
                     ('is-active','--quiet','pharos.service')]
