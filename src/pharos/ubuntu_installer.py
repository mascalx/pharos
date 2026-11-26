"""PHAROS Ubuntu x86-64 setup: conservative, rootless, and repeatable.

Third-party artifacts are downloaded ONLY from the publishers on setup, not bundled
with PHAROS. Configuration, original documents and indexes survive upgrades.
This CLI is intentionally Ubuntu-only until other platforms have real tests.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

import tomlkit

from .config import load_config
from .kiwix_runtime import KiwixManagedService, _PublisherRedirects
from .storage import MARKER_FILE, check_data_volume, initialize_volume

QDRANT_TAG = 'v1.18.3'  # Pinned to avoid silent index/storage migrations.
QDRANT_ASSET = 'qdrant-x86_64-unknown-linux-gnu.tar.gz'
QDRANT_SOURCE = f'https://github.com/qdrant/qdrant/releases/download/{QDRANT_TAG}/{QDRANT_ASSET}'
QDRANT_API = f'https://api.github.com/repos/qdrant/qdrant/releases/tags/{QDRANT_TAG}'
# Independently pinned upstream release artifact digest (also validated against
# GitHub's asset digest when present); not a publisher cryptographic signature.
QDRANT_SHA256 = '60663a254cf421dba4db45710872895cd3a714fe1e6978f7927923b5cfae4718'
MAX_RELEASE = 240 * 1024 * 1024
MAX_BINARY = 240 * 1024 * 1024


class InstallError(RuntimeError):
    pass


def require_platform(system: str | None = None, machine: str | None = None, os_release: str | None = None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    release = os_release if os_release is not None else Path('/etc/os-release').read_text(encoding='utf-8')
    if system != 'linux' or machine not in ('x86_64', 'amd64') or not re.search(r'(?m)^ID=ubuntu\s*$', release):
        raise InstallError('Installer disponibile solo per Ubuntu Linux x86-64 (altre piattaforme non collaudate)')
    if sys.version_info < (3, 11):
        raise InstallError('È necessario Python 3.11 o superiore')
    if getattr(os, 'geteuid', lambda: -1)() == 0:
        raise InstallError('Non eseguire PHAROS come root: usare il proprio utente')


def sha256_file(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open('rb') as src:
        for data in iter(lambda: src.read(1024 * 1024), b''):
            dig.update(data)
    return dig.hexdigest()


def _opener():
    # Ignore shell proxy settings; HTTPS redirects must target public addresses.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())


def release_asset_digest(opener=None) -> str:
    """Validate release identity and asset URL using official GitHub metadata."""
    opener = opener or _opener()
    request = urllib.request.Request(QDRANT_API, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'PHAROS-Ubuntu-Installer/0.6',
    })
    with opener.open(request, timeout=35) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise InstallError('Metadati Qdrant troppo grandi')
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise InstallError('Metadati ufficiali Qdrant non validi') from exc
    if data.get('tag_name') != QDRANT_TAG:
        raise InstallError('Versione Qdrant non corrispondente')
    assets = [x for x in data.get('assets', []) if x.get('name') == QDRANT_ASSET]
    if len(assets) != 1 or assets[0].get('browser_download_url') != QDRANT_SOURCE:
        raise InstallError('Artefatto Qdrant non pubblicato dalla release ufficiale prevista')
    digest = assets[0].get('digest')
    if digest is not None:
        if digest != 'sha256:' + QDRANT_SHA256:
            raise InstallError('Digest della release Qdrant diverso dal valore verificato')
    return QDRANT_SHA256


def download_qdrant(target: Path, opener=None):
    opener = opener or _opener()
    expected = release_asset_digest(opener)
    req = urllib.request.Request(QDRANT_SOURCE, headers={'User-Agent': 'PHAROS-Ubuntu-Installer/0.6'})
    size = 0
    with opener.open(req, timeout=70) as src, target.open('xb') as out:
        while block := src.read(1024 * 1024):
            size += len(block)
            if size > MAX_RELEASE:
                raise InstallError('Archivio Qdrant oltre il limite consentito')
            out.write(block)
    if sha256_file(target) != expected:
        raise InstallError('Checksum SHA-256 Qdrant non corrispondente')


def extract_qdrant(archive: Path, destination: Path):
    """Extract a single exact regular file; reject links and traversal."""
    with tarfile.open(archive, 'r:gz') as tf:
        matches = [m for m in tf.getmembers() if m.name == 'qdrant']
        if len(matches) != 1 or not matches[0].isreg() or not (0 < matches[0].size <= MAX_BINARY):
            raise InstallError('Archivio Qdrant non contiene un eseguibile regolare')
        entry = tf.extractfile(matches[0])
        if entry is None:
            raise InstallError('Impossibile leggere eseguibile Qdrant')
        with entry, destination.open('xb') as out:
            size = 0
            while block := entry.read(1024 * 1024):
                size += len(block)
                if size > MAX_BINARY:
                    raise InstallError('Binario Qdrant troppo grande')
                out.write(block)
            if size != matches[0].size:
                raise InstallError('Binario Qdrant troncato')
    destination.chmod(0o755)


class QdrantInstaller:
    def __init__(self, directory: Path):
        self.directory = directory
        self.binary = directory / 'qdrant'
        self.manifest = directory / 'install.json'

    def installed(self) -> bool:
        try:
            record = json.loads(self.manifest.read_text(encoding='utf-8'))
            return (not self.binary.is_symlink() and self.binary.is_file() and
                    record.get('version') == QDRANT_TAG and
                    record.get('source') == QDRANT_SOURCE and
                    re.fullmatch('[a-f0-9]{64}', record.get('binary_sha256', '')) is not None and
                    sha256_file(self.binary) == record['binary_sha256'])
        except (OSError, ValueError, TypeError):
            return False

    def install(self):
        if self.directory.is_symlink():
            raise InstallError('Directory Qdrant non sicura (symlink)')
        if self.installed():
            return False
        if self.binary.exists() or self.manifest.exists():
            raise InstallError('Qdrant presente ma non riconosciuto: nessuna sovrascrittura automatica')
        self.directory.mkdir(parents=True, exist_ok=True)
        # Never write into the final directory until download and checks succeed.
        with tempfile.TemporaryDirectory(prefix='.qdrant-', dir=self.directory) as tmp:
            stage = Path(tmp)
            archive = stage / QDRANT_ASSET
            download_qdrant(archive)
            binary = stage / 'qdrant'
            extract_qdrant(archive, binary)
            manifest = stage / 'install.json'
            manifest.write_text(json.dumps({
                'version': QDRANT_TAG, 'source': QDRANT_SOURCE,
                'archive_sha256': sha256_file(archive),
                'binary_sha256': sha256_file(binary),
                'verification': 'pinned SHA256 and official GitHub release metadata (not a publisher signature)',
            }, indent=2) + '\n', encoding='utf-8')
            os.replace(binary, self.binary)
            try:
                os.replace(manifest, self.manifest)
            except Exception:
                self.binary.unlink(missing_ok=True)  # never a pre-existing binary
                raise
        return True


def _atomic_new_file(path: Path, content: str, mode: int = 0o600) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return True


def config_template(config_file: Path, data_dir: Path, database_dir: Path, cache_dir: Path,
                    ai_url: str, provider: str, embedding_model: str, language: str,
                    host: str, port: int, require_mount: bool) -> bool:
    from importlib.resources import files
    doc = tomlkit.parse(files('pharos').joinpath('config.example.toml').read_text(encoding='utf-8'))
    doc['general']['language'] = language
    doc['server']['host'] = host
    doc['server']['port'] = port
    doc['security']['secrets_file'] = str(config_file.parent / 'secrets.toml')
    doc['storage']['data_dir'] = str(data_dir)
    doc['storage']['database_dir'] = str(database_dir)
    doc['storage']['cache_dir'] = str(cache_dir)
    doc['storage']['marker_id'] = str(uuid4())
    doc['storage']['require_marker'] = True
    doc['storage']['require_mount'] = require_mount
    doc['ai']['provider'] = provider
    doc['ai']['base_url'] = ai_url or 'http://127.0.0.1'
    doc['ai']['enabled'] = bool(ai_url)
    # An embedding model must pass a real embeddings probe in PHAROS before enabling RAG.
    doc['rag']['enabled'] = False
    doc['rag']['embedding_model'] = embedding_model
    doc['rag']['qdrant_url'] = 'http://127.0.0.1:6333'
    doc['kiwix']['server_url'] = 'http://127.0.0.1:8081'
    doc['kiwix']['browser_url'] = ''
    return _atomic_new_file(config_file, tomlkit.dumps(doc))


def yaml_config(path: Path, data: Path, snapshots: Path) -> bool:
    # Qdrant accepts absolute YAML paths. Local host binding is required.
    for directory in (data, snapshots):
        if directory.is_symlink():
            raise InstallError('Storage Qdrant non può essere un symlink')
        directory.mkdir(parents=True, exist_ok=True)
    body = ('log_level: INFO\n'
            'storage:\n'
            f'  storage_path: {json.dumps(str(data))}\n'
            f'  snapshots_path: {json.dumps(str(snapshots))}\n'
            'service:\n'
            '  host: 127.0.0.1\n'
            '  http_port: 6333\n'
            '  grpc_port: null\n'
            '  enable_cors: false\n'
            'telemetry_disabled: true\n')
    created = _atomic_new_file(path, body)
    if not created and path.read_text(encoding='utf-8') != body:
        raise InstallError('qdrant.yaml esistente non corrisponde allo storage: non verrà sovrascritto')
    return created


def _is_network_storage(path: Path) -> bool:
    """Detect NFS/SMB/SSHFS for operational database storage on Linux."""
    path = path.resolve()
    matches = []
    for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
        if ' - ' not in line:
            continue
        before, after = line.split(' - ', 1)
        fields, post = before.split(), after.split()
        if len(fields) < 5 or not post:
            continue
        mount = Path(fields[4].replace('\\040', ' ')).resolve()
        if path == mount or mount in path.parents:
            matches.append((len(str(mount)), post[0]))
    if not matches:
        raise InstallError('Impossibile determinare il filesystem dei database')
    return max(matches)[1] in {'nfs', 'nfs4', 'cifs', 'smbfs', 'fuse.sshfs', 'sshfs'}


def systemd_escape(value: str) -> str:
    # % is a systemd specifier, \ is an escape and quotes bound one ExecStart argument.
    if '\n' in value or '\r' in value or '\0' in value:
        raise InstallError('Percorso non valido per systemd')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit_content(name: str, command: list[str], *, qdrant: bool) -> str:
    exec_line = ' '.join(systemd_escape(x) for x in command)
    if qdrant:
        return (f'[Unit]\nDescription=PHAROS managed Qdrant\n\n'
                f'[Service]\nType=simple\nExecStart={exec_line}\nRestart=on-failure\n'
                f'RestartSec=3\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n')
    return (f'[Unit]\nDescription=PHAROS integrated web service\n'
            f'After=pharos-qdrant.service\nRequires=pharos-qdrant.service\n\n'
            f'[Service]\nType=simple\nExecStart={exec_line}\nRestart=on-failure\n'
            f'RestartSec=4\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n')


def _ensure_unit(path: Path, contents: str):
    if path.exists() and path.read_text(encoding='utf-8') != contents:
        if path.is_symlink() or 'PHAROS' not in path.read_text(encoding='utf-8'):
            raise InstallError('Servizio systemd esistente non gestito da PHAROS')
        # Atomic replacement only of PHAROS-owned unit content, no changes to data.
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.pharos-unit-', delete=False, encoding='utf-8') as f:
            staged = Path(f.name)
            f.write(contents)
        os.replace(staged, path)
    elif not path.exists():
        _atomic_new_file(path, contents, mode=0o644)


def _run_systemctl(*argv: str):
    result = subprocess.run(['systemctl', '--user', *argv], text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise InstallError(f'systemctl --user {" ".join(argv)}: {(result.stderr or result.stdout).strip()[:600]}')
    return result.stdout


def _http_ready(url: str, timeout: int = 30) -> bool:
    end = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < end:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.7)
    return False


def setup(args, *, check_platform: bool = True) -> dict:
    if check_platform:
        require_platform()
    home = Path.home()
    config_path = Path(args.config).expanduser().resolve() if args.config else home / '.config/pharos/config.toml'
    default_db = home / '.local/share/pharos/db'
    default_cache = home / '.cache/pharos'
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else home / 'PHAROS-data'
    if args.ai_url and urlsplit(args.ai_url).scheme not in ('http', 'https'):
        raise InstallError('Indirizzo IA non valido: usare http(s)')
    if args.provider not in ('tiiny_direct', 'tiiny_gateway', 'openai_compatible'):
        raise InstallError('Provider IA non valido')
    if args.language not in ('it', 'en'):
        raise InstallError('Lingua non supportata')
    if not (1024 <= args.port <= 65535) or args.host not in ('0.0.0.0', '127.0.0.1'):
        raise InstallError('Host/porta non supportati')
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing_install = config_path.is_file()
    old_config_hash = sha256_file(config_path) if existing_install else None
    if not config_path.exists():
        if not data_dir.exists():
            if args.require_mount:
                raise InstallError('Montare la directory NFS prima del setup: ' + str(data_dir))
            data_dir.mkdir(parents=True)
        # Fail closed on folders reused from another installation.
        if (data_dir / MARKER_FILE).exists():
            raise InstallError('Volume PHAROS già inizializzato: utilizzare il config.toml originale')
        if any(data_dir.iterdir()):
            raise InstallError('Directory dati non vuota: prima recuperare la configurazione originale')
        config_template(config_path, data_dir, default_db, default_cache, args.ai_url,
                        args.provider, args.embedding_model, args.language, args.host,
                        args.port, args.require_mount)
    config = load_config(config_path)
    if args.data_dir and config.storage.data_dir != data_dir:
        raise InstallError('Percorso dati diverso dalla configurazione esistente: nessuna migrazione automatica')
    if args.require_mount and not config.storage.require_mount:
        raise InstallError('require_mount non attivo nel TOML esistente: non sarà modificato automaticamente')
    if not config.storage.data_dir.is_dir():
        raise InstallError('Directory dati assente; controllare il mount prima di procedere')
    if not (config.storage.data_dir / MARKER_FILE).exists():
        initialize_volume(config.storage, str(config.storage.data_dir))
    status = check_data_volume(config.storage)
    if not status.available:
        raise InstallError('Volume dati non disponibile: ' + status.reason)
    if _is_network_storage(config.storage.database_dir):
        raise InstallError('Database operativi su filesystem di rete: usare un disco locale')
    config.storage.database_dir.mkdir(parents=True, exist_ok=True)
    config.storage.cache_dir.mkdir(parents=True, exist_ok=True)
    qdrant = QdrantInstaller(config.storage.database_dir / 'pharos-managed-services/qdrant')
    qdrant.install()
    qdrant_cfg = config_path.parent / 'qdrant.yaml'
    yaml_config(qdrant_cfg, config.storage.database_dir / 'qdrant', config.storage.database_dir / 'qdrant-snapshots')
    kiwix = KiwixManagedService(config)
    kiwix.install()
    venv_bin = Path(sys.executable).absolute().parent
    pharos_cli = venv_bin / 'pharos'
    if not pharos_cli.is_file():
        raise InstallError('CLI PHAROS non installata nello stesso ambiente Python')
    systemd_dir = home / '.config/systemd/user'
    systemd_dir.mkdir(parents=True, exist_ok=True)
    _ensure_unit(systemd_dir / 'pharos-qdrant.service', unit_content('qdrant',
                 [str(qdrant.binary), '--config-path', str(qdrant_cfg)], qdrant=True))
    _ensure_unit(systemd_dir / 'pharos.service', unit_content('pharos',
                 [str(pharos_cli), 'serve', '--config', str(config_path)], qdrant=False))
    if not args.no_start:
        _run_systemctl('daemon-reload')
        _run_systemctl('enable', '--now', 'pharos-qdrant.service')
        _run_systemctl('is-active', '--quiet', 'pharos-qdrant.service')
        if not _http_ready('http://127.0.0.1:6333/readyz'):
            raise InstallError('Qdrant non pronto: verificare journalctl --user -u pharos-qdrant')
        _run_systemctl('enable', 'pharos.service')
        _run_systemctl('restart', 'pharos.service')
        _run_systemctl('is-active', '--quiet', 'pharos.service')
        if not _http_ready(f'http://127.0.0.1:{config.server.port}/healthz'):
            raise InstallError('PHAROS non pronto: verificare journalctl --user -u pharos')
    if old_config_hash is not None and sha256_file(config_path) != old_config_hash:
        raise InstallError('Il TOML è cambiato durante l’aggiornamento: verificare il backup, nessun ripristino distruttivo automatico')
    return {'mode': 'update' if existing_install else 'install',
            'config_preserved': old_config_hash is not None,
            'config': str(config_path), 'data': str(config.storage.data_dir),
            'qdrant': str(qdrant.binary), 'kiwix': str(kiwix.binary),
            'url': f'http://<IP-UBUNTU>:{config.server.port}',
            'services_started': not args.no_start,
            'reboot_note': 'Controllare loginctl show-user "$USER" -p Linger; senza lingering i servizi utente potrebbero non avviarsi prima del login.'}


def parser():
    p = argparse.ArgumentParser(description='PHAROS: installazione integrata per Ubuntu x86-64')
    p.add_argument('--config', help='TOML principale (predefinito: ~/.config/pharos/config.toml)')
    p.add_argument('--data-dir', help='Directory documenti/ZIM, locale o montata dal NAS')
    p.add_argument('--require-mount', action='store_true', help='Richiedere che data-dir sia un mount point')
    p.add_argument('--provider', default='tiiny_direct', choices=('tiiny_direct','tiiny_gateway','openai_compatible'))
    p.add_argument('--ai-url', default='', help='URL base del provider IA, es. http://192.168.1.123')
    p.add_argument('--embedding-model', default='', help='ID embedding da registrare; abilitare RAG solo dopo verifica reale in PHAROS')
    p.add_argument('--language', default='it', choices=('it','en'))
    p.add_argument('--host', default='0.0.0.0', choices=('0.0.0.0', '127.0.0.1'))
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--no-start', action='store_true', help='Installa e prepara servizi senza avviarli (test)')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = setup(args)
    except (InstallError, OSError, ValueError, urllib.error.URLError) as exc:
        print(f'PHAROS SETUP ERROR: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
