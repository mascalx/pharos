#!/usr/bin/env bash
# PHAROS Ubuntu x86-64 rootless bootstrap. Run from the downloaded Git repository.
set -Eeuo pipefail
if [[ "${EUID}" -eq 0 ]]; then echo 'Non eseguire come root o con sudo.' >&2; exit 1; fi
if [[ ! -r /etc/os-release ]]; then echo '/etc/os-release mancante' >&2; exit 1; fi
# shellcheck source=/etc/os-release
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "$(uname -m)" != x86_64 ]]; then
  echo 'Questa build è dedicata a Ubuntu x86-64. Nessuna installazione eseguita.' >&2; exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then echo 'Python 3.11+ è richiesto' >&2; exit 1; fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 11): raise SystemExit('Python >=3.11 richiesto')
PY
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV="${HOME}/.local/share/pharos/venv"
if [[ ! -f "$VENV/bin/python" ]]; then
  if ! python3 -m venv "$VENV"; then
    # Minimal Ubuntu images may omit python3-venv. Only ask for privilege
    # escalation when the Python standard-library environment is unavailable.
    if command -v sudo >/dev/null 2>&1 && [[ -t 0 ]]; then
      echo 'Python venv non disponibile. Con consenso verrà installato python3-venv.' >&2
      read -r -p 'Installare il pacchetto ufficiale Ubuntu tramite sudo? [s/N] ' reply
      if [[ "$reply" == s || "$reply" == S ]]; then
        sudo apt-get update && sudo apt-get install -y python3-venv
        python3 -m venv "$VENV" || { echo 'Creazione ambiente Python fallita.' >&2; exit 1; }
      else
        echo 'Setup interrotto senza modificare i pacchetti del sistema.' >&2; exit 1
      fi
    else
      echo 'python3-venv non disponibile; installarlo dai repository Ubuntu per proseguire.' >&2; exit 1
    fi
  fi
fi
# Existing installs must be updated in place, never treated as a first setup.
# The authoritative TOML and all secrets are outside the Python package.
CONFIG="${HOME}/.config/pharos/config.toml"
arguments=("$@")
for ((i=0; i<${#arguments[@]}; i++)); do
  case "${arguments[i]}" in
    --config)
      if (( i+1 >= ${#arguments[@]} )); then echo 'Valore mancante per --config' >&2; exit 2; fi
      CONFIG="${arguments[i+1]}"; ((i+=1));;
    --config=*) CONFIG="${arguments[i]#--config=}";;
  esac
done
CONFIG="$(CONFIG_FILE="$CONFIG" python3 - <<'PYCONFIG'
import os
from pathlib import Path
print(Path(os.environ['CONFIG_FILE']).expanduser().resolve())
PYCONFIG
)"
STATE="${HOME}/.local/share/pharos"
mkdir -p "$STATE"
# Serialize setup/update operations (and protect their backup metadata).
exec 9>"$STATE/installer.lock"
if ! flock -n 9; then echo 'Un altro installer PHAROS è già in esecuzione.' >&2; exit 1; fi
SOURCE_HASH="$(SRC_DIR="$SRC" python3 - <<'PYHASH'
import hashlib, os
from pathlib import Path
root=Path(os.environ['SRC_DIR']); digest=hashlib.sha256()
for base in (root/'pyproject.toml',root/'README.md'):
    digest.update(base.relative_to(root).as_posix().encode());digest.update(base.read_bytes())
for file in sorted((root/'src').rglob('*')):
    if not file.is_file() or '__pycache__' in file.parts or file.suffix in ('.pyc', '.pyo'):
        continue
    digest.update(file.relative_to(root).as_posix().encode());digest.update(file.read_bytes())
print(digest.hexdigest())
PYHASH
)"
STAMP="$STATE/source.sha256"
if [[ ! -f "$CONFIG" && -f "${HOME}/.config/systemd/user/pharos.service" ]]; then
  echo 'Esiste già un servizio PHAROS, ma il TOML indicato non è stato trovato.' >&2
  echo 'Per proteggere l’installazione originale, ripetere con --config /percorso/del/config.toml.' >&2
  exit 1
fi
if [[ -f "$CONFIG" ]]; then
  echo 'PHAROS: installazione esistente rilevata — aggiornamento conservativo.'
  BACKUP_DIR="$STATE/config-backups"
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  BACKUP="$BACKUP_DIR/config-$(date -u +%Y%m%dT%H%M%SZ)-$$.toml"
  ( umask 077; cp -- "$CONFIG" "$BACKUP" )
  echo "Backup preventivo del TOML: $BACKUP"
else
  echo 'PHAROS: nuova installazione.'
fi
# Do not reinstall Python files if the checked-out source is unchanged.
if [[ -x "$VENV/bin/pharos" && -r "$STAMP" && "$(cat "$STAMP")" == "$SOURCE_HASH" ]]; then
  echo 'Pacchetto PHAROS già aggiornato: installazione Python non necessaria.'
else
  "$VENV/bin/python" -m pip install --disable-pip-version-check "$SRC"
  printf '%s\n' "$SOURCE_HASH" > "$STAMP.tmp.$$"
  mv -f -- "$STAMP.tmp.$$" "$STAMP"
fi
# Configure the LAN listener independently of the existing application TOML.
# The internal PHAROS port stays in the authoritative TOML. --no-start is used
# in automated tests, so it must not request sudo or alter the host system.
LAN_PROXY=true
INSTALL_ARGS=()
while (($#)); do
  case "$1" in
    --skip-lan-proxy) LAN_PROXY=false; shift;;
    --no-start) LAN_PROXY=false; INSTALL_ARGS+=("$1"); shift;;
    *) INSTALL_ARGS+=("$1"); shift;;
  esac
done
"$VENV/bin/python" -m pharos.ubuntu_installer "${INSTALL_ARGS[@]}"
# Prepare pinned offline cartography renderer as part of normal PHAROS setup.
# Do not disrupt an otherwise healthy install when the publisher is unreachable;
# archives and existing viewer resources remain untouched. No sudo or Docker.
if ! "$VENV/bin/python" -m pharos.maps_viewer_assets --config "$CONFIG"; then
  echo 'PHAROS Mappe: risorse visualizzatore non ancora disponibili. Riprova l’installer con Internet funzionante.' >&2
fi
if [[ "$LAN_PROXY" == true ]]; then
  APP_PORT="$(CONFIG_FILE="$CONFIG" "$VENV/bin/python" - <<'PYPORT'
import os
from pathlib import Path
from pharos.config import load_config
print(load_config(Path(os.environ['CONFIG_FILE'])).server.port)
PYPORT
)"
  "$VENV/bin/python" -m pharos.lan_proxy --upstream-port "$APP_PORT"
fi
# Record the successfully installed revision for read-only update checks.
# Do not store authentication material, remote URLs or NAS paths here.
if git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  REVISION="$(git -C "$SRC" rev-parse HEAD)"
  SRC_DIR="$SRC" INSTALLED_REVISION="$REVISION" STATE_DIR="$STATE" python3 - <<'PYUPDATE'
import json
import os
from pathlib import Path
import re
import tempfile
root=Path(os.environ['SRC_DIR']).resolve()
sha=os.environ['INSTALLED_REVISION'].lower()
state=Path(os.environ['STATE_DIR'])
if re.fullmatch(r'[0-9a-f]{40}',sha):
    target=state/'update-source.json'
    # Existing state location is already protected by installer.lock.
    fd,temp=tempfile.mkstemp(prefix='.update-source-',dir=state)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump({'checkout':str(root),'installed_sha':sha},stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp,0o600)
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
PYUPDATE
fi
