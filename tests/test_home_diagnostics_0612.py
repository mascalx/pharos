"""Regressions: truthful Tiiny health and compact Home editing controls."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.ai import GatewayError
from pharos.config import load_config
from pharos.tiiny_core import CoreAI
from pharos.ai import Gateway


def _direct(prepared):
    path=Path(prepared)
    raw=path.read_text(encoding='utf-8')
    raw=raw.replace('enabled=false\nprovider="openai_compatible"',
                    'enabled=true\nprovider="tiiny_direct"')
    path.write_text(raw,encoding='utf-8')
    return load_config(path)


def test_status_direct_uses_openai_fallback_when_native_catalog_busy(prepared,monkeypatch):
    cfg=_direct(prepared)
    hits=[]
    async def busy_native(self, timeout_seconds=None):
        hits.append('native')
        raise GatewayError('tiiny_native_api_unavailable')
    async def openai_model_list(self,timeout_seconds=None):
        hits.append('openai')
        return ['qwen-chat']
    monkeypatch.setattr(CoreAI,'models',busy_native)
    monkeypatch.setattr(Gateway,'models',openai_model_list)
    with TestClient(create_app(cfg)) as client:
        response=client.get('/api/status')
        assert response.status_code==200,response.text
        assert response.json()['ai']=='available'
        assert response.json()['ai_reason'] is None
        assert hits==['native','openai']


def test_status_direct_is_inconclusive_when_both_catalogs_fail(prepared,monkeypatch):
    cfg=_direct(prepared)
    async def broken(self,timeout_seconds=None):
        raise GatewayError('gateway_unavailable')
    monkeypatch.setattr(CoreAI,'models',broken)
    monkeypatch.setattr(Gateway,'models',broken)
    with TestClient(create_app(cfg)) as client:
        status=client.get('/api/status').json()
        assert status['ai']=='unknown'
        assert status['ai_reason']=='ai_status_not_verified'


def test_home_logo_tile_geometry_and_edit_position():
    from pharos.app import WEB
    html=(WEB/'index.html').read_text(encoding='utf-8')
    css=(WEB/'layout-dev2.css').read_text(encoding='utf-8')
    base=(WEB/'style.css').read_text(encoding='utf-8')
    assert 'class="home-intro-image"' not in html
    assert html.index('class="home-footer"') < html.index('class="home-edit-bar"')
    assert 'grid-template-columns:repeat(auto-fill' in css
    assert 'grid-auto-rows:1fr' in css
    assert 'justify-content:flex-end' in base
    assert 'right:1%;top:5px;' in css
    assert 'width:98%;height:23px;' in css


def test_auth_failure_is_a_real_error(prepared,monkeypatch):
    cfg=_direct(prepared)
    async def unavailable(self,timeout_seconds=None):
        raise GatewayError('gateway_auth_failed')
    monkeypatch.setattr(CoreAI,'models',unavailable)
    monkeypatch.setattr(Gateway,'models',unavailable)
    with TestClient(create_app(cfg)) as client:
        status=client.get('/api/status').json()
        assert status['ai']=='unavailable'
        assert status['ai_reason']=='gateway_auth_failed'


def test_status_gateway_timeout_is_unverified_not_false_red(prepared,monkeypatch):
    """A non-direct provider model-list timeout is inconclusive, not a chat failure."""
    path=Path(prepared)
    raw=path.read_text(encoding='utf-8').replace('enabled=false\nprovider="openai_compatible"',
                                                   'enabled=true\nprovider="openai_compatible"')
    path.write_text(raw,encoding='utf-8')
    cfg=load_config(path)
    async def models_fail(self,timeout_seconds=None):
        raise GatewayError('gateway_unavailable')
    monkeypatch.setattr(CoreAI,'models',models_fail)
    with TestClient(create_app(cfg)) as client:
        status=client.get('/api/status').json()
        assert status['ai']=='unknown'
        assert status['ai_reason']=='ai_status_not_verified'


def test_status_preserves_recent_positive_probe(prepared,monkeypatch):
    """Short inventory glitches after a real success must not flash a red indicator."""
    cfg=_direct(prepared)
    calls={'count':0}
    async def sometimes_busy(self,timeout_seconds=None):
        calls['count']+=1
        if calls['count']==1:
            return ['qwen-chat']
        raise GatewayError('gateway_unavailable')
    async def fallback_busy(self,timeout_seconds=None):
        raise GatewayError('gateway_unavailable')
    monkeypatch.setattr(CoreAI,'models',sometimes_busy)
    monkeypatch.setattr(Gateway,'models',fallback_busy)
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/status').json()['ai']=='available'
        again=client.get('/api/status').json()
        assert again['ai']=='available'
        assert again['ai_reason']=='ai_recently_verified'


def test_ai_status_distinguishes_reachable_service_from_unverified_models(prepared,monkeypatch):
    """Reachable HTTP port does not falsely imply that inference or models work."""
    cfg=_direct(prepared)
    async def models_fail(self,timeout_seconds=None):
        raise GatewayError('gateway_unavailable')
    async def socket_online(_url):
        return True
    monkeypatch.setattr(CoreAI,'models',models_fail)
    monkeypatch.setattr(Gateway,'models',models_fail)
    monkeypatch.setattr('pharos.app._ai_upstream_reachable',socket_online)
    with TestClient(create_app(cfg)) as client:
        result=client.get('/api/status').json()
        assert result['ai']=='reachable'
        assert result['ai_reason']=='ai_endpoint_reachable'


def test_ai_state_colors_are_explicit_and_kb_controls_separated():
    from pharos.app import WEB
    css=(WEB/'layout-dev2.css').read_text(encoding='utf-8')
    js=(WEB/'app.js').read_text(encoding='utf-8')
    kiwix=(WEB/'kiwix.js').read_text(encoding='utf-8')
    assert '.health-row strong.ok' in css
    assert '.health-row strong.bad' in css
    assert "ai === 'reachable'" in js
    assert "book.url?'a':'strong'" in kiwix
    assert 'kiwix-delete-btn{margin-left:auto}' in css
