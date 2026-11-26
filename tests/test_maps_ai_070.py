"""Dev17 regression: opt-in model interpretation + offline-only geographic lookup.

The AI can propose only a query. Every displayed coordinate must originate
from the existing deterministic index. All tests run without Tiiny/Internet.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pharos import maps_ai
from pharos.ai import Gateway
from pharos.app import create_app
from pharos.config import load_config


def fixture_config():
    return SimpleNamespace(language="it")


def record():
    return {
        "name": "Via delle Rose", "category": "street",
        "latitude": 44.447, "longitude": 11.865,
        "area": "nord-est", "locality_hint": "San Pietro del Fiume",
        "approximate_street": True, "requested_number": "12",
        "detail": "", "osm_id": "w555", "searchable": "irrelevant",
    }


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def complete(self, model, messages, **kwargs):
        self.calls.append((model, messages, kwargs))
        return self.output


def test_geo_planner_accepts_structured_query_and_qwen_think_wrappers():
    question="Dov'è Via delle Rose 12 a S.Pietro del Fiume?"
    completion='<think>Ignore bracketed guesses.</think>\n' + json.dumps({
        "intent":"lookup",
        "query":"Via delle Rose 12, San Pietro del Fiume"})
    assert maps_ai.parse_plan(completion, question)=={
        "intent":"lookup",
        "query":"Via delle Rose 12, San Pietro del Fiume"}


@pytest.mark.parametrize("completion", [
    "Coordinates: 44.4, 11.8",
    '{"intent":"lookup","query":"Via delle Rose, Roma"}',
    '{"intent":"lookup","query":"Via delle Rose, San Pietro del Fiume"}',
    '{"intent":"lookup","query":"Via delle Rose 12, San Pietro del Fiume","latitude":44.4}',
    '{"intent":"lookup","query":5}',
    '{"intent":"find","query":"Via delle Rose 12"}',
    '{"intent":"lookup","query":"Via delle Rose 12"}\\n{"intent":"lookup","query":"other"}',
])
def test_geo_planner_rejects_untrusted_or_incomplete_predictions(completion):
    question="Dove si trova Via delle Rose 12 a S.Pietro del Fiume?"
    assert maps_ai.parse_plan(completion, question) is None


def test_geo_chat_verified_coords_and_single_model_call(monkeypatch):
    question="Dove si trova Via delle Rose 12 a S.Pietro del Fiume?"
    selected="Via delle Rose 12, San Pietro del Fiume"
    provider=FakeProvider(json.dumps({"intent":"lookup","query":selected}))
    invoked=[]

    monkeypatch.setattr(maps_ai, "geo_indexes", lambda cfg: {"items":[{"area":"nord-est"}]})
    def fake_search(cfg, query, limit):
        invoked.append((query, limit))
        return {"indexes":1, "items":[record()]}
    monkeypatch.setattr(maps_ai,"geo_search", fake_search)

    reply=asyncio.run(maps_ai.geo_chat(provider,fixture_config(),"qwen3",question))
    assert len(provider.calls)==1
    assert invoked==[(selected,5)]
    assert reply["geo_status"]=="found"
    assert reply["geo_results"][0]["latitude"]==44.447
    assert reply["geo_results"][0]["longitude"]==11.865
    assert "osm_id" not in reply["geo_results"][0]
    assert "searchable" not in reply["geo_results"][0]
    assert reply["geo_results"][0]["approximate_street"]
    assert "itinerari" not in reply["answer"]


def test_no_installed_index_never_calls_ai(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",lambda cfg:{"items":[]})
    provider=FakeProvider("nothing")
    reply=asyncio.run(maps_ai.geo_chat(
        provider,fixture_config(),"model","Dove si trova Via Roma?"))
    assert reply["geo_status"]=="no_indexes"
    assert provider.calls==[]


def test_no_results_and_unsupported_are_not_hallucinated(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",lambda cfg:{"items":[{"area":"nord-est"}]})
    called=[]
    def missing(cfg, query, limit):
        called.append(query)
        return {"items":[],"indexes":1}
    monkeypatch.setattr(maps_ai,"geo_search",missing)
    absent=asyncio.run(maps_ai.geo_chat(
        FakeProvider('{"intent":"lookup","query":"Via delle Rose 12"}'),
        fixture_config(),"model","Cercami Via delle Rose 12"))
    assert absent["geo_status"]=="no_results"
    assert absent["geo_results"]==[]
    assert called==["Via delle Rose 12"]
    refused=asyncio.run(maps_ai.geo_chat(
        FakeProvider('{"intent":"unsupported","query":""}'),
        fixture_config(),"model","Calcola un percorso da Ravenna a Bologna"))
    assert refused["geo_status"]=="unsupported"
    assert refused["geo_results"]==[]
    assert called==["Via delle Rose 12"]


def test_malformed_model_output_never_falls_through_to_generic_chat(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",lambda cfg:{"items":[{"area":"nord-est"}]})
    monkeypatch.setattr(maps_ai,"geo_search",
                        lambda *a,**k:pytest.fail("must not search with invalid plan"))
    reply=asyncio.run(maps_ai.geo_chat(
        FakeProvider("The address is at coordinates 45, 12."),
        fixture_config(),"model","Where is Via delle Rose 12?"))
    assert reply["geo_status"]=="parse_error"
    assert not reply["geo_results"]


def test_geo_chat_http_mode_is_opt_in_and_rejects_kb_mix(prepared,monkeypatch):
    import pharos.app as app_module
    calls=[]
    async def fake_geo(provider, config, model, question, **kwargs):
        calls.append((model,question,kwargs))
        return {"answer":"Ho trovato un luogo offline.","geo_status":"found",
                "geo_query":"Ravenna",
                "geo_results":[{"name":"Ravenna","category":"place","latitude":44.42,
                                "longitude":12.2,"area":"nord-est"}]}
    monkeypatch.setattr(app_module,"geo_chat",fake_geo)
    with TestClient(create_app(load_config(prepared))) as client:
        hdr={"X-Pharos-Request":"1"}
        payload={"model":"mock-model","messages":[{"role":"user","content":"Dove si trova Ravenna?"}]}
        combined={**payload,"kb_ids":["kb"],"geo_enabled":True}
        forbidden=client.post("/api/chat",headers=hdr,json=combined)
        assert forbidden.status_code==422
        assert forbidden.json()["detail"]=="geo_kb_mode_conflict"
        valid=client.post("/api/chat",headers=hdr,json={**payload,"geo_enabled":True})
        assert valid.status_code==200
        assert valid.json()["answer"]=="Ho trovato un luogo offline."
        assert valid.json()["geo_results"][0]["name"]=="Ravenna"
        assert valid.json()["sources"]==[]
        assert len(calls)==1
        # Ordinary chat still follows the original provider flow.
        ordinary=client.post("/api/chat",headers=hdr,json=payload)
        assert ordinary.status_code==503


def test_geo_chat_opt_in_markup_and_safe_result_rendering():
    """The map interpreter must not silently turn on for ordinary Chat."""
    from pathlib import Path
    web=Path(__file__).resolve().parents[1]/"src"/"pharos"/"web"
    html=(web/"index.html").read_text(encoding="utf-8")
    js=(web/"app.js").read_text(encoding="utf-8")
    assert 'id="chat-geo-enabled"' in html
    assert 'id="chat-geo-enabled" checked' not in html
    assert '/static/app.js?v=' in html
    assert "geo_enabled" in js
    assert "if(geo_enabled&&kb_ids.length)" in js
    assert "title.textContent=String(item.name||'')" in js
    assert "window.pharosOpenMapForPoint" in js


def test_museum_category_plan_and_fallback_for_small_models(monkeypatch):
    question="Dove si trovano i musei di Bologna?"
    expected={"intent":"category_lookup","category":"museum","locality":"Bologna"}
    assert maps_ai.parse_plan(json.dumps(expected),question)==expected
    # Some compact models still answer "unsupported" to a category
    # question: deterministic user-text recognition keeps it supported.
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"sample","category_ready":True}]})
    actual=[]
    def category(config, kind, town, limit):
        actual.append((kind,town,limit))
        return {"items":[{"name":"Pinacoteca Civica","category":"poi",
                          "latitude":44.496,"longitude":11.346,"area":"sample",
                          "locality_hint":"Bologna"}],
                "requires_reindex":[]}
    monkeypatch.setattr(maps_ai,"geo_category_search",category)
    provider=FakeProvider('{"intent":"unsupported","query":""}')
    result=asyncio.run(maps_ai.geo_chat(provider,fixture_config(),"test",question))
    assert result["geo_status"]=="found"
    assert len(result["geo_results"])==1
    assert result["geo_results"][0]["name"]=="Pinacoteca Civica"
    assert actual==[("museum","Bologna",10)]
    assert len(provider.calls)==1


def test_old_category_index_gives_reindex_action_not_false_missing(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"sample","category_ready":False}]})
    monkeypatch.setattr(maps_ai,"geo_category_search",
                        lambda *args,**kwargs:{"items":[],"requires_reindex":["sample"]})
    answer=asyncio.run(maps_ai.geo_chat(
        FakeProvider('{"intent":"unsupported","query":""}'),
        fixture_config(),"test","Dove si trovano i musei di Bologna?"))
    assert answer["geo_status"]=="needs_reindex"
    assert "Ricostruisci indice" in answer["answer"]
    assert not answer["geo_results"]


def test_category_query_does_not_turn_a_route_into_location_search(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"sample"}]})
    monkeypatch.setattr(maps_ai,"geo_category_search",
                        lambda *a,**k:pytest.fail("must not run category search"))
    question="Calcola un percorso tra i musei di Bologna"
    result=asyncio.run(maps_ai.geo_chat(
        FakeProvider('{"intent":"unsupported","query":""}'),
        fixture_config(),"test",question))
    assert result["geo_status"]=="unsupported"
    assert result["geo_results"]==[]


@pytest.mark.parametrize(("question","category","town"), [
    ("Dove si trovano i ristoranti di Bologna?", "restaurant", "Bologna"),
    ("Mostrami i ristoranti a Bologna", "restaurant", "Bologna"),
    ("Cerco ristoranti in Modena", "restaurant", "Modena"),
    ("Dove sono le farmacie di Bologna?", "pharmacy", "Bologna"),
    ("Trova una farmacia a Bologna", "pharmacy", "Bologna"),
    ("Farmacie vicino a Modena", "pharmacy", "Modena"),
    ("Dove sono i musei di Bologna?", "museum", "Bologna"),
    ("restaurants near Bologna", "restaurant", "Bologna"),
    ("pharmacies in Bologna", "pharmacy", "Bologna"),
])
def test_supported_category_question_forms(question,category,town):
    expected={"intent":"category_lookup","category":category,"locality":town}
    assert maps_ai._category_hint(question)==expected
    assert maps_ai.parse_plan(json.dumps(expected),question)==expected


@pytest.mark.parametrize("question", [
    "Quali farmacie sono aperte ora a Bologna?",
    "Trova i ristoranti migliori di Bologna",
    "Calcola il percorso tra i ristoranti di Bologna",
    "Ristoranti a Bologna aperti oggi",
    "Quanto tempo serve per arrivare ai musei di Bologna?",
    "Dove trovo le pizzerie a Bologna?",  # Not supported in dev19
])
def test_category_mode_does_not_claim_unsupported_facts(question):
    assert maps_ai._category_hint(question) is None


def test_planner_cannot_switch_a_pharmacy_into_restaurants():
    user="Dove si trovano le farmacie di Bologna?"
    wrong='{"intent":"category_lookup","category":"restaurant","locality":"Bologna"}'
    altered='{"intent":"category_lookup","category":"pharmacy","locality":"Roma"}'
    assert maps_ai.parse_plan(wrong,user) is None
    assert maps_ai.parse_plan(altered,user) is None


@pytest.mark.parametrize(("category","label"), [
    ("restaurant","ristoranti"),
    ("pharmacy","farmacie"),
    ("museum","musei"),
])
def test_geo_chat_category_fallback_has_grounded_results(monkeypatch,category,label):
    question=f"Dove si trovano i {label} di Bologna?"
    if category=="pharmacy":
        question="Dove si trovano le farmacie di Bologna?"
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"sample","category_ready":True}]})
    invoked=[]
    def query(config,kind,town,limit):
        invoked.append((kind,town,limit))
        return {"items":[{"name":"Nome di prova","category":"poi",
                          "latitude":44.49,"longitude":11.34,
                          "area":"sample","poi_kind":"ignored"}],
                "requires_reindex":[]}
    monkeypatch.setattr(maps_ai,"geo_category_search",query)
    # Model response can be malformed: an explicit category+town should
    # still invoke the deterministic parser and never invent coordinates.
    provider=FakeProvider("Malformed model response!")
    reply=asyncio.run(maps_ai.geo_chat(provider,fixture_config(),"test",question))
    assert reply["geo_status"]=="found"
    assert invoked==[(category,"Bologna",10)]
    assert reply["geo_results"][0]["latitude"]==44.49
    assert "poi_kind" not in reply["geo_results"][0]
    singular = {"restaurant": "ristorante", "pharmacy": "farmacia", "museum": "museo"}[category]
    assert f"1 {singular}" in reply["answer"]
    assert reply["geo_query"].startswith(label)
    assert len(provider.calls)==1


def test_category_no_results_is_not_mislabelled_as_museums(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"sample","category_ready":True}]})
    monkeypatch.setattr(maps_ai,"geo_category_search",
                        lambda *a,**k:{"items":[],"requires_reindex":[]})
    result=asyncio.run(maps_ai.geo_chat(
        FakeProvider('{"intent":"unsupported","query":""}'),
        fixture_config(),"test","Dove sono i ristoranti di Bologna?"))
    assert result["geo_status"]=="category_no_results"
    assert result["geo_results"]==[]
    assert "musei" not in result["answer"]


@pytest.mark.parametrize(("question","label","clauses"),[
    ("Dove sono le pizzerie di Bologna?", "pizzerie",
     [{"amenity":"restaurant","cuisine":"pizza"}]),
    ("Dove sono i panifici a Bologna?", "panifici",
     [{"shop":"bakery"}]),
    ("Dove sono gli elettricisti di Bologna?", "elettricisti",
     [{"craft":"electrician"}]),
    ("Dove sono i campeggi vicino a Bologna?", "campeggi",
     [{"tourism":"camp_site"}]),
    ("Dove sono le biblioteche di Bologna?", "biblioteche",
     [{"amenity":"library"}]),
])
def test_generic_model_tags_are_not_a_coded_category_list(question,label,clauses):
    payload={"intent":"tag_lookup","label":label,"locality":"Bologna",
             "clauses":clauses}
    assert maps_ai.parse_plan(json.dumps(payload),question)==payload
    fenced=(chr(96)*3)+"json\n"+json.dumps(payload)+"\n"+(chr(96)*3)
    assert maps_ai.parse_plan(fenced,question)==payload


def test_generic_tag_parser_rejects_wrong_place_category_or_routing():
    user="Dove sono le pizzerie di Bologna?"
    common={"intent":"tag_lookup","label":"pizzerie","locality":"Bologna",
            "clauses":[{"amenity":"restaurant","cuisine":"pizza"}]}
    for modifications in (
        {"locality":"Roma"},
        {"label":"farmacie"},
        {"clauses":[{"amenity":"restaurant"}]*9},
        {"clauses":[{"amenity":"restaurant;drop table"}]},
    ):
        data={**common,**modifications}
        assert maps_ai.parse_plan(json.dumps(data),user) is None
    assert maps_ai.parse_plan(json.dumps(common),
                              "Calcola il percorso alle pizzerie di Bologna") is None
    assert maps_ai.parse_plan(json.dumps(common),
                              "Trova le pizzerie aperte ora di Bologna") is None
    assert maps_ai.parse_plan(json.dumps(common)+"\n"+json.dumps(common),user) is None


def test_geo_chat_dynamic_tag_lookup_is_verified_by_sqlite(monkeypatch):
    question="Dove sono i panifici di Bologna?"
    parsed={"intent":"tag_lookup","label":"panifici","locality":"Bologna",
            "clauses":[{"shop":"bakery"}]}
    provider=FakeProvider(json.dumps(parsed))
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"demo","tags_ready":True}]})
    monkeypatch.setattr(maps_ai,"geo_tag_catalog",
                        lambda cfg:["shop","amenity","craft"])
    observed=[]
    def search(cfg,clauses,locality,limit):
        observed.append((clauses,locality,limit))
        return {"items":[{"name":"Forno Sole","category":"poi",
                          "latitude":44.49,"longitude":11.34,
                          "area":"demo","locality_hint":"Bologna"}],
                "indexed_areas":1,"requires_reindex":[],
                "tag_pairs_available":True,"locality_found":True}
    monkeypatch.setattr(maps_ai,"geo_tag_search",search)
    answer=asyncio.run(maps_ai.geo_chat(provider,fixture_config(),"qwen",question))
    assert observed==[([{"shop":"bakery"}],"Bologna",20)]
    assert answer["geo_status"]=="found"
    assert answer["geo_results"][0]["name"]=="Forno Sole"
    assert answer["geo_results"][0]["latitude"]==44.49
    assert "shop=bakery" in answer["geo_query"]
    assert "panifici" in answer["answer"]
    assert "shop" in provider.calls[0][1][0]["content"]


def test_generic_category_old_index_prompts_rebuild_and_does_not_run_search(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"demo","tags_ready":False}]})
    monkeypatch.setattr(maps_ai,"geo_tag_catalog",
                        lambda *a:pytest.fail("v2 should not query tag catalog"))
    monkeypatch.setattr(maps_ai,"geo_tag_search",
                        lambda *a:{"items":[],"requires_reindex":["demo"],
                                   "indexed_areas":0,"tag_pairs_available":False})
    raw='{"intent":"tag_lookup","label":"panifici","locality":"Bologna",' \
        '"clauses":[{"shop":"bakery"}]}'
    answer=asyncio.run(maps_ai.geo_chat(
        FakeProvider(raw),fixture_config(),"qwen","Dove sono i panifici di Bologna?"))
    assert answer["geo_status"]=="needs_reindex"
    assert "Ricostruisci indice" in answer["answer"]


def test_generic_unknown_osm_pair_is_not_claimed_found(monkeypatch):
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"demo","tags_ready":True}]})
    monkeypatch.setattr(maps_ai,"geo_tag_catalog",lambda *a:["shop"])
    monkeypatch.setattr(maps_ai,"geo_tag_search",
                        lambda *a:{"items":[],"requires_reindex":[],
                                   "indexed_areas":1,"tag_pairs_available":False})
    raw='{"intent":"tag_lookup","label":"negozi di robot","locality":"Bologna",' \
        '"clauses":[{"shop":"robots"}]}'
    answer=asyncio.run(maps_ai.geo_chat(
        FakeProvider(raw),fixture_config(),"qwen",
        "Dove sono i negozi di robot a Bologna?"))
    assert answer["geo_status"]=="tag_filters_unavailable"
    assert answer["geo_results"]==[]


def test_v3_generic_plan_is_not_overridden_by_legacy_category_fallback(monkeypatch):
    question="Dove si trovano i ristoranti di Bologna?"
    raw=json.dumps({"intent":"tag_lookup","label":"ristoranti",
                    "locality":"Bologna","clauses":[{"amenity":"restaurant"}]})
    monkeypatch.setattr(maps_ai,"geo_indexes",
                        lambda cfg:{"items":[{"area":"demo","tags_ready":True}]})
    monkeypatch.setattr(maps_ai,"geo_tag_catalog",lambda cfg:["amenity"])
    monkeypatch.setattr(maps_ai,"geo_category_search",
                        lambda *a:pytest.fail("valid tag lookup must not downgrade"))
    called=[]
    def search(cfg,clauses,locality,limit):
        called.append((clauses,locality))
        return {"items":[],"requires_reindex":[],"indexed_areas":1,
                "tag_pairs_available":True,"locality_found":True}
    monkeypatch.setattr(maps_ai,"geo_tag_search",search)
    answer=asyncio.run(maps_ai.geo_chat(FakeProvider(raw),fixture_config(),
                                        "qwen",question))
    assert called==[([{"amenity":"restaurant"}],"Bologna")]
    assert answer["geo_status"]=="category_no_results"
