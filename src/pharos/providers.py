"""Provider routing contract for Core chat and optional module integrations.

The Core starts with one OpenAI-compatible adapter. Future modules register their
own adapters without replacing global AI credentials or changing the chat API.
A separate embedding route is deliberately available for Knowledge Base/RAG.
"""
from __future__ import annotations

from typing import Protocol

from .ai import GatewayError


class Provider(Protocol):
    async def models(self, timeout_seconds: float | None = None) -> list[str]: ...
    async def complete(self, model: str, messages: list[dict], thinking: bool | None = None) -> str: ...
    async def embeddings(self, model: str, texts: list[str]) -> list[list[float]]: ...
    async def complete_tools(self, model: str, messages: list[dict], tools: list[dict], thinking: bool | None = None, *, tool_choice: str = "auto") -> dict: ...


class ProviderRegistry:
    """Single-process registry with explicit routing; no automatic failover.

    This revision exposes only the built-in standard provider in the application.
    A module loader will later register providers via register().
    """

    def __init__(self, standard: Provider):
        self._providers: dict[str, Provider] = {'standard': standard}
        self._chat_id = 'standard'
        self._embedding_id = 'standard'

    def register(self, provider_id: str, provider: Provider) -> None:
        if not provider_id or provider_id == 'standard' or not provider_id.isascii() or not all(c.isalnum() or c in '_-' for c in provider_id):
            raise ValueError('invalid_provider_id')
        if provider_id in self._providers:
            raise ValueError('provider_already_registered')
        self._providers[provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        if provider_id == 'standard':
            raise ValueError('cannot_remove_standard_provider')
        if provider_id == self._chat_id or provider_id == self._embedding_id:
            raise ValueError('provider_in_use')
        if provider_id not in self._providers:
            raise ValueError('provider_not_found')
        del self._providers[provider_id]

    def select(self, *, chat: str | None = None, embeddings: str | None = None) -> None:
        if chat is not None and chat not in self._providers:
            raise ValueError('provider_not_found')
        if embeddings is not None and embeddings not in self._providers:
            raise ValueError('provider_not_found')
        if chat is not None:
            self._chat_id = chat
        if embeddings is not None:
            self._embedding_id = embeddings

    @property
    def active_chat(self) -> str:
        return self._chat_id

    @property
    def active_embeddings(self) -> str:
        return self._embedding_id

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(self._providers)

    async def models(self, timeout_seconds: float | None = None) -> list[str]:
        return await self._providers[self._chat_id].models(timeout_seconds=timeout_seconds)

    async def complete(self, model: str, messages: list[dict], thinking: bool | None = None) -> str:
        provider = self._providers[self._chat_id]
        return (await provider.complete(model, messages, thinking=thinking)
                if thinking is not None else await provider.complete(model, messages))

    async def complete_tools(self, model: str, messages: list[dict], tools: list[dict],
                             thinking: bool | None = None, *, tool_choice: str = "auto") -> dict:
        provider = self._providers[self._chat_id]
        method = getattr(provider, "complete_tools", None)
        if method is None:
            raise GatewayError("tool_api_unsupported")
        return await method(model, messages, tools, thinking=thinking, tool_choice=tool_choice)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return await self._providers[self._embedding_id].embeddings(model, texts)
