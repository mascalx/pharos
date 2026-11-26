"""Ubuntu HTTP :80 entrypoint without a fixed hostname.

A dedicated VM can be reached by its LAN IP or by any DNS/mDNS name.
The only managed configuration is the PHAROS Nginx vhost. Never replace
third-party sites or nonstandard Nginx default configurations.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SITE_PATH = Path('/etc/nginx/sites-available/pharos')
ENABLED_PATH = Path('/etc/nginx/sites-enabled/pharos')
DEFAULT_AVAILABLE = Path('/etc/nginx/sites-available/default')
DEFAULT_ENABLED = Path('/etc/nginx/sites-enabled/default')
MARKER = '# PHAROS MANAGED NGINX VHOST — do not modify other sites'


class LANProxyError(RuntimeError):
    pass


def nginx_site(upstream_port: int) -> str:
    if type(upstream_port) is not int or not (1024 <= upstream_port <= 65535):
        raise LANProxyError('Porta PHAROS interna non valida')
    # The server name _ is not a DNS name and does not bind the installation to
    # an arbitrary hostname: default_server handles IP and any chosen DNS name.
    # Keep the original URI in proxy_pass (no slash) for /kiwix, /api, /static.
    return f'''{MARKER}
server {{
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;
    client_max_body_size 128m;
    location / {{
        proxy_pass http://127.0.0.1:{upstream_port};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 1800s;
        proxy_send_timeout 1800s;
        proxy_buffering off;
    }}
}}
'''


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), text=True, capture_output=True, timeout=600, check=check)


def _privileged(*cmd: str):
    result = _run('sudo', *cmd, check=False)
    if result.returncode != 0:
        raise LANProxyError((result.stderr or result.stdout or 'sudo fallito').strip()[:500])
    return result


def _port_occupied() -> bool:
    result = _run('ss', '-H', '-ltn', '( sport = :80 )', check=False)
    if result.returncode:
        raise LANProxyError('Impossibile verificare la porta 80 (ss non disponibile)')
    return bool(result.stdout.strip())


def _nginx_running() -> bool:
    return _run('systemctl', 'is-active', '--quiet', 'nginx', check=False).returncode == 0


def _stock_default_enabled() -> bool:
    """Only Ubuntu's untouched example default site may be disabled automatically."""
    if not DEFAULT_ENABLED.is_symlink() or DEFAULT_ENABLED.resolve() != DEFAULT_AVAILABLE:
        return False
    try:
        body = DEFAULT_AVAILABLE.read_text(encoding='utf-8')
    except OSError:
        return False
    return ('Default server configuration' in body and
            'root /var/www/html;' in body and
            'server_name _;' in body and
            'listen 80 default_server;' in body)


def _check_other_sites():
    if ENABLED_PATH.is_symlink() and ENABLED_PATH.resolve() != SITE_PATH:
        raise LANProxyError('Il collegamento NGINX pharos non appartiene a PHAROS')
    if ENABLED_PATH.exists() and not ENABLED_PATH.is_symlink():
        raise LANProxyError('Il percorso NGINX enabled/pharos non è gestito da PHAROS')
    if DEFAULT_ENABLED.exists() or DEFAULT_ENABLED.is_symlink():
        if not _stock_default_enabled():
            raise LANProxyError('Sito NGINX predefinito personalizzato: non verrà modificato')
    if ENABLED_PATH.parent.is_dir():
        for site in ENABLED_PATH.parent.iterdir():
            if site.name not in {ENABLED_PATH.name, DEFAULT_ENABLED.name}:
                raise LANProxyError('Altri siti NGINX attivi: PHAROS non può assumere la porta 80 come default')


def configure_nginx(upstream_port: int) -> dict:
    if os.geteuid() == 0:
        raise LANProxyError('Eseguire come utente normale, senza sudo')
    config = nginx_site(upstream_port)
    if not shutil.which('sudo'):
        raise LANProxyError('sudo non disponibile; NGINX richiede autorizzazione iniziale')
    if SITE_PATH.is_symlink() or (SITE_PATH.exists() and
       not SITE_PATH.read_text(encoding='utf-8').startswith(MARKER)):
        raise LANProxyError('Il file NGINX pharos appartiene a un altro servizio: non verrà sovrascritto')
    _check_other_sites()  # refuse foreign sites before touching any system config
    if _port_occupied() and not _nginx_running():
        raise LANProxyError('La porta 80 è occupata da un servizio diverso da NGINX')

    old_contents = SITE_PATH.read_text(encoding='utf-8') if SITE_PATH.exists() else None
    if not shutil.which('nginx'):
        print('Installazione di NGINX dai repository ufficiali Ubuntu (richiede sudo).')
        _privileged('apt-get', 'update')
        _privileged('apt-get', 'install', '-y', 'nginx')
    if not shutil.which('nginx'):
        raise LANProxyError('NGINX non disponibile dopo il setup')
    _check_other_sites()  # apt may just have created Ubuntu's example default site
    disable_stock_default = _stock_default_enabled()
    changed = old_contents != config or not ENABLED_PATH.is_symlink() or disable_stock_default
    if changed:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='pharos-nginx-', delete=False) as f:
            staged = Path(f.name)
            f.write(config)
        removed_default = False
        created_link = False
        try:
            _privileged('install', '-d', '-m', '755', str(SITE_PATH.parent), str(ENABLED_PATH.parent))
            _privileged('install', '-m', '644', str(staged), str(SITE_PATH) + '.new')
            _privileged('mv', '-f', str(SITE_PATH) + '.new', str(SITE_PATH))
            if disable_stock_default:
                _privileged('rm', '--', str(DEFAULT_ENABLED))  # never delete available/default
                removed_default = True
            if not ENABLED_PATH.is_symlink():
                _privileged('ln', '-s', str(SITE_PATH), str(ENABLED_PATH))
                created_link = True
            _privileged('nginx', '-t')
            if _nginx_running():
                _privileged('systemctl', 'reload', 'nginx')
            else:
                _privileged('systemctl', 'enable', '--now', 'nginx')
        except LANProxyError:
            # Restore the previous site and previous default on setup failure.
            # Keep the original error: best-effort rollback may require manual repair.
            try:
                if old_contents is None:
                    _privileged('rm', '-f', '--', str(ENABLED_PATH), str(SITE_PATH))
                else:
                    with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='pharos-nginx-rollback-', delete=False) as back:
                        back.write(old_contents)
                        saved = Path(back.name)
                    try:
                        _privileged('install', '-m', '644', str(saved), str(SITE_PATH))
                    finally:
                        saved.unlink(missing_ok=True)
                    if created_link:
                        _privileged('rm', '-f', '--', str(ENABLED_PATH))
                if removed_default:
                    _privileged('ln', '-s', str(DEFAULT_AVAILABLE), str(DEFAULT_ENABLED))
            except LANProxyError as rollback_error:
                raise LANProxyError('Setup NGINX fallito e rollback incompleto: ' + str(rollback_error))
            raise
        finally:
            staged.unlink(missing_ok=True)
    elif not _nginx_running():
        _privileged('nginx', '-t')
        _privileged('systemctl', 'enable', '--now', 'nginx')
    user = os.environ.get('USER')
    if user and user == _run('id', '-un').stdout.strip():
        lingering = _run('loginctl', 'show-user', user, '-p', 'Linger', '--value', check=False)
        if lingering.returncode == 0 and lingering.stdout.strip() == 'no':
            _privileged('loginctl', 'enable-linger', user)
    return {'url_hint': 'http://<IP-o-DNS-della-VM>/', 'proxy': str(SITE_PATH),
            'upstream_port': upstream_port, 'changed': changed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Configura ingresso LAN PHAROS su NGINX porta 80')
    parser.add_argument('--upstream-port', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = configure_nginx(args.upstream_port)
    except (LANProxyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'PHAROS LAN PROXY ERROR: {exc}', file=sys.stderr)
        return 1
    print(f"Porta 80 attiva. Aprire PHAROS tramite IP o nome DNS della VM: {result['url_hint']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
