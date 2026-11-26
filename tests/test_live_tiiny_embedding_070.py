"""0.7.0.dev3: live Tiiny Direct URL, safe embedding selection without RAG mutation."""
from __future__ import annotations

from fastapi.testclient import TestClient
from pharos.app import create_app
from pharos.config import load_config

HEAD = {"X-Pharos-Request": "1"}


def _direct(prepared):
    text = prepared.read_text(encoding="utf-8")
    text = text.replace('provider="openai_compatible"', 'provider="tiiny_direct"')
    prepared.write_text(text, encoding="utf-8")
    app = create_app(load_config(prepared))
    return app


def _change(client, section, **changes):
    original = client.get("/api/settings").json()
    original["values"][section].update(changes)
    return client.put("/api/settings", headers=HEAD,
                      json={"revision": original["revision"],
                            "values": original["values"]})


def test_direct_url_changes_live_without_replacing_fifo(prepared):
    app = _direct(prepared)
    queue = app.state.gateway.fifo
    with TestClient(app) as client:
        response = _change(client, "ai", base_url="http://192.0.2.10")
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"] is False
        assert app.state.gateway.fifo is queue
        assert app.state.gateway.config.base_url == "http://192.0.2.10"
        assert app.state.gateway.gateway.config.base_url == "http://192.0.2.10"
        assert app.state.gateway._native.base_url == "http://192.0.2.10"
        assert app.state.config.ai.base_url == "http://192.0.2.10"
        assert load_config(prepared).ai.base_url == "http://192.0.2.10"
        assert client.get("/api/settings").json()["restart_fields"] == []


def test_busy_tiiny_refuses_live_repoint_and_does_not_save(prepared):
    app = _direct(prepared)
    with TestClient(app) as client:
        before = prepared.read_bytes()
        app.state.gateway.fifo._active = True
        try:
            denied = _change(client, "ai", base_url="http://192.0.2.11")
            assert denied.status_code == 409
            assert denied.json()["detail"] == "ai_queue_busy"
            assert prepared.read_bytes() == before
            assert app.state.gateway.config.base_url == "http://127.0.0.1:9876"
        finally:
            app.state.gateway.fifo._active = False


def test_provider_change_is_live(prepared):
    app = _direct(prepared)
    with TestClient(app) as client:
        result = _change(client, "ai", provider="tiiny_gateway",
                         base_url="http://192.0.2.12")
        assert result.status_code == 200, result.text
        assert not result.json()["restart_required"]
        assert result.json()["restart_fields"] == []
        assert app.state.gateway.config.provider == "tiiny_gateway"
        assert app.state.gateway.config.base_url == "http://192.0.2.12"
        assert app.state.gateway._native is None
        back = _change(client, "ai", provider="tiiny_direct", base_url="http://192.0.2.13")
        assert back.status_code == 200, back.text
        assert app.state.gateway._native.base_url == "http://192.0.2.13"


def test_inactive_kb_embedding_preference_saves_without_activation(prepared):
    app = _direct(prepared)
    with TestClient(app) as client:
        result = _change(client, "rag", embedding_model="qwen3-embedding-4b")
        assert result.status_code == 200, result.text
        assert not result.json()["restart_required"]
        assert app.state.rag is None
        assert app.state.config.rag.embedding_model == "qwen3-embedding-4b"
        assert client.get("/api/settings").json()["values"]["rag"]["enabled"] is False


def test_embedding_selector_preserves_rag_guard_in_settings_js():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (root / "src/pharos/web/settings.js").read_text()
    assert "async function loadSettingsEmbeddingModels(refresh=false)" in code
    assert "api('embedding-models'" in code
    assert "settingsSnapshot.module_runtime_active?.knowledge" in code
    assert "select.disabled=true;select.dataset.skip='1'" in code
    assert "showView('knowledge')" in code
    for language in ("it", "en"):
        import json
        catalog = json.loads((root / "src/pharos/web/locales" / f"{language}.json").read_text())
        assert "settingsKBModelActiveHint" in catalog["messages"]
        assert "settingsKBModelHint" in catalog["messages"]


def test_settings_rerenders_on_language_save_without_page_navigation():
    """Regression: the open Settings form contains dynamically generated labels."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (root / "src/pharos/web/settings.js").read_text(encoding="utf-8")
    marker = "await window.pharosUseLanguage(result.language,true);"
    after = code.split(marker, 1)[1].split("await loadSettings(true);", 1)[0]
    assert "renderSettings();" in after
    assert "renderConfig();" in after
    assert "window.location.reload(" not in after


def test_route_and_key_saved_together_and_stale_revision_preserves_key(prepared):
    from pharos.secrets import read_secrets
    app = _direct(prepared)
    with TestClient(app) as client:
        snap = client.get("/api/settings").json()
        snap["values"]["ai"].update(provider="tiiny_gateway", base_url="http://192.0.2.12")
        body = {"values": snap["values"], "revision": snap["revision"], "api_key":"test-secret"}
        result = client.put("/api/settings", headers=HEAD, json=body)
        assert result.status_code == 200, result.text
        assert app.state.gateway.api_key == "test-secret"
        assert read_secrets(app.state.config.security.secrets_file)["ai"]["api_key"] == "test-secret"
        body["api_key"] = "stale-secret"
        result = client.put("/api/settings", headers=HEAD, json=body)
        assert result.status_code == 409
        assert app.state.gateway.api_key == "test-secret"
        assert read_secrets(app.state.config.security.secrets_file)["ai"]["api_key"] == "test-secret"
        assert "test-secret" not in client.get("/api/settings").text
        result = _change(client, "ai", provider="tiiny_direct", base_url="http://192.0.2.13")
        assert result.status_code == 200
        assert app.state.gateway.api_key == ""
        assert app.state.gateway._native.api_key == ""
