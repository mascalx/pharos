"""Parse the model inventory exposed by Tiiny Gateway, not TiinyOS.

Endpoint: GET /gateway/v1/models, as documented by Tiiny Gateway SDK
0.3.4.7 (`TiinyGateway.model_status` and `AsyncTiinyGateway.model_status`).
A management inventory includes locally downloaded and NPU-running models.
The public /v1/models is NOT a reliable replacement for this inventory.

Pure parsing lives outside the HTTP adapter so RAG and future modules can reuse
it. Unknown JSON shapes fail closed, never pretending to be a complete catalog.
"""
from __future__ import annotations

import math
from typing import Any

class GatewayInventoryError(ValueError):
    pass

_RUNNING = {'running', 'loaded', 'active', 'serving'}
_INSTALLED = _RUNNING | {'downloaded', 'installed', 'stopped', 'unloaded', 'idle'}
_INCOMPLETE = {'downloading', 'download_pending', 'download_failed', 'failed', 'deleted', 'deleting', 'not_downloaded', 'not_installed'}


def _identifier(obj: Any) -> str | None:
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, dict):
        for key in ('id', 'model_id', 'model', 'fullname', 'name'):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_gateway_inventory(payload: Any) -> dict:
    """Normalize the Gateway's *installed* model list into PHAROS catalog shape.

    The observed Gateway contract uses a dict containing `models` and `running`.
    `models` is authoritative for local inventory; a `running` array is
    authoritative for NPU residency. Other diagnostic fields are not exposed.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
        raise GatewayInventoryError('gateway_inventory_invalid')
    models = payload['models']
    if len(models) > 10000:
        raise GatewayInventoryError('gateway_inventory_invalid')
    active_ids: set[str] | None = None
    if 'running' in payload:
        running = payload['running']
        if not isinstance(running, list):
            raise GatewayInventoryError('gateway_inventory_invalid')
        active_ids = set()
        for model in running:
            mid = _identifier(model)
            if not mid:
                # Unknown running shape: do not assert residency from it.
                active_ids = None
                break
            active_ids.add(mid)

    result: dict[str, dict] = {}
    residency_verified = True
    for entry in models:
        if not isinstance(entry, dict):
            raise GatewayInventoryError('gateway_inventory_invalid')
        model_id = _identifier(entry)
        if not model_id or len(model_id) > 256:
            raise GatewayInventoryError('gateway_inventory_invalid')
        status = str(entry.get('status') or entry.get('state') or '').strip().lower()
        if status in _INCOMPLETE:
            continue
        explicit_downloaded = entry.get('downloaded')
        if type(explicit_downloaded) is bool:
            downloaded: bool | None = explicit_downloaded
        elif status in _INSTALLED:
            downloaded = True
        else:
            downloaded = None
        if downloaded is False:
            continue

        if active_ids is not None:
            loaded: bool | None = model_id in active_ids
        elif type(entry.get('loaded')) is bool:
            loaded = entry['loaded']
        elif type(entry.get('in_npu')) is bool:
            loaded = entry['in_npu']
        elif status in _RUNNING:
            loaded = True
        elif status in {'downloaded', 'installed', 'stopped', 'unloaded', 'idle'}:
            loaded = False
        else:
            loaded = None
        if loaded is None:
            residency_verified = False
        if loaded is True:
            downloaded = True

        kind = entry.get('type') or entry.get('kind') or ''
        kind = str(kind)[:80] if kind is not None else ''
        capabilities = ([cap[:80] for cap in entry.get('capabilities', [])
                         if isinstance(cap, str)]
                        if isinstance(entry.get('capabilities'), list) else [])[:30]
        if not kind:
            kind = ', '.join(capabilities)[:80]
        value = entry.get('npu_usage')
        npu_usage = float(value) if type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 100 else None
        description = entry.get('desc') or entry.get('description') or ''
        description = str(description)[:500]
        result[model_id] = {
            'id': model_id, 'loaded': loaded, 'downloaded': downloaded,
            'status': status or 'unknown', 'kind': kind,
            'capabilities': capabilities, 'npu_usage': npu_usage, 'description': description,
        }

    return {
        'mode': 'tiiny_gateway', 'source': 'tiiny_gateway_management',
        'complete': True, 'residency_verified': residency_verified,
        'models': list(result.values()),
    }
