#!/usr/bin/env python3
"""Offline, non-destructive full PHAROS backup, verification and empty-target restore.

Stop pharos and pharos-qdrant before backup or restore. No network, sudo,
Docker, external dependency or automatic deletion of production datasets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

SCHEMA = 1


def ensure_services_stopped() -> None:
    for service in ('pharos.service', 'pharos-qdrant.service'):
        result = subprocess.run(['systemctl', '--user', 'show', '--property=ActiveState',
                                 '--value', service], text=True, capture_output=True,
                                timeout=12, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Impossibile verificare lo stato di {service}: {result.stderr.strip()}")
        state = result.stdout.strip()
        if state not in ('inactive', 'failed'):
            raise RuntimeError(f"{service} non arrestato (stato {state!r}). Fermare i servizi prima del backup.")


def require_network_mount(folder: Path) -> None:
    """Avoid restoring a large NAS dataset into an empty local mount point."""
    mountinfo = Path('/proc/mounts')
    if not mountinfo.exists():
        raise RuntimeError('Impossibile verificare il montaggio di rete')
    candidates = []
    for line in mountinfo.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mountpath = Path(parts[1].replace('\\040', ' ')).resolve(strict=False)
            if folder == mountpath or mountpath in folder.parents:
                candidates.append((len(mountpath.parts), parts[2]))
    if not candidates or max(candidates)[1].lower() not in ('nfs', 'nfs4', 'cifs', 'smb3', 'fuse.sshfs'):
        raise RuntimeError(f'Storage NAS non montato come filesystem di rete: {folder}')


def read_layout(config_file: Path) -> dict[str, Path]:
    config_file = config_file.expanduser().resolve(strict=True)
    with config_file.open('rb') as file:
        cfg = tomllib.load(file)
    storage = cfg.get('storage', {})
    layout = {'configuration': config_file.parent}
    for group, key in [('database', 'database_dir'), ('data', 'data_dir'), ('cache', 'cache_dir')]:
        value = storage.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Percorso mancante nel TOML: storage.' + key)
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = config_file.parent / path
        layout[group] = path.resolve(strict=True)
    if storage.get('require_mount') is True:
        require_network_mount(layout['data'])
    if any(not path.is_dir() for path in layout.values()):
        raise ValueError('Directory PHAROS mancante')
    paths = list(layout.values())
    for index, one in enumerate(paths):
        for other in paths[index+1:]:
            if one == other or one in other.parents or other in one.parents:
                raise ValueError('Directory di configurazione/database/dati/cache sovrapposte')
    secrets = cfg.get('security', {}).get('secrets_file')
    if secrets:
        candidate = Path(secrets).expanduser()
        if not candidate.is_absolute():
            candidate = config_file.parent / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.is_file() and candidate.parent != config_file.parent:
            layout['external_secrets'] = candidate
    return layout


def inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def safe_destination(destination: Path, layout: dict[str, Path]) -> Path:
    destination = destination.expanduser().resolve(strict=False)
    for path in layout.values():
        resolved = path.resolve()
        if inside(destination, resolved) or inside(resolved, destination):
            raise ValueError('Il backup deve stare su un percorso indipendente da tutti gli storage PHAROS')
    if destination.exists():
        raise ValueError('La directory del backup esiste già; non verrà sovrascritta')
    return destination


def clean_walk(root: Path):
    if root.is_symlink():
        raise ValueError(f'Link simbolico non consentito nel backup: {root}')
    if root.is_file():
        if not stat.S_ISREG(root.stat().st_mode):
            raise ValueError(f'File non regolare: {root}')
        yield Path(root.name), root
        return
    for current, directories, filenames in os.walk(root, followlinks=False):
        for entry in directories + filenames:
            p = Path(current) / entry
            if p.is_symlink():
                raise ValueError(f'Link simbolico non consentito nel backup: {p}')
            if not (p.is_dir() or p.is_file()):
                raise ValueError(f'File speciale non consentito: {p}')
        for filename in filenames:
            source = Path(current) / filename
            if not stat.S_ISREG(source.stat().st_mode):
                raise ValueError(f'File non regolare: {source}')
            yield source.relative_to(root), source


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while chunk := file.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(config: Path, destination: Path, check_services: bool = True) -> Path:
    layout = read_layout(config)
    destination = safe_destination(destination, layout)
    if check_services:
        ensure_services_stopped()
    if not destination.parent.is_dir():
        raise ValueError('La directory padre del backup deve già esistere')
    os.umask(0o077)
    staging = Path(tempfile.mkdtemp(prefix='.pharos-backup-', dir=destination.parent))
    try:
        manifest = {'schema': SCHEMA, 'created_utc': int(time.time()),
                    'paths': {name: str(path) for name, path in layout.items()}, 'files': []}
        total_bytes = 0
        for group, source in layout.items():
            print(f'Backup PHAROS: {group} — {source}', flush=True)
            target = staging / group
            if source.is_file():
                target.mkdir(mode=0o700)
                target = target / source.name
                shutil.copy2(source, target)
                manifest['files'].append({'path': f'{group}/{source.name}',
                    'size': target.stat().st_size, 'sha256': file_hash(target)})
            else:
                target.mkdir(mode=0o700)
                for relative, original in clean_walk(source):
                    dest = target / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, dest)
                    copied = dest.stat().st_size
                    previous = total_bytes // (1024 * 1024 * 1024)
                    total_bytes += copied
                    if total_bytes // (1024 * 1024 * 1024) > previous:
                        print(f'  Copiati {total_bytes // (1024 * 1024 * 1024)} GiB...', flush=True)
                    manifest['files'].append({'path': f'{group}/{relative.as_posix()}',
                        'size': dest.stat().st_size, 'sha256': file_hash(dest)})
        # User systemd files are included as reference material, not
        # automatically restored or enabled on a fresh installation.
        units = Path.home() / '.config/systemd/user'
        for unit in ('pharos.service', 'pharos-qdrant.service'):
            source = units / unit
            if source.is_file() and not source.is_symlink():
                dest = staging / 'services' / unit
                dest.parent.mkdir(mode=0o700, exist_ok=True)
                shutil.copy2(source, dest)
                manifest['files'].append({'path': f'services/{unit}',
                    'size': dest.stat().st_size, 'sha256': file_hash(dest)})
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
        verify_backup(staging)
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging)
        raise


def verify_backup(folder: Path) -> dict:
    folder = folder.expanduser().resolve(strict=True)
    path = folder / 'manifest.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('Manifest assente o non sicuro')
    manifest = json.loads(path.read_text(encoding='utf8'))
    if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), list):
        raise ValueError('Schema backup non compatibile')
    allowed = {'configuration', 'database', 'data', 'cache', 'external_secrets'}
    paths = manifest.get('paths')
    if (not isinstance(paths, dict) or
            not {'configuration', 'database', 'data', 'cache'}.issubset(paths) or
            set(paths) - allowed or
            any(not isinstance(value, str) or not Path(value).is_absolute()
                for value in paths.values())):
        raise ValueError('Percorsi originari del backup non validi')
    seen = set()
    for entry in manifest['files']:
        name = entry['path']
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or
                not relative.parts or relative.parts[0] not in allowed | {'services'} or
                name in seen):
            raise ValueError('Percorso manifest non valido o duplicato')
        seen.add(name)
        target = folder / relative
        if target.is_symlink() or not target.is_file() or target.stat().st_size != entry['size']:
            raise ValueError('File backup mancante o alterato: ' + name)
        if file_hash(target) != entry['sha256']:
            raise ValueError('Digest SHA-256 non valido: ' + name)
    stored = {str(path.relative_to(folder)) for _, path in clean_walk(folder) if path != folder / 'manifest.json'}
    if stored != seen:
        raise ValueError('Il backup contiene file non elencati o mancanti')
    return manifest


def restore_empty(backup: Path, check_services: bool = True) -> None:
    # Explicitly not an in-place overwrite or destructive rollback.
    manifest = verify_backup(backup)
    if check_services:
        ensure_services_stopped()
    paths = {name: Path(value).expanduser() for name, value in manifest['paths'].items()}
    backup = backup.expanduser().resolve(strict=True)
    config_copy = backup / 'configuration' / 'config.toml'
    if config_copy.exists():
        with config_copy.open('rb') as stream:
            config_data = tomllib.load(stream)
        if config_data.get('storage', {}).get('require_mount') is True:
            require_network_mount(paths['data'].resolve(strict=False))
    for group, target in paths.items():
        if group == 'external_secrets':
            if target.exists() or not target.parent.is_dir():
                raise ValueError(f'Non posso ripristinare su file già esistente: {target}')
        elif target.exists() and (not target.is_dir() or target.is_symlink() or any(target.iterdir())):
            raise ValueError(f'Ripristino consentito soltanto in cartelle vuote: {target}')
        if inside(backup, target.resolve(strict=False)) or inside(target.resolve(strict=False), backup):
            raise ValueError('Sorgente e destinazione sovrapposte')
    for group, target in paths.items():
        source = backup / group
        if group == 'external_secrets':
            shutil.copy2(source / target.name, target)
        else:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
    # Service definitions need verification/reinstallation by the installer.


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('backup', 'verify', 'restore-empty'))
    parser.add_argument('destination', type=Path)
    parser.add_argument('--config', type=Path,
                        default=Path.home() / '.config/pharos/config.toml')
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            print('Backup completo:', create_backup(args.config, args.destination))
        elif args.command == 'verify':
            data = verify_backup(args.destination)
            print('Verifica SHA-256 riuscita:', len(data['files']), 'file')
        else:
            restore_empty(args.destination)
            print('Ripristino in cartelle vuote completato; reinstallare i servizi e verificare i dati.')
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print('ERRORE:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
