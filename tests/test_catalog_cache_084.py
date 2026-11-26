from __future__ import annotations

from fastapi.testclient import TestClient
from pharos.app import create_app
from pharos.config import load_config


def test_shared_ai_catalog_cache_and_manual_refresh(prepared, monkeypatch):
    app = create_app(load_config(prepared))
    calls = []
    async def slow_inventory():
        calls.append(1)
        return {"mode": "tiiny_direct", "complete": True, "source": "test",
                "models": [
                    {"id": "chat-model", "kind": "chat", "loaded": True},
                    {"id": "test-embedding", "kind": "embedding", "loaded": False}
                ]}
    monkeypatch.setattr(app.state.gateway, "catalog", slow_inventory)
    with TestClient(app) as client:
        first=client.get("/api/ai/catalog")
        assert first.status_code == 200
        assert first.json()["models"][0]["id"] == "chat-model"
        second=client.get("/api/embedding-models")
        assert second.status_code == 200
        assert "test-embedding" in second.json()["models"]
        assert len(calls)==1, "Chat and KB should share one upstream inventory"
        cached=client.get("/api/ai/catalog")
        assert cached.status_code == 200
        assert len(calls)==1
        refreshed=client.get("/api/ai/catalog?refresh=true")
        assert refreshed.status_code == 200
        assert len(calls)==2, "Manual refresh must bypass cached inventory"
        kb_refresh=client.get("/api/embedding-models?refresh=true")
        assert kb_refresh.status_code == 200
        assert len(calls)==3


def test_catalog_errors_are_not_cached(prepared, monkeypatch):
    app=create_app(load_config(prepared))
    calls=[]
    async def fails_then_works():
        calls.append(1)
        if len(calls)==1:
            from pharos.ai import GatewayError
            raise GatewayError("catalog_temporarily_unavailable")
        return {"mode":"tiiny_direct","complete":True,"models":[]}
    monkeypatch.setattr(app.state.gateway, "catalog", fails_then_works)
    with TestClient(app) as client:
        assert client.get("/api/ai/catalog").status_code == 502
        assert client.get("/api/ai/catalog").status_code == 200
        assert len(calls)==2
