from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from pharos.ai import Gateway, GatewayError
from pharos.config import AIConfig


def config():
    return AIConfig(enabled=True, provider='openai_compatible', base_url='http://gateway.local:9876',default_model='',request_timeout_seconds=5,max_output_tokens=100)


def test_gateway_requests_openai_compatible(monkeypatch):
    requests=[]
    def handle(request: httpx.Request):
        requests.append(request)
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data':[{'id':'qwen-test'}]})
        if request.url.path == '/v1/chat/completions':
            assert request.headers['authorization'] == 'Bearer secret'
            assert request.read()
            return httpx.Response(200, json={'choices':[{'message':{'content':'ciao'}}]})
        return httpx.Response(404)
    transport=httpx.MockTransport(handle)
    class FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs['transport']=transport
            super().__init__(**kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    gateway=Gateway(config(), 'secret')
    async def check():
        assert await gateway.models() == ['qwen-test']
        assert await gateway.complete('qwen-test',[{'role':'user','content':'ciao'}]) == 'ciao'
    asyncio.run(check())
    assert [r.url.path for r in requests] == ['/v1/models','/v1/chat/completions']


def test_gateway_unavailable(monkeypatch):
    def handle(request): return httpx.Response(503)
    transport=httpx.MockTransport(handle)
    class FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    async def check():
        with pytest.raises(GatewayError, match='gateway_unavailable'):
            await Gateway(config()).models()
    asyncio.run(check())
