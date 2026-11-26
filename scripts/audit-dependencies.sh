#!/usr/bin/env bash
# Audit the selected PHAROS Python environment without installing anything into it.
set -Eeuo pipefail
if [[ $# -ne 2 ]]; then
    echo 'Usage: bash scripts/audit-dependencies.sh /path/to/python /new/report-directory' >&2
    exit 2
fi
PY="$1"
OUT="$2"
[[ -x "$PY" ]] || { echo 'Python not executable' >&2; exit 2; }
umask 077
mkdir -- "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
"$PY" -m pip list --format=json > "$OUT/packages.json"
"$PY" -m pip check > "$OUT/pip-check.txt" 2>&1 || { echo 'Dependency consistency check failed; see pip-check.txt' >&2; exit 1; }
"$PY" - "$OUT" <<'PYCODE'
import importlib.metadata as m
import json
import re
import sys
from pathlib import Path
out = Path(sys.argv[1])
packages = json.loads((out / 'packages.json').read_text())
records = []
requirements = []
for entry in sorted(packages, key=lambda p: p['name'].lower()):
    name, version = entry['name'], entry['version']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or not re.fullmatch(r'[A-Za-z0-9.+!_-]+', version):
        raise ValueError('Invalid package metadata')
    dist = m.distribution(name)
    records.append({'name': name, 'version': version,
                    'license_expression': dist.metadata.get('License-Expression'),
                    'license': dist.metadata.get('License'),
                    'license_classifiers': [c for c in dist.metadata.get_all('Classifier', []) if c.startswith('License ::')],
                    'license_files': [str(p) for p in (dist.files or []) if any(part.lower().startswith(('license', 'copying', 'notice')) for part in p.parts)]})
    if name.lower().replace('_', '-') != 'pharos-offline':
        requirements.append(f'{name}=={version}')
(out / 'licenses.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
(out / 'requirements.txt').write_text('\n'.join(requirements) + '\n', encoding='utf-8')
PYCODE
# Only a disposable auditor environment is changed. Queries disclose package
# names and versions to PyPI; no local PHAROS source or config is uploaded.
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
"$PY" -m venv "$TMP/venv"
"$TMP/venv/bin/python" -m pip install --disable-pip-version-check pip-audit
"$TMP/venv/bin/python" -m pip_audit --version > "$OUT/auditor-version.txt"
set +e
"$TMP/venv/bin/python" -m pip_audit --requirement "$OUT/requirements.txt" --no-deps --disable-pip --vulnerability-service pypi --format json --output "$OUT/vulnerabilities.json" 2> "$OUT/audit-stderr.txt"
STATUS=$?
set -e
printf '%s\n' "$STATUS" > "$OUT/audit-exit-code.txt"
echo "Audit reports: $OUT (exit $STATUS; review skipped packages and errors)"
exit "$STATUS"
