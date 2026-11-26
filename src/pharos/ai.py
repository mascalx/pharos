"""OpenAI-compatible inference adapter for any compatible AI service."""
from __future__ import annotations

import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from .config import AIConfig

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    pass


# Tiiny Gateway uses X-Tiiny-Priority: 0 = highest, 100 = lowest.
# Context-local priority keeps concurrent chat and ingestion requests independent.
INTERACTIVE_PRIORITY = 10
BACKGROUND_PRIORITY = 90
_AI_PRIORITY: ContextVar[int] = ContextVar('pharos_ai_priority', default=INTERACTIVE_PRIORITY)


@contextmanager
def background_inference():
    token = _AI_PRIORITY.set(BACKGROUND_PRIORITY)
    try:
        yield
    finally:
        _AI_PRIORITY.reset(token)


def inference_priority() -> int:
    return _AI_PRIORITY.get()


def supports_thinking_switch(model: str) -> bool:
    """Chat-model eligibility, not a guarantee of upstream support.

    An explicit user choice is offered on chat models; the inference server
    decides whether it supports the vendor extension. Never expose it to known
    embedding, re-ranking, ASR, image or other non-chat-only models.
    """
    tail = model.rsplit('/', 1)[-1].lower()
    return bool(tail) and not bool(re.search(
        r'(embedding|embed-text|rerank|^bge-|^nomic-embed|asr|whisper|tts|vision-only|image[-_ ]|ocr|music)', tail))


class Gateway:
    def __init__(self, config: AIConfig, api_key: str = ""):
        self.config = config
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self, timeout_seconds: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds or self.config.request_timeout_seconds), trust_env=False, follow_redirects=False, headers=self._headers())

    def _inference_headers(self) -> dict[str, str]:
        return ({'X-Tiiny-Priority': str(inference_priority())}
                if self.config.provider == 'tiiny_gateway' else {})

    async def models(self, timeout_seconds: float | None = None) -> list[str]:
        if not self.config.enabled:
            return []
        try:
            async with self._client(timeout_seconds) as client:
                response = await client.get(self.config.base_url + "/v1/models")
                if response.status_code in (401, 403):
                    raise GatewayError("gateway_auth_failed")
                response.raise_for_status()
                body = response.json()
            values = body.get("data") if isinstance(body, dict) else None
            if not isinstance(values, list):
                raise GatewayError("invalid_response")
            return [item["id"] for item in values[:100] if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]][:100]
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning("AI provider models request failed: %s", type(exc).__name__)
            raise GatewayError("gateway_unavailable") from exc

    async def embeddings(self, model: str, texts: list[str]) -> list[list[float]]:
        if not self.config.enabled:
            raise GatewayError("gateway_disabled")
        if not model or not texts or any(not x for x in texts):
            raise GatewayError("invalid_embedding_request")
        try:
            async with self._client() as client:
                response = await client.post(self.config.base_url + "/v1/embeddings",
                                             json={"model": model, "input": texts}, headers=self._inference_headers())
                if response.status_code in (401,403):
                    raise GatewayError("gateway_auth_failed")
                response.raise_for_status()
                body = response.json()
            values = body.get("data")
            if not isinstance(values, list) or len(values) != len(texts):
                raise GatewayError("invalid_embedding_response")
            if not all(isinstance(v, dict) and type(v.get("index")) is int for v in values):
                raise GatewayError("invalid_embedding_response")
            indexed = sorted(values, key=lambda v:v["index"])
            if [v["index"] for v in indexed] != list(range(len(texts))):
                raise GatewayError("invalid_embedding_response")
            vectors = [v.get("embedding") for v in indexed]
            if any(not isinstance(v,list) or not v or len(v)>8192 or
                   not all(type(x) in (int,float) and math.isfinite(x) for x in v) for v in vectors):
                raise GatewayError("invalid_embedding_response")
            if any(len(v) != len(vectors[0]) for v in vectors):
                raise GatewayError("invalid_embedding_response")
            return vectors
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning("AI provider embedding request failed: %s",type(exc).__name__)
            raise GatewayError("gateway_unavailable") from exc

    async def complete(self, model: str, messages: list[dict], thinking: bool | None = None) -> str:
        if not self.config.enabled:
            raise GatewayError("gateway_disabled")
        payload = {"model": model, "messages": messages, "stream": False, "max_tokens": self.config.max_output_tokens}
        if thinking is not None:
            if type(thinking) is not bool or not supports_thinking_switch(model):
                raise GatewayError('thinking_control_unsupported_model')
            payload['chat_template_kwargs'] = {'enable_thinking': thinking}
        try:
            async with self._client() as client:
                response = await client.post(self.config.base_url + "/v1/chat/completions", json=payload, headers=self._inference_headers())
                if response.status_code in (401, 403):
                    raise GatewayError("gateway_auth_failed")
                if thinking is not None and response.status_code in (400, 422):
                    raise GatewayError('thinking_control_upstream_rejected')
                response.raise_for_status()
                body = response.json()
            answer = body["choices"][0]["message"]["content"]
            if not isinstance(answer, str):
                raise GatewayError("invalid_response")
            if not answer.strip():
                raise GatewayError("gateway_empty_completion")
            return answer
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
            logger.warning("AI provider chat request failed: %s", type(exc).__name__)
            raise GatewayError("gateway_unavailable") from exc


    async def complete_tools(self, model: str, messages: list[dict], tools: list[dict],
                             thinking: bool | None = None, *, tool_choice: str = "auto") -> dict:
        """Structured native response; independent of text-only complete()."""
        if tool_choice not in ("auto", "required"):
            raise GatewayError("tool_choice_invalid")
        if not self.config.enabled:
            raise GatewayError("gateway_disabled")
        payload = {"model": model, "messages": messages, "tools": tools,
                   "tool_choice": tool_choice, "stream": False,
                   "max_tokens": self.config.max_output_tokens}
        if thinking is not None:
            if type(thinking) is not bool or not supports_thinking_switch(model):
                raise GatewayError("thinking_control_unsupported_model")
            payload["chat_template_kwargs"] = {"enable_thinking": thinking}
        try:
            async with self._client() as client:
                async with client.stream("POST", self.config.base_url + "/v1/chat/completions",
                                         json=payload, headers=self._inference_headers()) as response:
                    if response.status_code in (401, 403):
                        raise GatewayError("gateway_auth_failed")
                    if response.status_code in (400, 404, 405, 415, 422, 501):
                        raise GatewayError("tool_api_rejected")
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 262144:
                            raise GatewayError("tool_response_too_large")
            import json
            choice = json.loads(raw)["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise GatewayError("tool_response_invalid")
            return {"message": message, "finish_reason": choice.get("finish_reason")}
        except httpx.TimeoutException as exc:
            raise GatewayError("gateway_timeout") from exc
        except httpx.HTTPError as exc:
            raise GatewayError("gateway_unavailable") from exc
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise GatewayError("tool_response_invalid") from exc


# Backwards-compatible name: earlier builds and external integrations import Gateway.
OpenAICompatibleProvider = Gateway
