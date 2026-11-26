"""Locale packs are data-only, independent of Git checkout and editable via UI."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.locales import LocaleError, available, get

HEAD={"X-Pharos-Request":"1"}


def test_catalog_builtin_and_fallback(prepared):
    cfg=load_config(prepared)
    codes={lang["code"] for lang in available(cfg.storage.data_dir)}
    assert {"it","en"} <= codes
    it=get(cfg.storage.data_dir,"it")
    en=get(cfg.storage.data_dir,"en")
    assert it["messages"]["homeWelcome"] != en["messages"]["homeWelcome"]
    assert len(en["messages"]) >= 350


def test_translation_editor_persists_custom_file_and_selects_it_live(prepared):
    config=load_config(prepared)
    with TestClient(create_app(config)) as client:
        catalog=client.get("/api/locales").json()
        assert catalog["fallback"]=="en"
        assert {v["code"] for v in catalog["items"]}=={"it","en"}
        assert client.get("/api/locales/en/edit").json()["builtin"] is True
        assert client.put("/api/locales/fr",json={
            "code":"fr","name":"Français","messages":{"homeWelcome":"Bienvenue"},
            "revision":None
        }).status_code==403

        r=client.put("/api/locales/fr",headers=HEAD,json={
            "code":"fr","name":"Français","messages":{"homeWelcome":"Bienvenue"},
            "revision":None
        })
        assert r.status_code==200,r.text
        revision=r.json()["revision"]
        folder=config.storage.data_dir/"locales"
        assert (folder/"fr.json").is_file()
        assert (folder/"fr.json").stat().st_size < 256000
        assert client.get("/api/locales/fr").json()["messages"]["homeWelcome"]=="Bienvenue"
        assert client.get("/api/locales/fr").json()["messages"]["settings"]=="Configuration"
        assert client.get("/api/locales/fr/edit").json()["revision"]==revision
        assert "fr" in {v["code"] for v in client.get("/api/settings").json()["language_options"]}

        previous=client.get("/api/settings").json()
        previous["values"]["general"]["language"]="fr"
        change=client.put("/api/settings",headers=HEAD,json={
            "values":previous["values"],"revision":previous["revision"]})
        assert change.status_code==200,change.text
        assert change.json()["language"]=="fr"
        assert client.get("/api/config").json()["language"]=="fr"
        assert load_config(prepared).language=="fr"
        # The active language cannot be deleted, even if explicitly requested.
        assert client.delete("/api/locales/fr",headers=HEAD,params={"revision":revision}).status_code==409

        stale=client.put("/api/locales/fr",headers=HEAD,json={
            "code":"fr","name":"Français","messages":{},"revision":"a"*64})
        assert stale.status_code==409
        valid=client.put("/api/locales/fr",headers=HEAD,json={
            "code":"fr","name":"Français","messages":{"homeWelcome":"Salut"},"revision":revision})
        assert valid.status_code==200,valid.text
        current=valid.json()["revision"]
        assert current!=revision
        assert client.get("/api/locales/fr").json()["messages"]["homeWelcome"]=="Salut"

        settings=client.get("/api/settings").json()
        settings["values"]["general"]["language"]="en"
        change=client.put("/api/settings",headers=HEAD,json={
            "values":settings["values"],"revision":settings["revision"]})
        assert change.status_code==200,change.text
        assert client.delete("/api/locales/fr",headers=HEAD,
                             params={"revision":current}).status_code==200
        assert client.get("/api/locales/fr").status_code==404
    assert not (folder/"fr.json").exists()


def test_invalid_codes_keys_builtin_and_cross_origin_are_rejected(prepared):
    cfg=load_config(prepared)
    with TestClient(create_app(cfg)) as client:
        payload={"code":"en","name":"Replaced","messages":{"homeWelcome":"X"},
                 "revision":None}
        assert client.put("/api/locales/en",headers=HEAD,json=payload).status_code==422
        assert client.put("/api/locales/../../etc",headers=HEAD,json=payload).status_code in (404,422)
        payload={"code":"fr","name":"French","messages":{"unsafeKey":"X"},"revision":None}
        assert client.put("/api/locales/fr",headers=HEAD,json=payload).status_code==422
        payload["messages"]={"homeWelcome":"<script>alert(1)</script>"}
        assert client.put("/api/locales/fr",headers={**HEAD,"Origin":"https://evil.example"},
                          json=payload).status_code==403


def test_builtin_files_shipped_and_html_loads_editor():
    root=Path(__file__).resolve().parents[1]
    web=root/"src/pharos/web"
    for lang in ("it","en"):
        obj=json.loads((web/"locales"/f"{lang}.json").read_text(encoding="utf-8"))
        assert obj["code"]==lang
        assert len(obj["messages"])>350
        assert all(isinstance(s,str) for s in obj["messages"].values())
    html=(web/"index.html").read_text(encoding="utf-8")
    js=(web/"app.js").read_text(encoding="utf-8")
    assert 'id="view-translations"' in html
    assert 'src="/static/translations.js?' in html
    assert 'await window.pharosLoadLocaleCatalog()' in js
    assert 'loadLanguageBundle(' in js



def test_missing_custom_language_file_does_not_prevent_core_start(prepared):
    """The NFS storage may be unavailable after an earlier language choice."""
    cfg=load_config(prepared)
    folder=cfg.storage.data_dir/"locales"
    folder.mkdir()
    (folder/"fr.json").write_text(json.dumps({
        "code":"fr","name":"Français","messages":{"homeWelcome":"Bienvenue"}
    }),encoding="utf-8")
    prepared.write_text(prepared.read_text().replace('language="it"', 'language="fr"'),
                        encoding="utf-8")
    # Simulate a temporarily inaccessible custom language file.
    (folder/"fr.json").unlink()
    config=load_config(prepared)
    assert config.language=="fr"
    with TestClient(create_app(config)) as client:
        assert client.get("/").status_code==200
        assert client.get("/api/locales/en").status_code==200
        assert client.get("/api/locales/fr").status_code==404


def test_language_change_requires_existing_pack(prepared):
    with TestClient(create_app(load_config(prepared))) as client:
        state=client.get("/api/settings").json()
        state["values"]["general"]["language"]="zz"
        result=client.put("/api/settings",headers=HEAD,json={
            "values":state["values"],"revision":state["revision"]})
        assert result.status_code==422
        assert load_config(prepared).language=="it"
