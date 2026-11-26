#!/usr/bin/env bash
# Free local equivalent of the PHAROS GitHub Actions verification on Ubuntu.
# No network access, package installation or changes to PHAROS data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PHAROS_TEST_PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON_BIN" >&2
  exit 2
fi
if ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
  echo "pytest is missing in $PYTHON_BIN. Use a separate test virtualenv with PHAROS [test] dependencies." >&2
  exit 2
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for the JavaScript smoke checks." >&2
  exit 2
fi

echo "[1/4] Python regression suite"
"$PYTHON_BIN" -m pytest -q

echo "[2/4] JavaScript syntax"
for file in \
  src/pharos/web/markdown.js \
  src/pharos/web/app.js \
  src/pharos/web/home.js \
  src/pharos/web/settings.js \
  src/pharos/web/knowledge.js \
  src/pharos/web/kiwix.js \
  src/pharos/web/jobs.js \
  src/pharos/web/updates.js \
  src/pharos/web/translations.js \
  src/pharos/web/maps.js \
  src/pharos/web/maps_viewer.js \
  src/pharos/web/maps_gpx.js \
  src/pharos/web/maps_search.js \
  src/pharos/web/kb_viewer.js; do
  node --check "$file"
done

echo "[3/4] Browser simulation without external services"
node tests/maps_ui_smoke.cjs
node tests/maps_search_ui_smoke.cjs
node tests/maps_viewer_smoke.cjs
node tests/maps_gpx_ui_smoke.cjs
node tests/gpx_auto_import_smoke.cjs
node tests/markdown_smoke.cjs
node tests/kiwix_reader_smoke.cjs
node tests/kb_viewer_smoke.cjs
node tests/chat_default_model_smoke.cjs
node tests/kb_jobs_notifications_smoke.cjs
node tests/kb_lexical_ui_smoke.cjs
node tests/knowledge_ui_smoke.cjs
node tests/updates_ui_smoke.cjs
node tests/section_headings_smoke.cjs

echo "[4/4] Installer and offline recovery syntax"
for file in install-pharos.sh uninstall-pharos.sh scripts/install-ubuntu.sh scripts/update-ubuntu.sh; do
  bash -n "$file"
done
"$PYTHON_BIN" -m py_compile scripts/pharos-backup.py scripts/windows_supervisor.py

echo "PHAROS local regression suite: PASS"
