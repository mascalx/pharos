"""Regression for Kiwix Tools 3.8.2: accepts individual ZIMPATHs, not directories.

No real network, process, or archive is used. The 3.8.2 upstream
implementation calls addBookFromPath on each positional argument.
"""
from pathlib import Path

from pharos.config import load_config
from pharos.kiwix_runtime import KiwixManagedService


class DummyProcess:
    def __init__(self, argv):
        self.argv = argv
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def setup(prepared, monkeypatch):
    from pharos import kiwix_runtime

    service = KiwixManagedService(load_config(prepared))
    service.prepare_data()
    service.code_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(KiwixManagedService, 'binary_installed', lambda self: True)
    monkeypatch.setattr(kiwix_runtime.time, 'sleep', lambda _: None)
    monkeypatch.setattr(KiwixManagedService, '_http_ready', lambda self, host, port: self._process is not None and self._process.poll() is None)
    launched = []

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs, DummyProcess(argv)))
        return launched[-1][2]

    monkeypatch.setattr(kiwix_runtime.subprocess, 'Popen', fake_popen)
    return service, launched


def test_passes_actual_zim_files_as_separate_positional_args(prepared, monkeypatch):
    service, launched = setup(prepared, monkeypatch)
    a = service.zim_dir / 'a-enciclopedia.zim'
    b = service.zim_dir / 'z-spazi e accenti é.zim'
    a.write_bytes(b'zim-a')
    b.write_bytes(b'zim-b')
    # These are NOT ZIM archives; must not reach kiwix-serve argv.
    (service.zim_dir / 'unfinished.zim.part').write_bytes(b'partial')
    (service.zim_dir / 'a-enciclopedia.zim.sha256').write_text('checksum')
    (service.zim_dir / 'nested').mkdir()
    (service.zim_dir / 'nested' / 'not-recursed.zim').write_bytes(b'not in root')
    try:
        (service.zim_dir / 'suspicious.zim').symlink_to(a)
    except (OSError, NotImplementedError):
        pass  # Windows runners may not permit developer symlinks

    assert service.ensure_running('http://127.0.0.1:8081') is True
    assert len(launched) == 1
    argv = launched[0][0]
    assert argv[0] == str(service.binary)
    assert '--address=127.0.0.1' in argv
    assert '--port=8081' in argv
    assert any(x.startswith('--attachToProcess=') for x in argv)
    assert argv[-2:] == [str(a), str(b)]
    assert str(service.zim_dir) not in argv
    assert str(service.zim_dir / 'unfinished.zim.part') not in argv
    assert str(service.zim_dir / 'suspicious.zim') not in argv
    assert launched[0][1]['cwd'] == str(service.code_dir)
    service.stop()


def test_empty_library_does_not_launch_process(prepared, monkeypatch):
    service, launched = setup(prepared, monkeypatch)
    (service.zim_dir / 'incomplete.zim.part').write_bytes(b'partial')
    assert service.ensure_running('http://127.0.0.1:8081') is False
    assert not launched
    assert service.status()['waiting_for_zim']


def test_zim_addition_restarts_server_with_updated_arguments(prepared, monkeypatch):
    service, launched = setup(prepared, monkeypatch)
    a = service.zim_dir / 'one.zim'
    b = service.zim_dir / 'two.zim'
    a.write_bytes(b'a')
    assert service.ensure_running('http://localhost:8081') is True
    assert len(launched) == 1
    assert launched[0][0][-1] == str(a)
    assert service.ensure_running('http://localhost:8081') is True
    assert len(launched) == 1  # no unnecessary restart
    b.write_bytes(b'b')
    assert service.ensure_running('http://localhost:8081') is True
    assert launched[0][2].terminated
    assert len(launched) == 2
    assert launched[1][0][-2:] == [str(a), str(b)]
    service.stop()
