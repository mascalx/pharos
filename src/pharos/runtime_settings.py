"""Classify persisted TOML settings as live or restart-only.

A saved TOML is always authoritative on process restart.  During runtime we
merge only independently safe fields into the immutable configuration snapshot.
Never silently redirect active databases, change listener sockets, or switch a
model/provider while an inference may be running.
"""
from __future__ import annotations

from dataclasses import fields, replace

from .config import AIConfig, Config, RAGConfig

# Switching the server/network or AI provider while operations are in flight
# is not safely atomic.  Keep the original runtime object until explicit restart.
COLD_AI = frozenset({'provider', 'base_url'})
COLD_RAG = frozenset({'enabled', 'qdrant_url'})


def live_snapshot(active: Config, persisted: Config, *, rag_active: bool,
                  direct_url_live: bool = False, provider_live: bool = False) -> Config:
    """Return settings safe to take effect in the running process.

    Provider/address changes may be applied live only after the caller
    verifies that complete AI operations and background jobs are idle.
    Active RAG indexes may only change their embedding model via the verified
    shadow-reindex workflow.  Before first activation, model selection can be
    staged without touching indexes.
    """
    cold_ai = frozenset() if provider_live else (COLD_AI - {'base_url'} if direct_url_live else COLD_AI)
    ai = replace(persisted.ai, **{key: getattr(active.ai, key) for key in cold_ai})
    cold_rag = set(COLD_RAG)
    if rag_active:
        cold_rag.add('embedding_model')
    rag = replace(persisted.rag, **{key: getattr(active.rag, key) for key in cold_rag})
    return replace(active, language=persisted.language, ai=ai, rag=rag,
                   kiwix=persisted.kiwix)


def pending_restart_fields(active: Config, persisted: Config) -> list[str]:
    """Compare authoritative disk values to effective running configuration."""
    pending = []
    for section in ('server', 'security', 'storage'):
        a, b = getattr(active, section), getattr(persisted, section)
        pending.extend(f'{section}.{field.name}' for field in fields(a)
                       if getattr(a, field.name) != getattr(b, field.name))
    pending.extend(f'ai.{key}' for key in sorted(COLD_AI)
                   if getattr(active.ai, key) != getattr(persisted.ai, key))
    pending.extend(f'rag.{key}' for key in ('enabled', 'qdrant_url', 'embedding_model')
                   if getattr(active.rag, key) != getattr(persisted.rag, key))
    return pending
