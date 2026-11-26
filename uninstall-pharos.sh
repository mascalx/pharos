#!/usr/bin/env bash
# Conservative removal of the default per-user PHAROS Ubuntu installation.
set -Eeuo pipefail
if [[ "$EUID" -eq 0 ]]; then echo 'Run as the PHAROS user, not root.' >&2; exit 1; fi
if (($#)); then echo 'Usage: bash uninstall-pharos.sh (interactive, preserves all data)' >&2; exit 2; fi
STATE="$HOME/.local/share/pharos"
VENV="$STATE/venv"
UNITS="$HOME/.config/systemd/user"
# Refuse symlinked installation locations; do not follow alternate storage.
for path in "$HOME/.local" "$HOME/.local/share" "$STATE" "$VENV" "$HOME/.config" "$HOME/.config/systemd" "$UNITS"; do
  if [[ -L "$path" ]]; then echo "Symlinked path: $path; manual review required." >&2; exit 1; fi
done
if [[ ! -d "$STATE" ]]; then echo 'No default PHAROS state directory found.'; exit 0; fi
if [[ -L "$STATE/installer.lock" ]]; then echo 'Unsafe installer lock.' >&2; exit 1; fi
exec 9>"$STATE/installer.lock"
flock -n 9 || { echo 'PHAROS installation/update in progress.' >&2; exit 1; }
# Verify every existing unit before making changes.
for unit in pharos.service pharos-qdrant.service; do
  file="$UNITS/$unit"
  if [[ -L "$file" ]]; then echo "Refusing symlinked unit: $file" >&2; exit 1; fi
  if [[ -e "$file" ]]; then
    if [[ "$unit" == pharos.service ]]; then
      grep -Fxq 'Description=PHAROS integrated web service' "$file" &&
        grep -Fq "$VENV/bin/pharos" "$file" || { echo "Unrecognized unit: $file" >&2; exit 1; }
    else
      grep -Fxq 'Description=PHAROS managed Qdrant' "$file" || { echo "Unrecognized unit: $file" >&2; exit 1; }
    fi
  fi
done
if [[ -e "$VENV" && ( ! -f "$VENV/pyvenv.cfg" || ! -f "$VENV/bin/pharos" ) ]]; then
  echo "Unrecognized virtual environment: $VENV" >&2; exit 1
fi
echo 'This removes PHAROS user services and its dedicated virtualenv.'
echo "Virtualenv: $VENV"
echo 'Preserved: source checkout, TOML, secrets, all data, databases, archives, cache and managed binaries.'
echo 'NGINX, shared packages and system settings are not changed. See docs/UNINSTALL.md.'
read -r -p 'Type REMOVE PHAROS to continue: ' answer </dev/tty
[[ "$answer" == 'REMOVE PHAROS' ]] || { echo 'Cancelled.'; exit 0; }
for unit in pharos.service pharos-qdrant.service; do
  if [[ -f "$UNITS/$unit" ]]; then
    systemctl --user disable --now "$unit"
    if systemctl --user is-active --quiet "$unit"; then echo "Service still active: $unit" >&2; exit 1; fi
  fi
done
for unit in pharos.service pharos-qdrant.service; do
  if [[ -f "$UNITS/$unit" ]]; then rm -- "$UNITS/$unit"; fi
done
systemctl --user daemon-reload
# Keep the directory and all storage outside this dedicated environment.
if [[ -d "$VENV" ]]; then
  rm -rf --one-file-system -- "$VENV"
fi
# Remove only installation markers, not backups or config.
for name in source.sha256 update-source.json; do
  if [[ -f "$STATE/$name" && ! -L "$STATE/$name" ]]; then rm -- "$STATE/$name"; fi
done
echo 'PHAROS application removed. Data retained. See docs/UNINSTALL.md for remaining files and optional proxy cleanup.'
