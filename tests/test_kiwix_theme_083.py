"""Kiwix article palette injection must preserve document and prevent binary rewriting."""
from pharos.kiwix_proxy import (
    KIWIX_PHAROS_THEME, _insert_kiwix_theme, _THEME_SCAN_LIMIT
)


def test_kiwix_theme_inserted_before_head_end():
    source = b'<!DOCTYPE html><html><head><title>Offline</title></head><body>Article</body></html>'
    patched = _insert_kiwix_theme(source)
    assert patched.count(KIWIX_PHAROS_THEME) == 1
    assert patched.index(KIWIX_PHAROS_THEME) < patched.index(b'</head>')
    assert b'<title>Offline</title>' in patched
    assert b'<body>Article</body>' in patched
    assert b'#faebdc' in patched


def test_kiwix_theme_body_fallback():
    source = b'<html><body><main>Text</main></body></html>'
    patched = _insert_kiwix_theme(source)
    assert patched.count(KIWIX_PHAROS_THEME) == 1
    assert patched.index(KIWIX_PHAROS_THEME) > patched.index(b'<body>')


def test_kiwix_theme_no_html_markers_remains_unchanged():
    source = b'plain text without html elements'
    assert _insert_kiwix_theme(source) == source
    assert 0 < _THEME_SCAN_LIMIT <= 256 * 1024
