"""Windows foreground process supervisor; no external dependency."""
import ctypes
from ctypes import wintypes
import msvcrt
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from pharos.config import load_config


def bind_job():
    # Assign the supervisor BEFORE starting children. They inherit membership;
    # the non-inheritable job handle lives until the supervisor exits.
    class Basic(ctypes.Structure):
        _fields_ = [('per_process', ctypes.c_int64), ('per_job', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                    ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                    ('scheduling', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                    ('peak_job', ctypes.c_size_t)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.AssignProcessToJobObject.restype = wintypes.BOOL
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        api.CloseHandle(job)
        raise ctypes.WinError(error)
    if not api.AssignProcessToJobObject(job, api.GetCurrentProcess()):
        error = ctypes.get_last_error()
        api.CloseHandle(job)
        raise ctypes.WinError(error)
    # Deliberately do not close this handle while this process is in the job.
    return job


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(root):
    root = root.resolve()
    config = root / 'config' / 'config.toml'
    qdrant = root / 'bin' / 'qdrant.exe'
    qconfig = root / 'config' / 'qdrant.yaml'
    for path in (config, qdrant, qconfig):
        if not path.is_file():
            raise RuntimeError(f'Missing PHAROS file: {path}')
    port = load_config(config).server.port
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    with (root / 'launcher.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('PHAROS launcher is already running.') from exc
        job = bind_job()
        for check in {6333, 6334, port}:
            if listening(check):
                raise RuntimeError(f'Port {check} already occupied. Stop the previous PHAROS instance or identify the other service; no process was terminated.')
        q = core = None
        log = logs / 'qdrant.log'
        try:
            with log.open('ab') as output:
                output.write(b'\n--- PHAROS launcher start ---\n')
                output.flush()
                q = subprocess.Popen([str(qdrant), '--config-path', str(qconfig)],
                                     cwd=root, stdout=output, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if q.poll() is not None:
                        raise RuntimeError(f'Qdrant exited with code {q.returncode}; see {log}')
                    try:
                        with opener.open('http://127.0.0.1:6333/readyz', timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError(f'Qdrant readiness timeout; see {log}')
                print(f'PHAROS starting at http://127.0.0.1:{port}/', flush=True)
                core = subprocess.Popen([sys.executable, '-m', 'pharos.cli', 'serve', '--config', str(config)], cwd=root)
                while core.poll() is None:
                    if q.poll() is not None:
                        raise RuntimeError(f'Qdrant stopped with code {q.returncode}; see {log}')
                    time.sleep(0.25)
                return core.returncode
        except KeyboardInterrupt:
            # Ctrl+C also reaches the foreground Core; allow its lifespan shutdown.
            if core is not None:
                try:
                    core.wait(timeout=15)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    pass
            return 0
        finally:
            stop(core)
            stop(q)
            # Job handle remains open until OS closes it on process exit.


if __name__ == '__main__':
    try:
        sys.exit(run(Path(sys.argv[1])))
    except Exception as exc:
        print(f'PHAROS: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)
