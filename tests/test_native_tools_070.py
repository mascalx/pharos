"""Native geographic tool protocol, using synthetic providers and indexes."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from pharos import maps_ai
from pharos.ai import Gateway, GatewayError
from pharos.config import AIConfig
from pharos.tools import geography
from pharos.tools.orchestrator import run
from pharos.tools.registry import ToolError


class Provider:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.history = []

    async def complete_tools(self, model, messages, tools, thinking=None, *, tool_choice="auto"):
        self.history.append((list(messages), tools, thinking, tool_choice))
        return next(self.calls)


def call(name, arguments, id="call1"):
    return {"message": {"content": None, "tool_calls": [
        {"id": id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]},
        "finish_reason": "tool_calls"}


def poi_args():
    return {"locality": "Bologna", "filters": "amenity=library"}


def test_native_poi_terminal_and_thinking(monkeypatch):
    cfg = SimpleNamespace(language="en")
    monkeypatch.setattr(maps_ai, "geo_indexes",
                        lambda cfg: {"items": [{"area": "sample", "tags_ready": True}]})
    expected = {"name": "Demo Library", "category": "poi",
                "latitude": 44.5, "longitude": 11.3, "area": "sample"}
    monkeypatch.setattr(maps_ai, "geo_tag_search",
                        lambda cfg, clauses, locality, limit: {
                            "items": [expected], "indexed_areas": 1,
                            "tag_pairs_available": True})
    provider = Provider([call("geo_search_pois", poi_args())])
    result = asyncio.run(maps_ai.geo_chat(provider, cfg, "demo",
                         "Find libraries in Bologna", thinking=False))
    assert result["geo_results"] == [expected]
    assert result["geo_status"] == "found"
    assert len(provider.history) == 1
    assert provider.history[0][3] == "required"
    assert provider.history[0][2] is False
    assert result["geo_diagnostics"]["status"] == "completed"
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse
    encoded = jsonable_encoder(result)
    response = JSONResponse(encoded)
    assert response.status_code == 200
    body = json.loads(response.body)
    snapshot = next(step["result"] for step in body["geo_diagnostics"]["steps"] if step.get("terminal"))
    assert snapshot["geo_results"] == [expected]
    assert "geo_diagnostics" not in snapshot
    result["geo_results"][0]["name"] = "changed"
    original_snapshot = next(step["result"] for step in result["geo_diagnostics"]["steps"] if step.get("terminal"))
    assert original_snapshot["geo_results"] == [expected]


def test_invalid_then_discovery_then_terminal(monkeypatch):
    cfg = SimpleNamespace(language="en")
    indexes = [{"area": "sample", "tags_ready": True}]
    monkeypatch.setattr(geography, "discover", lambda *args: {"values": ["library"]})
    monkeypatch.setattr(maps_ai, "geo_tag_search", lambda *args: {
        "items": [{"name":"Library","category":"poi","latitude":44.5,"longitude":11.3}], "indexed_areas": 1, "tag_pairs_available": True, "locality_found": True})
    provider = Provider([
        call("geo_search_pois", {"latitude": 1}, "a"),
        call("geo_discover_tags", {"key": "amenity"}, "b"),
        call("geo_search_pois", poi_args(), "c")])
    registry = geography.registry_for(cfg, "Find libraries in Bologna", indexes, 5)
    result = asyncio.run(run(provider, "demo", [], registry))
    assert result["status"] == "completed"
    assert result["result"]["geo_status"] == "found"
    assert provider.history[1][0][-1]["role"] == "tool"
    assert "invalid_arguments" in provider.history[1][0][-1]["content"]
    assert provider.history[2][0][-1]["tool_call_id"] == "b"


def test_native_no_call_never_invents_results():
    registry = geography.registry_for(SimpleNamespace(language="en"), "Find Rome", [], 5)
    provider = Provider([{"message": {"content": "invented coordinates"}, "finish_reason": "stop"}])
    result = asyncio.run(run(provider, "demo", [], registry))
    assert result["status"] == "no_tool_call"
    assert result["result"] is None


@pytest.mark.parametrize("raw", [
    '{"query":"Rome","latitude":42}',
    '{"query":"Rome","query":"Milan"}',
    '{"query":42}',
])
def test_strict_arguments(raw):
    registry = geography.registry_for(SimpleNamespace(language="en"), "Find Rome", [], 5)
    with pytest.raises(ToolError):
        registry.validate("geo_search_places", raw)


def test_native_deadline():
    class Slow:
        async def complete_tools(self, *a, **k):
            await asyncio.sleep(1)
    registry = geography.registry_for(SimpleNamespace(language="en"), "Find Rome", [], 5)
    assert asyncio.run(run(Slow(), "demo", [], registry, timeout=0.01))["status"] == "timeout"


def test_native_http_contract_and_text_regression(monkeypatch):
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        if "tools" in body:
            return httpx.Response(200, json={"choices": [call("geo_search_places", {"query":"Rome"})]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Pronto"}}]})
    transport = httpx.MockTransport(handle)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: original(*a, transport=transport, **kw))
    cfg = AIConfig(enabled=True, provider="openai_compatible",
                   base_url="http://example.invalid", default_model="demo",
                   request_timeout_seconds=5, max_output_tokens=100)
    gateway = Gateway(cfg)
    async def check():
        assert await gateway.complete("demo", []) == "Pronto"
        reply = await gateway.complete_tools("demo", [], [], thinking=None, tool_choice="required")
        assert reply["message"]["tool_calls"]
    asyncio.run(check())
    assert "tools" not in seen[0]
    assert seen[1]["tool_choice"] == "required"
    assert "chat_template_kwargs" not in seen[1]

def test_retry_wrong_filter_then_success(monkeypatch):
    cfg = SimpleNamespace(language="en")
    indexes = [{"area":"sample","tags_ready":True}]
    def lookup(cfg, clauses, locality, limit):
        correct = clauses == [{"amenity":"library"}]
        return {"items": [{"name":"Library","category":"poi","latitude":44.5,"longitude":11.3}] if correct else [],
                "locality_found":True,"tag_pairs_available":correct}
    monkeypatch.setattr(maps_ai, "geo_tag_search", lookup)
    wrong = {"locality":"Bologna","filters":"library=yes"}
    provider = Provider([call("geo_search_pois",wrong),
        call("geo_search_pois",wrong,"b"),
        call("geo_search_pois",poi_args(),"c")])
    registry = geography.registry_for(cfg,"Find libraries in Bologna",indexes,5)
    result = asyncio.run(run(provider,"demo",[],registry))
    assert result["status"] == "completed"
    assert result["result"]["geo_results"][0]["latitude"] == 44.5
    assert any(x.get("error","").startswith("repeated_call") for x in result["trace"])


def test_flat_parser():
    assert geography.parse_filters("shop=bakery OR amenity=restaurant AND cuisine=pizza") == [
        {"shop":"bakery"},{"amenity":"restaurant","cuisine":"pizza"}]


@pytest.mark.parametrize("filters", ["amenity", "amenity=*", "x=y AND x=z", "x=y OR", "x=y AND", "OR x=y", "x=y OR AND z=w", "x=y AND OR z=w", "x=y; DROP TABLE entries"])
def test_flat_parser_rejects_invalid(filters):
    with pytest.raises(ToolError):
        geography.parse_filters(filters)


def test_accent_normalization_and_inline_schema():
    from pharos.maps_geosearch import _words
    assert _words("Forlì") == _words("Forli")
    registry = geography.registry_for(SimpleNamespace(language="en"),"Find Rome",[],5)
    assert '"$ref"' not in json.dumps(registry.definitions())


def test_preview_is_independent_json_snapshot():
    from pharos.tools.orchestrator import _preview
    value = {"items": [{"name": "original"}]}
    snapshot = _preview(value, 8192)
    value["items"][0]["name"] = "changed"
    assert snapshot == {"items": [{"name": "original"}]}
    value["self"] = value
    assert _preview(value, 8192) == {"unserializable": True}
    assert _preview({"text": "a" * 100}, 10)["truncated"] is True
