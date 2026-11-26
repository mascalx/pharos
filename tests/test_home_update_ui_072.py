"""Regression: migrated Home preferences, update marker and UI compatibility."""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

import pytest

from pharos.home import FEATURES, home_preferences, save_home_preferences
from pharos.update_check import UpdateChecker

OLD="a"*40
NEW="b"*40


def test_new_home_tile_preserves_existing_order_and_visibility(prepared):
    import tomlkit
    from pathlib import Path

    config=prepared
    raw=tomlkit.parse(config.read_text())
    earlier=["notifications","settings","chat","diagnostics","knowledge","kiwix"]
    raw["home"]={"order":earlier,"hidden":["chat","kiwix"]}
    config.write_text(tomlkit.dumps(raw))
    result=home_preferences(config)
    assert result["order"] == earlier+["translations","maps"]
    assert result["hidden"] == ["chat","kiwix"]
    result["order"].insert(0,result["order"].pop())
    result["hidden"].append("translations")
    saved=save_home_preferences(config,result)
    assert saved==home_preferences(config)
    assert saved["order"][0]=="maps"
    assert saved["hidden"]==["chat","kiwix","translations"]


def test_five_tile_preferences_also_migrate(prepared):
    import tomlkit
    raw=tomlkit.parse(prepared.read_text())
    order=["settings","chat","knowledge","kiwix","diagnostics"]
    raw["home"]={"order":order,"hidden":["settings"]}
    prepared.write_text(tomlkit.dumps(raw))
    assert home_preferences(prepared)=={
        "order":order+["notifications","translations","maps"],"hidden":["settings"]}


def test_stale_update_flag_clears_on_local_marker_change(tmp_path):
    async def execute():
        checkout=tmp_path/"checkout"
        checkout.mkdir()
        marker=tmp_path/"update-source.json"
        marker.write_text(json.dumps({"checkout":str(checkout),"installed_sha":OLD}))
        checker=UpdateChecker(SimpleNamespace(updates=SimpleNamespace(
            enabled=False,check_interval_hours=24)),marker)
        checker.state.update({
            "state":"available","available":True,"installed_sha":OLD,
            "remote_sha":NEW,"compare_url":"https://github.com/mascalx/pharos/compare/"+OLD+"...main",
            "last_checked":1234})
        marker.write_text(json.dumps({"checkout":str(checkout),"installed_sha":NEW}))
        state=checker.snapshot()
        assert state["state"]=="current"
        assert state["installed_sha"]==NEW
        assert state["available"] is False
        assert state["compare_url"]==""
        assert state["last_checked"] is None
        await checker.stop()
    asyncio.run(execute())


def test_translation_styles_match_existing_settings_palette():
    from pathlib import Path
    css=(Path(__file__).resolve().parents[1]/"src/pharos/web/layout-dev2.css").read_text()
    assert ".shell #view-translations" in css
    assert "background:#faebdc;color:#201b18;border:1px solid #d5b499" in css
    assert ".shell .settings-field select" in css
