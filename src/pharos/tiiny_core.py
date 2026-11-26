"""Native Tiiny control plane and single-process FIFO inference coordination.

No cloud calls, no automatic model file deletion. All device addresses and secrets
are supplied by the validated PHAROS configuration. The native catalog is NOT
replaced by the incomplete OpenAI-compatible /v1/models response.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar
from urllib.parse import quote

import httpx

from .ai import Gateway, GatewayError, inference_priority, INTERACTIVE_PRIORITY
from .config import AIConfig
from .gateway_inventory import GatewayInventoryError, parse_gateway_inventory

log = logging.getLogger(__name__)
T = TypeVar('T')
_LOADED = {'running', 'loaded', 'ready', 'active', 'serving'}
_DOWNLOADED = _LOADED | {'downloaded', 'installed', 'stopped', 'idle', 'unloaded'}


class FIFOCoordinator:
    """Priority then FIFO scheduler for Tiiny Direct, 0 highest priority.

    Existing callers without priority are interactive. Low-priority embedding
    batches yield to waiting chat at the next scheduling boundary; a running
    inference cannot be interrupted. The historical class name is retained for
    compatibility with callers and tests.
    """
    def __init__(self, limit: int = 16, wait_seconds: float = 900):
        self._limit = limit
        self._wait_seconds = wait_seconds
        self._waiting = 0
        self._active = False
        self._sequence = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []

    def status(self) -> dict:
        return {'active': self._active, 'waiting': self._waiting,
                'capacity': self._limit, 'ordering': 'priority_fifo', 'scope': 'single_process'}

    def _dispatch(self):
        if self._active:
            return
        while self._waiters:
            priority, sequence, future = min(self._waiters, key=lambda item: (item[0], item[1]))
            self._waiters.remove((priority, sequence, future))
            if future.done():
                continue
            self._active = True
            self._waiting -= 1
            future.set_result(None)
            return

    async def run(self, operation: Callable[[], Awaitable[T]], priority: int = INTERACTIVE_PRIORITY) -> T:
        if type(priority) is not int or not 0 <= priority <= 100:
            raise GatewayError('ai_priority_invalid')
        if self._waiting + int(self._active) >= self._limit:
            raise GatewayError('ai_queue_full')
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._sequence += 1
        self._waiters.append((priority, self._sequence, future))
        self._waiting += 1
        self._dispatch()
        acquired = False
        try:
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=self._wait_seconds)
            except asyncio.TimeoutError as exc:
                raise GatewayError('ai_queue_timeout') from exc
            acquired = True
            return await operation()
        finally:
            if acquired or (future.done() and not future.cancelled()):
                # Cancellation may strike *after* dispatch but before the
                # waiting coroutine resumes; the granted slot still needs release.
                self._active = False
            else:
                self._waiters = [item for item in self._waiters if item[2] is not future]
                self._waiting -= 1
                future.cancel()
            self._dispatch()


@dataclass(frozen=True)
class NativeModel:
    id: str
    loaded: bool | None
    downloaded: bool
    status: str
    kind: str
    npu_usage: float | None
    description: str

    def as_dict(self) -> dict:
        return {'id': self.id, 'loaded': self.loaded, 'downloaded': self.downloaded,
                'status': self.status, 'kind': self.kind, 'npu_usage': self.npu_usage,
                'description': self.description}


class TiinyNative:
    """TiinyOS native model management. Requires a Tiiny device URL, not /v1.

    The /api/v1/models/ and /api/v1/models/running shapes vary between firmware
    versions; unknown shapes fail closed instead of reporting an incomplete list.
    """
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 15):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _client(self) -> httpx.AsyncClient:
        headers = {'Accept': 'application/json'}
        if self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds), trust_env=False,
                                 follow_redirects=False, headers=headers)

    @staticmethod
    def _records(value: Any, *, names: tuple[str, ...]) -> list[dict]:
        if isinstance(value, list):
            if not all(isinstance(x, dict) for x in value):
                raise GatewayError('tiiny_catalog_invalid')
            return value
        if isinstance(value, dict):
            for field in names:
                if field in value:
                    return TiinyNative._records(value[field], names=names)
        raise GatewayError('tiiny_catalog_invalid')

    async def _request(self, method: str, path: str, *, body: dict | None = None) -> Any:
        try:
            async with self._client() as client:
                response = await client.request(method, self.base_url + path, json=body)
            if response.status_code in (401, 403):
                raise GatewayError('gateway_auth_failed')
            if response.status_code == 404:
                raise GatewayError('tiiny_native_api_unavailable')
            response.raise_for_status()
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning('Tiiny native %s failed: %s', method, type(exc).__name__)
            raise GatewayError('tiiny_native_unavailable') from exc

    async def catalog(self) -> list[NativeModel]:
        raw = await self._request('GET', '/api/v1/models/?including_downloading=false')
        entries = self._records(raw, names=('models', 'data', 'items', 'results'))
        # The running endpoint is a supplementary source: older firmware may
        # encode residency directly in the main catalog. A missing running API
        # must not silently erase loaded states already reported there.
        running_ids: set[str] | None = None
        try:
            running_raw = await self._request('GET', '/api/v1/models/running')
            running_entries = self._records(running_raw, names=('models', 'data', 'items', 'running', 'results'))
            if any(not (x.get('model_id') or x.get('id') or x.get('name')) for x in running_entries):
                raise GatewayError('tiiny_running_status_invalid')
            running_ids = {str(x.get('model_id') or x.get('id') or x.get('name'))
                           for x in running_entries}
        except GatewayError:
            log.warning('Tiiny running-model endpoint unavailable; residency may be unknown')
        found: dict[str, NativeModel] = {}
        for entry in entries:
            name = entry.get('model_id') or entry.get('id') or entry.get('name')
            if not isinstance(name, str) or not name or len(name) > 256:
                raise GatewayError('tiiny_catalog_invalid')
            status = str(entry.get('status') or entry.get('state') or '').lower()
            # The native catalog endpoint is an inventory of installed models.
            # Download-in-progress is filtered at the request level, then also
            # filtered here to avoid advertising an incomplete download.
            if status in {'downloading', 'download_pending', 'failed', 'deleted'}:
                continue
            if running_ids is not None:
                # The running endpoint is authoritative for NPU residency.
                # "ready" in the inventory can mean download-ready, not NPU-ready.
                loaded = name in running_ids
            elif entry.get('loaded') is True or status in _LOADED:
                loaded = True
            elif entry.get('loaded') is False or status in {'stopped', 'unloaded'}:
                loaded = False
            else:
                loaded = None  # installed inventory is valid; NPU residency is unverified
            kind = str(entry.get('type') or entry.get('kind') or entry.get('capability') or '')
            value = entry.get('npu_usage')
            units = float(value) if type(value) in (float, int) and 0 <= value <= 100 else None
            description = str(entry.get('desc') or entry.get('description') or '')[:500]
            found[name] = NativeModel(name, loaded, True, status or ('running' if loaded else 'downloaded'),
                                      kind[:80], units, description)
        return list(found.values())

    async def action(self, model_id: str, operation: str) -> None:
        if operation not in ('launch', 'stop'):
            raise ValueError('invalid_tiiny_action')
        await self._request('POST', '/api/v1/models/' + quote(model_id, safe='') + '/' + operation,
                            body={'model_id': model_id} if operation == 'launch' else None)


class CoreAI:
    """Built-in provider router; no dependency on the optional module manager."""
    def __init__(self, config: AIConfig, api_key: str = '', management_api_key: str = ''):
        self.config = config
        self.gateway = Gateway(config, api_key)
        self._management_api_key = management_api_key
        self.fifo = FIFOCoordinator(limit=config.queue_limit,
                                    wait_seconds=float(config.request_timeout_seconds + config.npu_load_timeout_seconds))
        # Architectural boundary: an external Tiiny Gateway already knows the
        # device address.  Never use the legacy management_url for direct
        # TiinyOS calls when provider=tiiny_gateway (even if it remains in TOML).
        self._native = (TiinyNative(config.base_url, api_key)
                        if config.provider == 'tiiny_direct' else None)

    def apply_live_config(self, updated: AIConfig, *, api_key: str | None = None,
                          provider_live: bool = False) -> None:
        """Apply a prevalidated idle route change without replacing the shared FIFO."""
        route_changed = (updated.provider != self.config.provider or
                         updated.base_url != self.config.base_url)
        if route_changed:
            if not provider_live and (updated.provider != self.config.provider or
                                       self.config.provider != 'tiiny_direct'):
                raise ValueError('ai_provider_change_requires_restart')
            if self.fifo._active or self.fifo._waiting:
                raise ValueError('ai_queue_busy')
        key = self.gateway.api_key if api_key is None else api_key
        if route_changed or api_key is not None:
            self._native = (TiinyNative(updated.base_url, key)
                            if updated.provider == 'tiiny_direct' else None)
        self.gateway.config = updated
        self.gateway.api_key = key
        self.fifo._limit = updated.queue_limit
        self.fifo._wait_seconds = float(updated.request_timeout_seconds + updated.npu_load_timeout_seconds)
        self.config = updated

    @property
    def api_key(self) -> str:
        return self.gateway.api_key

    @api_key.setter
    def api_key(self, key: str) -> None:
        self.gateway.api_key = key
        if self.is_direct and self._native:
            self._native.api_key = key

    def management_key(self, key: str) -> None:
        # Retained for existing secrets and API compatibility. The legacy
        # separate native key is intentionally not used by gateway mode.
        self._management_api_key = key

    @property
    def is_direct(self) -> bool:
        return self.config.provider == 'tiiny_direct'

    def queue_status(self) -> dict:
        return {'mode': self.config.provider, **self.fifo.status(),
                'enabled': self.config.enabled and self._native is not None}

    async def _gateway_catalog(self) -> dict:
        """Query the extended Gateway API, never TiinyOS, with live AI credentials."""
        try:
            async with self.gateway._client() as client:
                response = await client.get(self.config.base_url + '/gateway/v1/models')
                if response.status_code in (401, 403):
                    raise GatewayError('gateway_inventory_auth_failed')
                if response.status_code == 404:
                    raise GatewayError('gateway_inventory_not_supported')
                response.raise_for_status()
                return parse_gateway_inventory(response.json())
        except GatewayInventoryError as exc:
            raise GatewayError('gateway_inventory_invalid') from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning('Tiiny Gateway extended inventory unavailable: %s', type(exc).__name__)
            raise GatewayError('gateway_inventory_unavailable') from exc

    async def catalog(self) -> dict:
        if not self.config.enabled:
            return {'mode': self.config.provider, 'complete': False, 'models': [], 'reason': 'gateway_disabled'}
        if self._native is not None:
            items = await self._native.catalog()
            return {'mode': self.config.provider, 'complete': True, 'source': 'tiiny_native',
                    'residency_verified': all(item.loaded is not None for item in items),
                    'models': [item.as_dict() for item in items]}
        if self.config.provider == 'tiiny_gateway':
            try:
                return await self._gateway_catalog()
            except GatewayError as exc:
                # Older gateways or incompatible management responses still get
                # the standard catalog as a *marked partial* fallback only.
                management_error = str(exc)
                log.warning('Gateway extended catalog not usable (%s); trying standard models', management_error)
            try:
                names = await self.gateway.models()
            except GatewayError as exc:
                log.warning('Gateway standard model list also unavailable: %s', str(exc))
                return {'mode': 'tiiny_gateway', 'complete': False,
                        'source': 'openai_compatible', 'reason': 'gateway_models_unavailable',
                        'error_code': str(exc), 'management_error_code': management_error,
                        'models': []}
            return {'mode': 'tiiny_gateway', 'complete': False,
                    'source': 'openai_compatible', 'reason': 'gateway_catalog_not_exposed',
                    'management_error_code': management_error,
                    'models': [{'id': name, 'loaded': None, 'downloaded': None,
                                'status': 'unknown', 'kind': '', 'npu_usage': None,
                                'description': ''} for name in names]}
        # OpenAI-compatible mode intentionally does NOT call Tiiny management APIs.
        names = await self.gateway.models()
        return {'mode': self.config.provider, 'complete': False,
                'source': 'openai_compatible', 'reason': 'generic_provider',
                'models': [{'id': name, 'loaded': None, 'downloaded': None,
                            'status': 'unknown', 'kind': '', 'npu_usage': None,
                            'description': ''} for name in names]}

    async def models(self, timeout_seconds: float | None = None) -> list[str]:
        if not self.config.enabled:
            return []
        if self.is_direct:
            if self._native is None:
                raise GatewayError('tiiny_native_api_unavailable')
            return [m.id for m in await self._native.catalog()]
        return await self.gateway.models(timeout_seconds=timeout_seconds)

    async def _ensure_model(self, model_id: str) -> None:
        if self._native is None:
            raise GatewayError('tiiny_native_api_unavailable')
        records = await self._native.catalog()
        model = next((m for m in records if m.id == model_id), None)
        if model is None:
            raise GatewayError('tiiny_model_not_installed')
        if model.loaded is True:
            return
        if model.loaded is None:
            raise GatewayError('tiiny_residency_unverified')
        if self.config.npu_policy == 'manual':
            raise GatewayError('tiiny_model_not_loaded')
        # The managed policy deliberately does NOT evict: safe eviction will
        # require firmware-verified free-capacity and unload-candidate fields.
        await self._native.action(model_id, 'launch')
        for _ in range(self.config.npu_load_timeout_seconds):
            await asyncio.sleep(1)
            models = await self._native.catalog()
            if any(m.id == model_id and m.loaded for m in models):
                return
        raise GatewayError('tiiny_model_load_timeout')

    async def load(self, model_id: str) -> dict:
        if not self.is_direct or not self.config.enabled:
            raise GatewayError('tiiny_direct_required')
        async def work() -> dict:
            assert self._native is not None
            installed = await self._native.catalog()
            if not any(m.id == model_id for m in installed):
                raise GatewayError('tiiny_model_not_installed')
            if not any(m.id == model_id and m.loaded for m in installed):
                await self._native.action(model_id, 'launch')
            return {'model': model_id, 'requested': 'load', 'status': 'loading_or_loaded'}
        return await self.fifo.run(work, priority=inference_priority())

    async def unload(self, model_id: str) -> dict:
        if not self.is_direct or not self.config.enabled:
            raise GatewayError('tiiny_direct_required')
        async def work() -> dict:
            assert self._native is not None
            records = await self._native.catalog()
            if not any(m.id == model_id for m in records):
                raise GatewayError('tiiny_model_not_installed')
            if any(m.id == model_id and m.loaded for m in records):
                await self._native.action(model_id, 'stop')
            return {'model': model_id, 'requested': 'unload', 'status': 'unloading_or_unloaded'}
        return await self.fifo.run(work, priority=inference_priority())

    async def complete(self, model: str, messages: list[dict], thinking: bool | None = None) -> str:
        # External gateways own their own scheduling and NPU management.
        # The optional Qwen chat-template flag is passed through only on request.
        async def generate() -> str:
            return (await self.gateway.complete(model, messages, thinking=thinking)
                    if thinking is not None else await self.gateway.complete(model, messages))
        if not self.is_direct:
            return await generate()
        async def work() -> str:
            await self._ensure_model(model)
            return await generate()
        return await self.fifo.run(work, priority=inference_priority())

    async def complete_tools(self, model: str, messages: list[dict], tools: list[dict],
                             thinking: bool | None = None, *, tool_choice: str = "auto") -> dict:
        async def generate():
            return await self.gateway.complete_tools(model, messages, tools, thinking=thinking, tool_choice=tool_choice)
        if not self.is_direct:
            return await generate()
        async def work():
            await self._ensure_model(model)
            return await generate()
        return await self.fifo.run(work, priority=inference_priority())

    async def embeddings(self, model: str, texts: list[str]) -> list[list[float]]:
        if not self.is_direct:
            return await self.gateway.embeddings(model, texts)
        async def work() -> list[list[float]]:
            await self._ensure_model(model)
            return await self.gateway.embeddings(model, texts)
        return await self.fifo.run(work, priority=inference_priority())
