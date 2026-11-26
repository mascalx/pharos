"""Regression checks for official language catalogs."""
import json
import re
from pathlib import Path
from pharos.config_edit import FIELDS

WEB = Path(__file__).resolve().parents[1] / "src/pharos/web"

def test_translation_catalogs():
    en, it = [json.loads((WEB / "locales" / f"{lang}.json").read_text(encoding="utf-8"))["messages"] for lang in ("en", "it")]
    assert en.keys() == it.keys()
    for key in en:
        assert sorted(re.findall(r"\{[A-Za-z_]\w*\}", en[key])) == sorted(re.findall(r"\{[A-Za-z_]\w*\}", it[key]))
        assert not re.search(r"\b\d+\.\d+\.\d+(?:\.dev\d+)?\b", en[key] + " " + it[key])
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="sidebar-version">—</span>' in html
    assert "el('sidebar-version').textContent=appConfig.version" in (WEB / "app.js").read_text(encoding="utf-8")
    for key in ("heroTag", "kbRename", "updatesInstallConfirm", "mapsSub", "modulesInstall", "settingsLabel_require_login"):
        assert key not in en
    required = set(re.findall(r'data-i18n(?:-placeholder|-aria|-title)?="([A-Za-z]\w*)"', html))
    for name in re.findall(r'<script src="/static/([^"?]+\.js)\?', html):
        required.update(re.findall(r"""\b(?:t|gpxMsg)\(\s*['"]([A-Za-z]\w*)['"]\s*\)""", (WEB / name).read_text(encoding="utf-8")))
    required.update("settingsLabel_" + key for fields in FIELDS.values() for key in fields if key != "management_url")
    assert required <= en.keys(), sorted(required - en.keys())
