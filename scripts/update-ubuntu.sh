#!/usr/bin/env bash
# Update a PHAROS Ubuntu Git checkout, then run the idempotent installer.
set -Eeuo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if ! git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo 'Aggiornamento GitHub disponibile solo da un clone Git del progetto PHAROS.' >&2
  exit 1
fi
# Existing local edits in a tracked stylesheet should never silently disappear.
if ! git -C "$SRC" diff --quiet -- src/pharos/web/layout-dev2.css; then
  DIRECTORY="${HOME}/.config/pharos/css-backups"
  mkdir -p "$DIRECTORY"
  chmod 700 "$DIRECTORY"
  SNAPSHOT="$DIRECTORY/layout-dev2-before-update-$(date -u +%Y%m%dT%H%M%SZ)-$$.css"
  ( umask 077; cp -- "$SRC/src/pharos/web/layout-dev2.css" "$SNAPSHOT" )
  echo "CSS modificato localmente: copia salvata in $SNAPSHOT"
  echo "Per renderlo persistente, spostare le sole regole personalizzate in ~/.config/pharos/custom.css"
fi
# Do not force, reset, stash, or rewrite local changes. If Git detects a
# conflict, fail visibly, retaining source files and all user data.
git -C "$SRC" pull --ff-only
exec bash "$SRC/scripts/install-ubuntu.sh" "$@"
