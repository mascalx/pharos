"""Embedding model *discovery* for RAG clients.

Do not conflate downloaded model inventory with a successful inference probe:
listing must have no NPU side effects.  This adapter is intentionally independent
from RAG storage and HTTP so a future independent KB module can reuse it.
"""
from __future__ import annotations

from typing import Any

_EMBEDDING_HINTS = (
    'embed', 'bge-', 'e5-', 'gte-', 'nomic', 'jina-', 'instructor',
    'minilm', 'sentence-transformer', 'text2vec', 'mxbai-',
)
_CAPABILITY_HINTS = ('embed', 'feature extraction', 'sentence similarity', 'retrieval')


def embedding_candidates(catalog: dict[str, Any], current_model: str = '') -> dict[str, Any]:
    """Return candidate metadata without invoking the model or hiding unloaded ones.

    With a native *complete* inventory, models that lack meaningful type/name
    information remain visible as unverified candidates: firmware revisions may
    use new model-family names. With standard OpenAI-compatible inventories,
    display only recognizable candidates and report inventory incompleteness.
    """
    complete = catalog.get('complete') is True
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for item in catalog.get('models', []):
        if not isinstance(item, dict):
            continue
        model_id = item.get('id')
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        kind = item.get('kind') if isinstance(item.get('kind'), str) else ''
        kind_lower = kind.casefold()
        capabilities = item.get('capabilities')
        capability_text = (' '.join(v for v in capabilities if isinstance(v, str)).casefold()
                           if isinstance(capabilities, list) else '')
        if any(hint in capability_text for hint in _CAPABILITY_HINTS):
            basis = 'capability'
        elif any(hint in kind_lower for hint in _CAPABILITY_HINTS):
            basis = 'type'
        elif any(hint in model_id.casefold() for hint in _EMBEDDING_HINTS):
            basis = 'name'
        elif complete and not kind.strip():
            basis = 'unknown'
        else:
            continue  # Explicitly classified as another type or unnamed generic entry
        seen.add(model_id)
        entries.append({
            'id': model_id,
            'loaded': item.get('loaded') if type(item.get('loaded')) is bool else None,
            'downloaded': item.get('downloaded') if type(item.get('downloaded')) is bool else None,
            'kind': kind,
            'candidate_basis': basis,
            'configured_only': False,
        })
    # Preserve the configured model without implying that it is installed or usable.
    if current_model and current_model not in seen:
        entries.insert(0, {'id': current_model, 'loaded': None, 'downloaded': None,
                           'kind': '', 'candidate_basis': 'configured', 'configured_only': True})
    return {
        'models': [entry['id'] for entry in entries],  # dev9 compatibility
        'items': entries,
        'catalog_complete': complete,
        'catalog_source': catalog.get('source', 'unknown'),
        'catalog_reason': catalog.get('reason'),
        'catalog_error_code': catalog.get('error_code'),
        'catalog_management_error_code': catalog.get('management_error_code'),
        'mode': catalog.get('mode'),
        'detection': 'catalog_candidate_probe_on_demand',
    }
