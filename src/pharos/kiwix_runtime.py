"""Kiwix binary and subprocess lifecycle, independent of the Kiwix extension wheel.

ZIM data is stored exclusively under storage.data_dir/kiwix/zim. Binary code,
software download cache and process metadata live under storage.database_dir.
No pip, sudo, Docker, shell or system package manager is used.

Important trust boundary: downloads use a fixed HTTPS publisher origin and its
sidecar checksum. Upstream provides MD5 (not an authenticated SHA-256 pin). The
strong locally stored SHA-256 verifies subsequent use, but a first-time download
cannot claim reproducible publisher attestation without a pinned signed hash.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .storage import check_data_volume

log = logging.getLogger(__name__)
KIWIX_VERSION = '3.8.2'
PUBLISHER = 'https://download.kiwix.org/release/kiwix-tools/'
MAX_ARCHIVE = 85 * 1024 * 1024
MAX_BINARY = 110 * 1024 * 1024
# Tested via published asset names, not real execution on non-Linux platforms.
ARCHIVES = {
    ('linux', 'x86_64'): 'kiwix-tools_linux-x86_64-3.8.2.tar.gz',
    ('linux', 'aarch64'): 'kiwix-tools_linux-aarch64-3.8.2.tar.gz',
    ('darwin', 'x86_64'): 'kiwix-tools_macos-x86_64-3.8.2.tar.gz',
    ('darwin', 'arm64'): 'kiwix-tools_macos-arm64-3.8.2.tar.gz',
    ('windows', 'amd64'): 'kiwix-tools_win-x86_64-3.8.1.zip',
}


class KiwixRuntimeError(ValueError):
    pass


def platform_asset(system: str | None = None, machine: str | None = None) -> str:
    sysname = (system or platform.system()).lower()
    arch = (machine or platform.machine()).lower()
    normalized = {'amd64': 'x86_64', 'arm64': 'aarch64'} if sysname == 'linux' else {}
    arch = normalized.get(arch, arch)
    if sysname == 'windows':
        arch = 'amd64' if arch in ('x86_64', 'amd64') else arch
    if sysname == 'darwin':
        arch = 'arm64' if arch in ('aarch64', 'arm64') else arch
    try:
        return ARCHIVES[(sysname, arch)]
    except KeyError as exc:
        raise KiwixRuntimeError('kiwix_platform_not_supported') from exc


def asset_version(filename: str) -> str:
    # The official Windows build is published separately from Linux/macOS.
    if filename not in ARCHIVES.values():
        raise KiwixRuntimeError('kiwix_platform_not_supported')
    return filename.rsplit('-', 1)[1].split('.zip')[0].split('.tar.gz')[0]


class _PublisherRedirects(urllib.request.HTTPRedirectHandler):
    """Follow Kiwix-selected public HTTPS mirrors without pinning mirror names.

    Only validated publisher URLs can initiate downloads. Redirected hosts are
    checked for public Internet DNS answers (including IPv6), preventing common
    redirect-to-LAN/loopback SSRF. TLS hostname verification remains enabled.
    A DNS rebinding race is not completely excluded by the stdlib opener; only
    GET, without credentials, is used and downloaded bytes are verified before use.
    """
    max_redirections = 8
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            parts = urlsplit(newurl)
            host = parts.hostname
            port = parts.port
            if (parts.scheme.lower() != 'https' or not host or
                    parts.username is not None or parts.password is not None or
                    port not in (None, 443) or parts.fragment or
                    any(ord(ch) < 33 for ch in newurl)):
                raise ValueError('unsafe redirect URL')
            # Resolve all published IPs, not just the first address. A DNS entry
            # with ANY private/link-local/reserved response is rejected.
            endpoints = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not endpoints or any(not ipaddress.ip_address(info[4][0]).is_global
                                    for info in endpoints):
                raise ValueError('non-public redirect destination')
        except (ValueError, UnicodeError, OSError) as exc:
            raise KiwixRuntimeError('kiwix_untrusted_download_redirect') from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class KiwixManagedService:
    """Single-worker process owner and pinned-version binary installer.

    A server is spawned only for a directory containing at least one local ZIM.
    It listens on the configured URL host, with loopback default. The service
    process is stopped with PHAROS, and restarted on the next PHAROS startup.
    """

    def __init__(self, config):
        self.config = config
        self.data_dir = config.storage.data_dir / 'kiwix'
        self.zim_dir = self.data_dir / 'ZIM'
        self.code_dir = config.storage.database_dir / 'pharos-managed-services' / 'kiwix'
        self.bin_name = 'kiwix-serve.exe' if platform.system().lower() == 'windows' else 'kiwix-serve'
        self.binary = self.code_dir / self.bin_name
        self.manifest = self.code_dir / 'install.json'
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._zim_signature: tuple = ()
        self._last_error = ''
        self._bound_url = ''

    def _require_storage(self):
        if not check_data_volume(self.config.storage).available:
            raise KiwixRuntimeError('data_volume_unavailable')
        if self.zim_dir.is_symlink() or self.data_dir.is_symlink():
            raise KiwixRuntimeError('kiwix_storage_symlink_forbidden')

    def _zims(self):
        self._require_storage()
        if not self.zim_dir.exists():
            return []
        return sorted(p for p in self.zim_dir.iterdir()
                      if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.zim')

    def prepare_data(self):
        self._require_storage()
        self.zim_dir.mkdir(parents=True, exist_ok=True)

    def binary_installed(self) -> bool:
        try:
            record = json.loads(self.manifest.read_text(encoding='utf-8'))
            asset = platform_asset()
            if record.get('version') != asset_version(asset) or record.get('asset') != asset:
                return False
            sha = record.get('binary_sha256', '')
            if not re.fullmatch('[a-f0-9]{64}', sha) or self.binary.is_symlink():
                return False
            if not self.binary.is_file() or _sha256_file(self.binary) != sha:
                return False
            if asset.endswith('.zip'):
                dlls = record.get('dll_sha256')
                if not isinstance(dlls, dict) or not dlls:
                    return False
                for name, digest in dlls.items():
                    if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.dll', name, re.I):
                        return False
                    dll = self.code_dir / name
                    if (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                            or dll.is_symlink() or not dll.is_file() or _sha256_file(dll) != digest):
                        return False
            return True
        except (OSError, ValueError, TypeError, KiwixRuntimeError):
            return False

    def _download(self, url: str, target: Path, max_bytes: int, progress=None):
        # The initial URL must be a pinned, platform-specific artifact at the
        # official publisher. Only its HTTPS redirects can choose other mirrors.
        name = url[len(PUBLISHER):] if url.startswith(PUBLISHER) else ''
        allowed = set(ARCHIVES.values())
        if name not in allowed and not (name.endswith('.md5') and name[:-4] in allowed):
            raise KiwixRuntimeError('kiwix_download_source_invalid')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())
        req = urllib.request.Request(url, headers={'User-Agent': 'PHAROS-Kiwix/0.1'})
        try:
            with opener.open(req, timeout=45) as response, target.open('wb') as output:
                size = 0
                declared = response.headers.get('Content-Length', '')
                total = int(declared) if declared.isdigit() else None
                if total is not None and total > max_bytes:
                    raise KiwixRuntimeError('kiwix_download_too_large')
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise KiwixRuntimeError('kiwix_download_too_large')
                    output.write(block)
                    if progress is not None:
                        progress(size, total)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KiwixRuntimeError('kiwix_download_unavailable') from exc

    def _archive_and_digest(self, stage: Path, filename: str, progress=None) -> Path:
        # The official publisher publishes sidecar MD5 files, not SHA256 pins.
        # A deliberate TLS origin restriction and matching upstream checksum
        # protect ordinary transport errors, but do NOT amount to signed origin.
        checksum_file = stage / 'publisher.md5'
        archive_file = stage / filename
        self._download(PUBLISHER + filename + '.md5', checksum_file, 4096)
        checksum_text = checksum_file.read_text(encoding='ascii', errors='replace')
        expected = re.search(r'(?<![a-fA-F0-9])[a-fA-F0-9]{32}(?![a-fA-F0-9])', checksum_text)
        if not expected:
            raise KiwixRuntimeError('kiwix_publisher_checksum_missing')
        self._download(PUBLISHER + filename, archive_file, MAX_ARCHIVE, progress)
        digest = hashlib.md5()  # nosec: vendor checksum format, downloaded over TLS
        with archive_file.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        actual = digest.hexdigest()
        if actual.lower() != expected.group(0).lower():
            raise KiwixRuntimeError('kiwix_archive_checksum_mismatch')
        return archive_file

    @staticmethod
    def _extract_binary(archive: Path, destination: Path, name: str):
        """Extract ONLY one regular kiwix-serve binary, no links or archive paths."""
        size = 0
        if archive.name.endswith('.zip'):
            with zipfile.ZipFile(archive) as z:
                matches = [x for x in z.infolist() if Path(x.filename).name.lower() == name.lower()]
                if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > MAX_BINARY:
                    raise KiwixRuntimeError('kiwix_archive_invalid')
                entry = matches[0]
                mode = (entry.external_attr >> 16) & 0o170000
                if mode not in (0, stat.S_IFREG):
                    raise KiwixRuntimeError('kiwix_archive_unsafe_member')
                stream = z.open(entry)
                expected_size = entry.file_size
                with stream, destination.open('xb') as out:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BINARY:
                            raise KiwixRuntimeError('kiwix_archive_invalid')
                        out.write(chunk)
        else:
            with tarfile.open(archive, 'r:gz') as tar:
                matches = [x for x in tar if Path(x.name).name == name]
                if len(matches) != 1 or not matches[0].isreg() or matches[0].size > MAX_BINARY:
                    raise KiwixRuntimeError('kiwix_archive_invalid')
                stream = tar.extractfile(matches[0])
                if stream is None:
                    raise KiwixRuntimeError('kiwix_archive_invalid')
                expected_size = matches[0].size
                with stream, destination.open('xb') as out:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BINARY:
                            raise KiwixRuntimeError('kiwix_archive_invalid')
                        out.write(chunk)
        if size != expected_size or size == 0:
            raise KiwixRuntimeError('kiwix_archive_invalid')
        destination.chmod(0o755 if os.name != 'nt' else 0o644)

    @staticmethod
    def _extract_windows_dlls(archive: Path, destination: Path) -> dict:
        """Extract regular DLLs beside the server from the verified Windows ZIP."""
        hashes = {}
        seen = set()
        total = 0
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                name = entry.filename.replace('\\', '/').rsplit('/', 1)[-1]
                if not name.lower().endswith('.dll'):
                    continue
                mode = (entry.external_attr >> 16) & 0o170000
                if (not re.fullmatch(r'[A-Za-z0-9_.+-]+\.dll', name, re.I)
                        or name.lower() in seen or entry.is_dir()
                        or mode not in (0, stat.S_IFREG)
                        or entry.file_size <= 0 or entry.file_size > MAX_BINARY):
                    raise KiwixRuntimeError('kiwix_archive_unsafe_member')
                seen.add(name.lower())
                total += entry.file_size
                if total > 256 * 1024 * 1024 or len(seen) > 128:
                    raise KiwixRuntimeError('kiwix_archive_invalid')
                target = destination / name
                with z.open(entry) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if target.stat().st_size != entry.file_size:
                    raise KiwixRuntimeError('kiwix_archive_invalid')
                hashes[name] = _sha256_file(target)
        if not hashes:
            raise KiwixRuntimeError('kiwix_archive_invalid')
        return hashes

    def install(self, progress=None):
        with self._lock:
            self.prepare_data()
            if self.binary_installed():
                return
            if self._process is not None and self._process.poll() is None:
                raise KiwixRuntimeError('kiwix_service_must_be_stopped')
            filename = platform_asset()
            self.code_dir.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.kiwix-stage-', dir=self.code_dir.parent) as temp:
                stage = Path(temp)
                archive = self._archive_and_digest(stage, filename, progress)
                executable = stage / self.bin_name
                self._extract_binary(archive, executable, self.bin_name)
                record = {'version': asset_version(filename), 'asset': filename,
                          'binary_sha256': _sha256_file(executable),
                          'archive_sha256': _sha256_file(archive),
                          'source': PUBLISHER + filename,
                          'publisher_checksum': 'MD5 over HTTPS (NOT cryptographically signed)'}
                if filename.endswith('.zip'):
                    record['dll_sha256'] = self._extract_windows_dlls(archive, stage)
                (stage / 'install.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
                if self.code_dir.is_symlink():
                    raise KiwixRuntimeError('kiwix_unsafe_install_destination')
                self.code_dir.mkdir(parents=True, exist_ok=True)
                # Publish executable, DLLs and manifest as one rollback-capable set.
                names = [self.bin_name, *record.get('dll_sha256', {}), 'install.json']
                backup = stage / 'previous'
                backup.mkdir()
                saved, published = [], []
                try:
                    for name in names:
                        target = self.code_dir / name
                        if target.is_symlink() or (target.exists() and not target.is_file()):
                            raise KiwixRuntimeError('kiwix_unsafe_install_destination')
                        if target.exists():
                            os.replace(target, backup / name)
                            saved.append(name)
                    for name in names:
                        os.replace(stage / name, self.code_dir / name)
                        published.append(name)
                except Exception:
                    for name in reversed(published):
                        (self.code_dir / name).unlink(missing_ok=True)
                    for name in reversed(saved):
                        os.replace(backup / name, self.code_dir / name)
                    raise

    @staticmethod
    def _http_ready(host: str, port: int) -> bool:
        """An HTTP response (including 404) proves the listener speaks HTTP."""
        host = '127.0.0.1' if host in ('0.0.0.0', 'localhost') else host
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f'http://{host}:{port}/', timeout=0.45) as response:
                return response.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except (OSError, urllib.error.URLError):
            return False

    def _signature(self, paths):
        return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)

    def ensure_running(self, server_url: str) -> bool:
        with self._lock:
            if not self.binary_installed():
                raise KiwixRuntimeError('kiwix_runtime_missing')
            parts=urlsplit(server_url)
            if parts.scheme != 'http' or not parts.hostname or not parts.port or parts.path not in ('', '/'):
                raise KiwixRuntimeError('kiwix_managed_url_invalid')
            if parts.hostname not in ('127.0.0.1','localhost','0.0.0.0'):
                # Legacy external Kiwix server (dev16 configuration): keep working
                # without launching an additional local process on another host.
                self.stop()
                return True
            zims = self._zims()
            self.prepare_data()
            signature = self._signature(zims)
            live = self._process is not None and self._process.poll() is None
            if live and signature == self._zim_signature and server_url == self._bound_url:
                return True
            if live:
                self.stop()
            self._zim_signature = signature
            self._bound_url = server_url
            if not zims:
                return False
            parts = urlsplit(server_url)
            if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '0.0.0.0') or not parts.port or parts.path not in ('', '/'):
                raise KiwixRuntimeError('kiwix_managed_url_must_be_local_http')
            host = parts.hostname
            port = parts.port
            if self._http_ready(host, port):
                self._last_error = 'kiwix_port_already_in_use'
                raise KiwixRuntimeError(self._last_error)
            logpath = self.code_dir / 'kiwix.log'
            with logpath.open('ab') as logfile:
                try:
                    self._process = subprocess.Popen(
                        # Kiwix Tools 3.8.2 requires explicit ZIM file paths: it
                        # treats a directory as one invalid ZIM, unlike newer
                        # releases that can scan directories automatically.
                        # `zims` is filtered by _zims(): regular .zim files only,
                        # without symlinks, partial downloads or sidecar files.
                        [str(self.binary), '--address='+host, '--port='+str(port),
                         '--urlRootLocation=/kiwix',
                         '--attachToProcess='+str(os.getpid()), *(str(path) for path in zims)],
                        cwd=str(self.code_dir), stdout=logfile, stderr=subprocess.STDOUT,
                        close_fds=True, start_new_session=(os.name != 'nt'))
                except OSError as exc:
                    self._last_error = 'kiwix_service_start_failed'
                    raise KiwixRuntimeError(self._last_error) from exc
            # A spawned PID is not proof that Kiwix is accepting HTTP requests.
            # Poll for readiness instead of assuming 150 ms is sufficient.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    break
                if self._http_ready(host, port):
                    self._last_error = ''
                    return True
                time.sleep(0.2)
            self._last_error = 'kiwix_service_start_failed'
            self.stop()  # preserve the ZIM files and the diagnostic process log
            raise KiwixRuntimeError(self._last_error)

    def stop(self):
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._process = None
            self._bound_url = ''

    def delete_zim(self, filename: str, server_url: str) -> dict:
        """Delete ONE confirmed local ZIM; preserve every other file and all code.

        The managed reader must be stopped before unlinking (required on
        Windows), then restarted with the remaining ZIMs. No shell, recursive
        directory removal, wildcard, symlink or partial-download deletion.
        """
        if (not isinstance(filename, str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,190}\.zim', filename) or
                '..' in filename):
            raise KiwixRuntimeError('kiwix_zim_filename_invalid')
        with self._lock:
            parts = urlsplit(server_url)
            if (parts.scheme != 'http' or parts.hostname not in
                    ('127.0.0.1', 'localhost', '0.0.0.0') or
                    not parts.port or parts.path not in ('', '/')):
                # Never delete files from the PHAROS data tree when a separate
                # externally managed Kiwix server has been configured.
                raise KiwixRuntimeError('kiwix_managed_service_required')
            self._require_storage()
            matches = [path for path in self._zims() if path.name == filename]
            if not matches:
                raise KiwixRuntimeError('kiwix_zim_not_found')
            target=matches[0]
            # Revalidate immediately before removing; a symlink, broken link,
            # directory, or changed storage mount must never be deleted.
            if target.is_symlink() or not target.is_file():
                raise KiwixRuntimeError('kiwix_zim_not_found')
            was_live=self._process is not None and self._process.poll() is None
            if was_live:
                self.stop()
            try:
                self._require_storage()
                if target.is_symlink() or not target.is_file():
                    raise KiwixRuntimeError('kiwix_zim_not_found')
                target.unlink()
            except (OSError, KiwixRuntimeError) as exc:
                if was_live:
                    try:
                        self.ensure_running(server_url)
                    except KiwixRuntimeError:
                        pass
                if isinstance(exc, KiwixRuntimeError):
                    raise
                raise KiwixRuntimeError('kiwix_zim_delete_failed') from exc
            # A deleted file cannot be restored by the UI. If restarting the
            # service fails, report the failure separately but keep the user
            # informed that deletion itself succeeded.
            restart_error=''
            if was_live and self.binary_installed():
                try:
                    self.ensure_running(server_url)
                except KiwixRuntimeError as exc:
                    restart_error=str(exc)
            return {'deleted': filename, 'restart_error':restart_error}

    def uninstall(self):
        with self._lock:
            self.stop()
            # Only managed code; NEVER touch config.storage.data_dir/kiwix.
            self.binary.unlink(missing_ok=True)
            self.manifest.unlink(missing_ok=True)
            # Preserve diagnostic log, and never traverse or delete the data tree.

    def status(self):
        with self._lock:
            installed = self.binary_installed()
            active = self._process is not None and self._process.poll() is None
            return {'installed': installed, 'running': active,
                    'waiting_for_zim': bool(installed and not active and not self._zims()),
                    'zim_dir': str(self.zim_dir), 'error': self._last_error}


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as src:
        while block := src.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()
