"""PHAROS web API and local UI."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import replace
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, quote

from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .ai import GatewayError, supports_thinking_switch
from .tiiny_core import CoreAI
from .embedding_catalog import embedding_candidates
from .providers import ProviderRegistry
from .config import Config, load_config
from .storage import check_data_volume
from .knowledge_contract import RAGError
from .config_edit import SettingsError, settings_values, save_settings
from .runtime_settings import live_snapshot, pending_restart_fields
from .secrets import read_secrets, write_secrets, SecretError
from .kiwix_runtime import KiwixManagedService, KiwixRuntimeError
from .download_jobs import DownloadJobs, JobError
from .knowledge_jobs import KnowledgeJobs
from .lexical_jobs import LexicalJobs
from .knowledge import RAGEngine, ReindexService
from .kiwix import make_router as kiwix_router
from .kiwix_proxy import install_kiwix_proxy
from .home import home_preferences, save_home_preferences, HomeError
from .update_check import UpdateChecker
from . import web_updater
from . import locales as locale_catalog
from .maps import router as maps_router, delete_local_map
from .waypoints import router as waypoints_router
from .maps_gpx import router as gpx_router
from .maps_download import MapJobs, REGIONS
from .maps_catalog import builds as map_builds
from .maps_areas import areas as geo_areas
from .maps_search import search as geo_search, list_indexes as geo_indexes, delete_index as geo_delete_index, GeoSearchError
from .maps_ai import geo_chat

logger = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"

async def _ai_upstream_reachable(url: str) -> bool:
    """Only establishes TCP reachability, never model/inference readiness."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, port), timeout=1.5)
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
        return True
    except (OSError, asyncio.TimeoutError, ValueError):
        return False



class SettingsPayload(BaseModel):
    values: dict
    revision: str = Field(min_length=64, max_length=64)
    api_key: str | None = Field(default=None, max_length=2048)


class LocalePayload(BaseModel):
    code: str = Field(min_length=2, max_length=5)
    name: str = Field(min_length=1, max_length=60)
    messages: dict[str, str]
    revision: str | None = Field(default=None, max_length=64)


class NewAPIKeyPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=2048)


class ZimDownloadPayload(BaseModel):
    url: str = Field(min_length=12,max_length=2000)




class ModelPayload(BaseModel):
    model: str = Field(min_length=1, max_length=256)


class ReindexPayload(ModelPayload):
    revision: str = Field(min_length=64, max_length=64)



class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class SearchPayload(BaseModel):
    kb_ids: list[str] = Field(min_length=1, max_length=5)
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=12)
    retrieval_mode: Literal['vector', 'candidate_fusion', 'hybrid_fts'] = 'vector'


class AnswerPayload(SearchPayload):
    model: str = Field(min_length=1, max_length=256)
    thinking: bool | None = None


class ChatPayload(BaseModel):
    model: str = Field(default="", max_length=256)
    thinking: bool | None = None
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    kb_ids: list[str] = Field(default_factory=list, max_length=5)
    geo_enabled: bool = False


class KBPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class DocumentNamePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=220)

class ZimLinkPayload(BaseModel):
    filename: str = Field(min_length=5,max_length=200)

class MapSelectionPayload(BaseModel):
    region_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=16, max_length=17)
    zoom: int | None = Field(default=None, ge=0, le=15)
    include_search: bool = False

class GeoIndexPayload(BaseModel):
    region_id: str = Field(min_length=1, max_length=80)
    refresh: bool = False

class StartUpdatePayload(BaseModel):
    target_sha: str = Field(pattern=r'^[0-9a-f]{40}$')

def create_app(config: Config) -> FastAPI:
    secrets = read_secrets(config.security.secrets_file)
    gateway = CoreAI(config.ai, secrets.get('ai', {}).get('api_key', ''),
                     secrets.get('ai', {}).get('management_api_key', ''))
    providers = ProviderRegistry(gateway)
    kiwix_service = KiwixManagedService(config)
    # Preserve the exact pre-existing SQLite/Qdrant naming and RAG algorithms.
    rag = RAGEngine(config, gateway) if config.rag.enabled else None
    reindex_service = ReindexService(rag) if rag else None
    activation_lock = asyncio.Lock()
    update_start_lock = asyncio.Lock()
    update_pending = False
    active_mutations = 0
    # A brief catalogue outage must not turn a recently proven AI connection
    # into a false red service failure. Never hide authentication errors.
    ai_health_last_success = 0.0
    # Shared short-lived inventory cache: the native Tiiny catalog may require
    # several slow upstream calls. Chat, Knowledge and Settings reuse one
    # completed snapshot and coalesce simultaneous discovery requests.
    # Manual Refresh always bypasses this cache.
    catalog_cache = None
    catalog_cached_at = 0.0
    catalog_lock = asyncio.Lock()
    CATALOG_TTL_SECONDS = 45
    ai_requests = 0

    def ai_busy():
        return bool(ai_requests or gateway.fifo._active or gateway.fifo._waiting
                    or (rag and rag.reindex_in_progress)
                    or (knowledge_jobs and knowledge_jobs._tasks)
                    or jobs.active_knowledge_jobs())

    async def fast_catalog(refresh: bool = False):
        nonlocal catalog_cache, catalog_cached_at
        if (not refresh and catalog_cache is not None and
                time.monotonic() - catalog_cached_at < CATALOG_TTL_SECONDS):
            return catalog_cache
        async with catalog_lock:
            if (not refresh and catalog_cache is not None and
                    time.monotonic() - catalog_cached_at < CATALOG_TTL_SECONDS):
                return catalog_cache
            # Do not publish a half-populated inventory after an exception.
            result = await gateway.catalog()
            catalog_cache = result
            catalog_cached_at = time.monotonic()
            return result

    def invalidate_catalog():
        nonlocal catalog_cache, catalog_cached_at
        catalog_cache = None
        catalog_cached_at = 0.0

    jobs = DownloadJobs(config, kiwix_service)
    map_jobs = MapJobs(config, jobs)
    update_checker = UpdateChecker(config)
    knowledge_jobs = KnowledgeJobs(config, rag, jobs) if rag else None
    lexical_jobs = LexicalJobs(rag, jobs) if rag else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Bring up a prepared Kiwix library at boot. An empty library is healthy;
        # a broken Kiwix binary must not prevent PHAROS diagnostics from loading.
        try:
            if kiwix_service.binary_installed() and check_data_volume(config.storage).available:
                try:
                    await asyncio.to_thread(kiwix_service.ensure_running, config.kiwix.server_url)
                except (KiwixRuntimeError, OSError) as exc:
                    logger.warning('Kiwix startup: %s', exc)
            if knowledge_jobs:
                knowledge_jobs.resume()
            if lexical_jobs:
                lexical_jobs.resume()
            jobs.resume()
            map_jobs.resume()
            update_checker.start()
            yield
        finally:
            await update_checker.stop()
            if knowledge_jobs:
                knowledge_jobs.stop()
            if lexical_jobs:
                lexical_jobs.stop()
            await asyncio.to_thread(map_jobs.stop)
            await asyncio.to_thread(jobs.stop)
            kiwix_service.stop()

    app = FastAPI(title='PHAROS', version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.config = config
    app.state.gateway = gateway
    app.state.providers = providers
    app.state.rag = rag
    app.state.reindex = reindex_service
    app.state.jobs = jobs
    app.state.map_jobs = map_jobs
    app.state.update_checker = update_checker
    app.state.knowledge_jobs = knowledge_jobs
    app.state.lexical_jobs = lexical_jobs
    app.state.kiwix_service = kiwix_service
    app.mount('/static', StaticFiles(directory=WEB), name='static')
    # The browser's api() helper always prefixes requests with /api/.  Kiwix
    # routes historically lived under /extensions/kiwix, leaving the browser
    # with 404s for status, books and the independent online OPDS catalog.
    # Mount one router in both locations: /api for the actual web UI, and the
    # original paths for API consumers that already use them.  Both share one
    # catalog/cache and the same local kiwix-serve supervisor.
    kiwix_routes = kiwix_router(config, kiwix_service)
    app.include_router(kiwix_routes, prefix='/api')
    app.include_router(kiwix_routes, include_in_schema=False)
    install_kiwix_proxy(app, config, kiwix_service)
    app.include_router(maps_router(config))
    app.include_router(waypoints_router(config))
    app.include_router(gpx_router(config))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # While a detached updater modifies PHAROS, do not accept new work
        # or settings mutations. Reads (including health and progress) remain
        # available across the restart.
        nonlocal ai_requests, active_mutations
        path = request.url.path
        mutating = (request.method not in ("GET", "HEAD", "OPTIONS")
                    and path.startswith(("/api/", "/extensions/kiwix/"))
                    and path not in ("/api/updates/start", "/api/updates/check"))
        if mutating and (update_pending or web_updater.maintenance()):
            return JSONResponse({"detail": "update_in_progress"}, status_code=423)
        if mutating:
            active_mutations += 1
        tracked = (path in ('/api/chat', '/api/models', '/api/status', '/api/ai/catalog',
                            '/api/embedding-models', '/api/embedding-models/probe')
                   or path.startswith(('/api/rag/', '/api/ai/models/'))
                   or (request.method not in ('GET', 'HEAD', 'OPTIONS') and path.startswith('/api/kb/')))
        if tracked:
            ai_requests += 1
        try:
            response = await call_next(request)
        finally:
            if tracked:
                ai_requests -= 1
            if mutating:
                active_mutations -= 1
        # Only the read-only Kiwix proxy may be framed by the PHAROS UI.
        # The iframe itself is sandboxed without allow-same-origin: untrusted
        # ZIM scripts cannot read the parent dashboard or its origin.
        is_kiwix = (request.url.path == "/kiwix" or
                    request.url.path.startswith("/kiwix/"))
        if is_kiwix:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' data: blob:; img-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; connect-src 'self'; frame-src 'self'; "
                "frame-ancestors 'self'; base-uri 'self'; form-action 'self'; "
                "object-src 'none'; sandbox allow-scripts allow-forms allow-downloads")
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        elif (request.url.path.startswith("/api/kb/") and
              request.url.path.endswith("/file") and
              response.headers.get("content-type", "").lower().startswith("application/pdf")):
            # Allow only a PDF file response in our restricted internal KB
            # viewer. Other KB resources continue to prohibit framing.
            # Chrome's built-in PDF viewer cannot initialize inside a
            # CSP-sandboxed document. This exception applies only to PDF
            # responses validated by the document endpoint, not HTML or
            # other user-uploaded files.
            response.headers["Content-Security-Policy"] = (
                "frame-ancestors 'self'; base-uri 'none'")
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; "
                "script-src 'self'; style-src 'self'; connect-src 'self'; "
                "worker-src 'self' blob:; frame-src 'self'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'")
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    def guarded_request(request: Request) -> None:
        """No permissive CORS. Require non-simple same-origin browser mutation."""
        if request.headers.get("X-Pharos-Request") != "1":
            raise HTTPException(status_code=403, detail="request_header_required")
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != request.headers.get("host", "").lower():
                raise HTTPException(status_code=403, detail="origin_mismatch")

    def require_knowledge() -> None:
        if rag is None:
            raise HTTPException(503, detail='rag_disabled')

    @app.get('/api/home')
    def read_home():
        return home_preferences(config.source)

    @app.put('/api/home')
    def write_home(payload: dict, request: Request):
        guarded_request(request)
        try:
            return save_home_preferences(config.source, payload)
        except HomeError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.delete('/api/maps/regions/{filename}')
    def maps_delete_downloaded_region(filename: str, request: Request):
        guarded_request(request)
        return delete_local_map(config, filename, jobs)

    @app.get('/api/maps/catalog')
    async def maps_catalog(refresh: bool = False):
        return await asyncio.to_thread(map_builds, refresh=refresh)

    @app.get('/api/maps/areas')
    async def maps_geographic_areas():
        return await asyncio.to_thread(geo_areas,
            cache_path=config.storage.database_dir / "maps-geography.json")

    @app.get('/api/maps/available-regions')
    def maps_available_regions():
        return {'items': list(REGIONS)}

    @app.post('/api/maps/downloads', status_code=202)
    def maps_new_download(payload: MapSelectionPayload, request: Request):
        guarded_request(request)
        try:
            return map_jobs.submit(payload.region_id, payload.source_id, payload.zoom,
                                   include_search=payload.include_search)
        except JobError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get('/api/maps/search/indexes')
    def maps_search_indexes():
        return geo_indexes(config)

    @app.get('/api/maps/search')
    def maps_search_local(q: str = "", limit: int = 20):
        try:
            return geo_search(config, q, limit=limit)
        except GeoSearchError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.post('/api/maps/search/indexes', status_code=202)
    def maps_new_index(payload: GeoIndexPayload, request: Request):
        guarded_request(request)
        try:
            return map_jobs.submit_index(payload.region_id, refresh=payload.refresh)
        except (JobError, GeoSearchError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.delete('/api/maps/search/indexes/{area_id}')
    def maps_delete_search_index(area_id: str, request: Request):
        guarded_request(request)
        try:
            return geo_delete_index(config, area_id, jobs)
        except GeoSearchError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get('/api/jobs')
    def get_jobs():
        return {'items': jobs.list()}

    @app.delete('/api/extensions/kiwix/zims/{filename}')
    def delete_kiwix_zim(filename: str, request: Request):
        guarded_request(request)
        if jobs.has_active_zim_download():
            raise HTTPException(409, detail='kiwix_download_in_progress')
        try:
            from .config import load_config
            return kiwix_service.delete_zim(filename, load_config(config.source).kiwix.server_url)
        except KiwixRuntimeError as exc:
            code=404 if str(exc)=='kiwix_zim_not_found' else (
                422 if str(exc)=='kiwix_zim_filename_invalid' else
                503 if str(exc)=='data_volume_unavailable' else 409)
            raise HTTPException(code, detail=str(exc)) from exc

    @app.post('/api/jobs/kiwix/zim', status_code=202)
    def start_zim_download(payload: ZimDownloadPayload, request: Request):
        guarded_request(request)
        try:
            return jobs.submit('download-zim', payload.url)
        except JobError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post('/api/jobs/{job_id}/cancel')
    async def cancel_download(job_id: str, request: Request):
        guarded_request(request)
        try:
            with jobs._db() as db:
                row=db.execute('SELECT kind FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row and row['kind'] in ('download-map', 'index-map'):
                return map_jobs.cancel(job_id)
            if row and row['kind']=='build-lexical':
                if lexical_jobs is None:
                    raise JobError('job_unknown')
                return lexical_jobs.cancel(job_id)
            if row and row['kind']=='ingest-document':
                if not knowledge_jobs:
                    raise JobError('job_unknown')
                return knowledge_jobs.cancel(job_id)
            return jobs.cancel(job_id)
        except JobError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post('/api/jobs/{job_id}/read')
    def mark_job_read(job_id: str, request: Request):
        guarded_request(request)
        try:
            return jobs.mark_read(job_id)
        except JobError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.get("/api/updates")
    async def update_status(request: Request):
        # Reconcile the installed commit without contacting GitHub. A remote
        # probe is scheduled separately and never delays rendering Diagnostics.
        return update_checker.request_refresh(
            force=request.query_params.get("refresh") == "1")

    @app.get("/api/updates/installation")
    def update_installation_status():
        return web_updater.snapshot()

    @app.post("/api/updates/start", status_code=202)
    async def update_installation_start(payload: StartUpdatePayload, request: Request):
        guarded_request(request)
        nonlocal update_pending
        async with update_start_lock:
            if update_pending:
                raise HTTPException(409, detail="update_already_in_progress")
            if active_mutations:
                raise HTTPException(409, detail="update_blocked_operation")
            # Close the mutation gate before yielding to the threaded preflight.
            # Keep it closed even if the initiating client disconnects.
            update_pending = True
            task = asyncio.create_task(asyncio.to_thread(
                web_updater.start, update_checker, config, jobs, rag,
                payload.target_sha))
            def release_gate(done):
                nonlocal update_pending
                update_pending = False
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(release_gate)
            try:
                return await asyncio.shield(task)
            except (web_updater.UpdateError, ValueError, OSError) as exc:
                reason = str(exc) if isinstance(exc, web_updater.UpdateError) else "updater_unavailable"
                raise HTTPException(409, detail=reason) from None

    @app.post("/api/updates/check")
    async def update_check_now(request: Request):
        guarded_request(request)
        return await update_checker.check()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get('/')
    def home():
        return FileResponse(WEB / 'index.html')

    @app.get('/custom.css', include_in_schema=False)
    def custom_css():
        css_path = config.source.parent / 'custom.css'
        if css_path.is_symlink() or not css_path.is_file():
            return Response('', media_type='text/css', headers={'Cache-Control': 'no-store'})
        return FileResponse(css_path, media_type='text/css', headers={'Cache-Control': 'no-store'})

    @app.get("/api/locales")
    def list_locales():
        return {"items": locale_catalog.available(config.storage.data_dir),
                "fallback": "en"}

    @app.get("/api/locales/{code}")
    def language_bundle(code: str):
        try:
            return locale_catalog.get(config.storage.data_dir, code)
        except locale_catalog.LocaleError as exc:
            raise HTTPException(404, detail=str(exc)) from None

    @app.get("/api/locales/{code}/edit")
    def editable_language_bundle(code: str):
        try:
            data = locale_catalog.edit_template(config.storage.data_dir, code)
            data["reference"] = locale_catalog.get(config.storage.data_dir, "en")["messages"]
            return data
        except locale_catalog.LocaleError as exc:
            raise HTTPException(404, detail=str(exc)) from None

    @app.put("/api/locales/{code}")
    def save_language_bundle(code: str, payload: LocalePayload, request: Request):
        guarded_request(request)
        if code != payload.code:
            raise HTTPException(422, detail="language_code_mismatch")
        try:
            return locale_catalog.save(config,
                {"code": code, "name": payload.name, "messages": payload.messages},
                payload.revision)
        except locale_catalog.LocaleError as exc:
            raise HTTPException(409 if "changed" in str(exc) else 422, detail=str(exc)) from None

    @app.delete("/api/locales/{code}")
    def delete_language_bundle(code: str, revision: str, request: Request):
        guarded_request(request)
        try:
            # Include the persisted language, which may differ from the live
            # snapshot before a service restart.
            if code in (config.language, load_config(config.source).language):
                raise locale_catalog.LocaleError("cannot_delete_active_language")
            locale_catalog.delete(config, code, revision)
            return {"deleted": code}
        except locale_catalog.LocaleError as exc:
            raise HTTPException(409 if "changed" in str(exc) or "active" in str(exc) else 422,
                                detail=str(exc)) from None

    @app.get("/api/config")
    def public_config():
        return {"version": __version__, "language": config.language,
                "security": {"login_required": False},
                "storage": {"data_dir": str(config.storage.data_dir),
                            "database_dir": str(config.storage.database_dir),
                            "cache_dir": str(config.storage.cache_dir),
                            "require_mount": config.storage.require_mount},
                "modules": {"knowledge_enabled": rag is not None},
                "rag": {"enabled": bool(rag and rag.config.rag.enabled), "embedding_model": rag.embedding_model if rag else config.rag.embedding_model},
                "ai": {"enabled": config.ai.enabled, "provider": config.ai.provider,
                       "default_model": config.ai.default_model,
                       "api_key_configured": bool(gateway.api_key),
                       "active_provider": providers.active_chat, "available_providers": list(providers.available),
                       "queue": gateway.queue_status()}}

    def effective_config() -> Config:
        """Account for a completed shadow-index migration before any new edit.

        The engine's live embedding model is authoritative after the verified
        reindex path has switched its collections.  Keep it in the snapshot
        used to compare on-disk changes, without changing indexes here.
        """
        if rag and config.rag.embedding_model != rag.embedding_model:
            return replace(config, rag=replace(config.rag, embedding_model=rag.embedding_model))
        return config

    @app.get("/api/settings")
    def current_settings():
        result = settings_values(config.source)
        result['api_key_configured'] = bool(gateway.api_key)
        result['management_api_key_configured'] = bool(gateway._management_api_key)
        result['module_runtime_active'] = {'knowledge': rag is not None}
        result['language_options'] = locale_catalog.available(config.storage.data_dir)
        result['provider_options'] = [
            {'id': 'tiiny_direct', 'label': 'Tiiny Direct (Core + FIFO)'},
            {'id': 'tiiny_gateway', 'label': 'Tiiny Gateway esterno'},
            {'id': 'openai_compatible', 'label': 'OpenAI-compatible / Ollama'},
        ]
        pending = pending_restart_fields(effective_config(), load_config(config.source))
        result['restart_required'] = bool(pending)
        result['restart_fields'] = pending
        return result

    @app.put("/api/settings")
    async def edit_settings(payload: SettingsPayload, request: Request):
        nonlocal config, ai_health_last_success
        guarded_request(request)
        # Serialize with first-time embedding activation, which also writes
        # the same TOML.  Do not interrupt a running shadow-reindex operation.
        async with activation_lock:
            if rag and rag.reindex_in_progress:
                raise HTTPException(409, detail='reindex_in_progress')
            model_changed = payload.values.get('rag', {}).get('embedding_model') != (rag.embedding_model if rag else config.rag.embedding_model)
            if model_changed and rag is not None:
                raise HTTPException(409, detail='embedding_change_requires_confirmation')
            try:
                requested_ai = payload.values.get('ai', {})
                route_changed = (requested_ai.get('base_url') != config.ai.base_url
                                 or requested_ai.get('provider') != config.ai.provider)
                key_changed = payload.api_key is not None
                async with catalog_lock:
                    if (route_changed or key_changed) and ai_busy():
                        raise HTTPException(409, detail='ai_queue_busy')
                    # Do not reuse a credential for a different provider/host
                    # unless the caller explicitly supplies it again.
                    new_key = payload.api_key
                    if route_changed and new_key is None:
                        new_key = ''
                    secret_path = config.security.secrets_file
                    previous = None
                    if new_key is not None and ('\0' in new_key or '\r' in new_key or '\n' in new_key):
                        raise HTTPException(422, detail='invalid_api_key')
                    if new_key is not None:
                        if (payload.values.get('security', {}).get('secrets_file') != settings_values(config.source)['values']['security']['secrets_file']
                                or load_config(config.source).security.secrets_file != secret_path):
                            raise HTTPException(409, detail='ai_secret_path_change_separately')
                        previous = read_secrets(secret_path)
                        updated_secrets = {**previous, 'ai': {**previous.get('ai', {}), 'api_key': new_key}}
                        write_secrets(secret_path, updated_secrets)
                    try:
                        revision = save_settings(config, payload.values, payload.revision)
                    except Exception:
                        if previous is not None:
                            write_secrets(secret_path, previous)
                        raise
                    persisted = load_config(config.source)
                    updated = live_snapshot(effective_config(), persisted,
                                            rag_active=rag is not None,
                                            provider_live=route_changed)
                    gateway.apply_live_config(updated.ai, api_key=new_key, provider_live=route_changed)
                    if route_changed or key_changed:
                        ai_health_last_success = 0.0
                    invalidate_catalog()
                    config = updated
                    app.state.config = updated
                    if rag is not None:
                        rag.config = updated
                    # Kiwix HTTP endpoints read the authoritative TOML per call.
                    pending = pending_restart_fields(config, persisted)
                    return {'revision': revision, 'restart_required': bool(pending),
                            'restart_fields': pending, 'language': config.language,
                            'applied_live': True}
            except (SettingsError, SecretError, OSError) as exc:
                raise HTTPException(409 if 'changed' in str(exc) else 422, detail=str(exc)) from exc

    @app.put("/api/settings/api-key")
    async def update_api_key(payload: NewAPIKeyPayload, request: Request):
        nonlocal ai_health_last_success
        guarded_request(request)
        async with activation_lock, catalog_lock:
            if ai_busy():
                raise HTTPException(409, detail='ai_queue_busy')
            values = read_secrets(config.security.secrets_file)
            values.setdefault('ai', {})['api_key'] = payload.api_key
            write_secrets(config.security.secrets_file, values)
            gateway.api_key = payload.api_key
            ai_health_last_success = 0.0
            invalidate_catalog()
            return {'configured': True}

    @app.put("/api/settings/management-key")
    async def update_management_api_key(payload: NewAPIKeyPayload, request: Request):
        guarded_request(request)
        values = read_secrets(config.security.secrets_file)
        values.setdefault('ai', {})['management_api_key'] = payload.api_key
        write_secrets(config.security.secrets_file, values)
        gateway.management_key(payload.api_key)
        return {'configured': True}

    @app.get("/api/embedding-models")
    async def embedding_models(refresh: bool = False):
        # Model discovery must remain available before the first RAG activation.
        try:
            catalog = await fast_catalog(refresh=refresh)
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        # Discovery is read-only. Probe and potential NPU auto-load happen only
        # when the user requests verification or an actual reindex.
        return embedding_candidates(catalog, rag.embedding_model if rag else config.rag.embedding_model)

    @app.post("/api/embedding-models/probe")
    async def probe_embedding(payload: ModelPayload, request: Request):
        guarded_request(request)
        try:
            vector = (await gateway.embeddings(payload.model, ['PHAROS embedding compatibility test']))[0]
            return {'model': payload.model, 'supported': True, 'dimensions': len(vector)}
        except GatewayError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.post("/api/rag/activate")
    async def activate_rag(payload: ReindexPayload, request: Request):
        """First-time RAG setup with model verification and no manual TOML edits.

        A previously indexed database cannot be silently repointed to a new
        embedding model. The normal shadow-index reindex path remains separate.
        """
        nonlocal config, rag, reindex_service, knowledge_jobs, lexical_jobs
        guarded_request(request)
        async with activation_lock:
            if rag is not None or config.rag.enabled:
                raise HTTPException(409, detail='rag_already_enabled')
            from .config_edit import revision as config_revision
            if config_revision(config.source) != payload.revision:
                raise HTTPException(409, detail='config_changed_reload')
            model = payload.model.strip()
            if not model:
                raise HTTPException(422, detail='embedding_model_missing')
            if not config.ai.enabled:
                raise HTTPException(422, detail='gateway_disabled')
            try:
                vector = (await gateway.embeddings(model, ['PHAROS embedding compatibility test']))[0]
                if not vector or len(vector) > 8192 or not all(
                        isinstance(x, (float, int)) and math.isfinite(x) for x in vector):
                    raise HTTPException(422, detail='embedding_vector_invalid')
            except GatewayError as exc:
                raise HTTPException(422, detail='embedding_probe_failed: '+str(exc)) from exc
            candidate = replace(config, rag=replace(config.rag, enabled=True, embedding_model=model))
            try:
                engine = RAGEngine(candidate, gateway)
                # Existing KBs and their active collections must be preserved.
                # If the disabled config lost its model identity, never guess it.
                for kb in engine.list_kbs():
                    if kb.get('indexed_documents', 0) or kb.get('active_collection'):
                        if kb.get('embedding_model') != model:
                            raise HTTPException(409, detail='existing_indexes_require_reindex')
            except OSError as exc:
                raise HTTPException(503, detail='rag_storage_unavailable') from exc
            values = settings_values(config.source)['values']
            values['rag']['enabled'] = True
            values['rag']['embedding_model'] = model
            try:
                save_settings(config, values, payload.revision)
            except (SettingsError, SecretError, OSError) as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            config = load_config(config.source)
            rag = engine
            reindex_service = ReindexService(rag)
            knowledge_jobs = KnowledgeJobs(config, rag, jobs)
            lexical_jobs = LexicalJobs(rag, jobs)
            lexical_jobs.resume()
            app.state.knowledge_jobs = knowledge_jobs
            app.state.lexical_jobs = lexical_jobs
            app.state.config = config
            return {'enabled': True, 'model': model, 'dimensions': len(vector)}

    @app.get("/api/rag/reindex")
    def reindex_status():
        if not reindex_service:
            return {'active': False, 'job': None}
        return reindex_service.status()

    @app.post("/api/rag/reindex", status_code=202)
    async def reindex_start(payload: ReindexPayload, request: Request):
        require_knowledge()
        guarded_request(request)
        if not reindex_service:
            raise HTTPException(503, detail='rag_disabled')
        try:
            result = await reindex_service.start(payload.model, payload.revision)
            notifyid=jobs.create('reindex-rag','Knowledge · '+payload.model,{'model':payload.model})
            async def track_reindex():
                try:
                    while True:
                        status=reindex_service.status()
                        info=status.get('job') or {}
                        total=info.get('total') or 0
                        done=info.get('completed') or 0
                        state=info.get('state','')
                        if state in ('complete','failed','interrupted'):
                            jobs._change(notifyid,status='completed' if state=='complete' else
                                         'failed' if state=='failed' else 'interrupted',
                                         detail=(info.get('error') or '')[:128],
                                         progress=100 if state=='complete' else (int(done*100/total) if total else 0))
                            return
                        jobs._change(notifyid,status='running',detail=f'{done}/{total}',
                                     progress=min(99,int(done*100/total)) if total else 0)
                        await asyncio.sleep(1.5)
                except asyncio.CancelledError:
                    jobs._change(notifyid,status='interrupted',detail='pharos_restarted')
                    raise
            asyncio.create_task(track_reindex(),name='pharos-reindex-notification')
            return result
        except RAGError as exc:
            raise HTTPException(409 if str(exc) in ('reindex_in_progress','config_changed_reload') else 422,
                                detail=str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    @app.get("/api/status")
    async def status():
        nonlocal ai_health_last_success
        storage = check_data_volume(config.storage)
        ai_state = "disabled"
        ai_reason = None
        if config.ai.enabled:
            reasons = []
            # Model-inventory endpoints may be temporarily locked by TiinyOS
            # while inference works. An unavailable inventory is not proof
            # that the actual chat engine is unavailable.
            try:
                await asyncio.wait_for(providers.models(timeout_seconds=5), timeout=6)
                ai_state = "available"
            except (GatewayError, asyncio.TimeoutError) as exc:
                reasons.append(str(exc) if isinstance(exc, GatewayError) else 'gateway_timeout')
                if gateway.is_direct:
                    try:
                        await asyncio.wait_for(gateway.gateway.models(timeout_seconds=5), timeout=6)
                        ai_state = "available"
                    except (GatewayError, asyncio.TimeoutError) as fallback_exc:
                        reasons.append(str(fallback_exc) if isinstance(fallback_exc, GatewayError)
                                       else 'gateway_timeout')
            if ai_state == "available":
                ai_health_last_success = asyncio.get_running_loop().time()
            elif 'gateway_auth_failed' in reasons:
                ai_state = 'unavailable'
                ai_reason = 'gateway_auth_failed'
            else:
                # Preserve a recent positive probe while model inventory is busy.
                # Beyond the grace period, report the state as unverified,
                # never as a confirmed failure without a conclusive error.
                recent = (ai_health_last_success > 0 and
                          asyncio.get_running_loop().time()-ai_health_last_success <= 120)
                if recent:
                    ai_state = 'available'
                    ai_reason = 'ai_recently_verified'
                elif await _ai_upstream_reachable(config.ai.base_url):
                    # Reachability is independent of a busy TiinyOS model
                    # catalog. Do not claim that chat inference was tested.
                    ai_state = 'reachable'
                    ai_reason = 'ai_endpoint_reachable'
                else:
                    ai_state = 'unknown'
                    ai_reason = 'ai_status_not_verified'
        return {"app": "available", "database": "available",
                "storage": {"available": storage.available, "reason": storage.reason,
                            "mounted": storage.mounted},
                "ai": ai_state, "ai_reason": ai_reason}

    @app.get("/api/models")
    async def models():
        try:
            return {"models": await providers.models()}
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    @app.get('/api/ai/catalog')
    async def ai_catalog(refresh: bool = False):
        try:
            return await fast_catalog(refresh=refresh)
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    @app.get('/api/ai/queue')
    def ai_queue():
        return gateway.queue_status()

    @app.post('/api/ai/models/{operation}')
    async def ai_model_operation(operation: Literal['launch', 'stop'], payload: ModelPayload, request: Request):
        guarded_request(request)
        try:
            result = await (gateway.load(payload.model) if operation == 'launch' else gateway.unload(payload.model))
            invalidate_catalog()
            return result
        except GatewayError as exc:
            raise HTTPException(409 if str(exc) in ('tiiny_model_not_installed', 'ai_queue_full',
                                'tiiny_direct_required') else 502, detail=str(exc)) from exc

    @app.post("/api/chat")
    async def chat(payload: ChatPayload, request: Request):
        nonlocal ai_health_last_success
        guarded_request(request)
        if payload.messages[-1].role != "user":
            raise HTTPException(422, detail="last_message_must_be_user")
        model = payload.model.strip() or config.ai.default_model
        if not model:
            raise HTTPException(422, detail="model_required")
        if payload.thinking is not None and not supports_thinking_switch(model):
            raise HTTPException(422, detail='thinking_control_unsupported_model')
        if sum(len(item.content) for item in payload.messages) > 32000:
            raise HTTPException(422, detail="conversation_too_large")
        try:
            if payload.geo_enabled:
                if payload.kb_ids:
                    raise HTTPException(422, detail="geo_kb_mode_conflict")
                result = await geo_chat(
                    providers, config, model, payload.messages[-1].content,
                    thinking=payload.thinking)
                if result.get("geo_status") != "no_indexes":
                    ai_health_last_success = asyncio.get_running_loop().time()
                return {"answer": result["answer"], "model": model,
                        "sources": [], "insufficient_evidence": False,
                        "geo_results": result["geo_results"],
                        "geo_status": result["geo_status"],
                        "geo_query": result["geo_query"],
                        "geo_diagnostics": result.get("geo_diagnostics", {})}
            if payload.kb_ids:
                require_knowledge()
                result = await rag_engine().answer(
                    payload.kb_ids, payload.messages[-1].content, model,
                    history=[m.model_dump() for m in payload.messages[:-1][-8:]],
                    thinking=payload.thinking)
                compact_sources = [{k: hit.get(k) for k in
                    ('reference', 'kb_id', 'kb_name', 'document_id', 'filename', 'page',
                     'source_type', 'url', 'title')}
                    for hit in result['sources']]
                ai_health_last_success = asyncio.get_running_loop().time()
                return {"answer": result['answer'], "model": model, "sources": compact_sources,
                        "citations_missing": result.get('citations_missing', False),
                        "insufficient_evidence": result['insufficient_evidence']}
            answer = await providers.complete(model, [message.model_dump() for message in payload.messages],
                                              thinking=payload.thinking)
            ai_health_last_success = asyncio.get_running_loop().time()
            return {"answer": answer, "model": model, "sources": [], "insufficient_evidence": False}
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except RAGError as exc:
            code = (503 if str(exc) in ("rag_disabled", "vector_store_unavailable", "gateway_disabled")
                    else 502 if str(exc) == 'rag_answer_empty' else 422)
            raise HTTPException(code, detail=str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(503 if str(exc) == "gateway_disabled" else 502, detail=str(exc)) from exc

    def rag_engine():
        if rag is None:
            raise HTTPException(503, detail="rag_disabled")
        return rag

    @app.get("/api/kb")
    def knowledge_bases():
        if config.rag.enabled:
            require_knowledge()
        return {"enabled": rag is not None, "items": rag.list_kbs() if rag else []}

    @app.get('/api/kiwix/kb-links')
    def kiwix_kb_links():
        # A read-only local metadata view, no filesystem upload or ZIM scan.
        require_knowledge()
        return {'items': rag_engine().all_zim_links()}

    @app.get('/api/kb/{kb_id}/zim-sources')
    def knowledge_zim_sources(kb_id: str):
        require_knowledge()
        try:
            return {'items':rag_engine().zim_sources(kb_id)}
        except RAGError as exc:
            raise HTTPException(404 if str(exc)=='kb_not_found' else 422,detail=str(exc)) from exc

    @app.post('/api/kb/{kb_id}/zim-sources',status_code=201)
    def knowledge_link_zim(kb_id:str,payload:ZimLinkPayload,request:Request):
        require_knowledge();guarded_request(request)
        try:return rag_engine().link_zim(kb_id,payload.filename)
        except RAGError as exc:
            raise HTTPException(404 if str(exc) in ('kb_not_found','zim_not_installed') else 422,
                                detail=str(exc)) from exc

    @app.delete('/api/kb/{kb_id}/zim-sources/{filename}')
    def knowledge_unlink_zim(kb_id:str,filename:str,request:Request):
        require_knowledge();guarded_request(request)
        try:return rag_engine().unlink_zim(kb_id,filename)
        except RAGError as exc:
            raise HTTPException(404 if str(exc) in ('kb_not_found','zim_link_not_found') else 422,
                                detail=str(exc)) from exc

    @app.get("/api/kb/{kb_id}/documents")
    def knowledge_documents(kb_id: str):
        require_knowledge()
        try:
            return {"documents": rag_engine().documents(kb_id)}
        except RAGError as exc:
            raise HTTPException(404 if str(exc)=="kb_not_found" else 422, detail=str(exc)) from exc

    @app.get('/api/kb/{kb_id}/lexical')
    def knowledge_lexical_state(kb_id: str):
        require_knowledge()
        try:
            return rag_engine().lexical.status(kb_id)
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.post('/api/kb/{kb_id}/lexical/build', status_code=202)
    async def knowledge_lexical_build(kb_id: str, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            return lexical_jobs.submit(kb_id)
        except RAGError as exc:
            raise rag_http_error(exc) from exc
        except JobError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    def rag_http_error(exc: RAGError) -> HTTPException:
        msg = str(exc)
        if msg in ("rag_disabled", "vector_store_unavailable", "vector_store_error", "data_volume_unavailable"):
            return HTTPException(503, detail=msg)
        if msg in ("kb_not_found", "document_not_found", "original_unavailable"):
            return HTTPException(404, detail=msg)
        if msg == 'original_hash_mismatch':
            return HTTPException(409, detail=msg)
        return HTTPException(422, detail=msg)

    # The dashboard may intentionally have no password. Mutations remain same-origin
    # only and require the special request header; no separate admin password.
    @app.post("/api/kb", status_code=201)
    def knowledge_create(payload: KBPayload, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            return rag_engine().create_kb(payload.name, payload.description)
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.patch("/api/kb/{kb_id}")
    def knowledge_edit(kb_id: str, payload: KBPayload, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            return rag_engine().update_kb(kb_id, payload.name, payload.description)
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.delete("/api/kb/{kb_id}")
    async def knowledge_delete(kb_id: str, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            for job in jobs.active_knowledge_jobs():
                if __import__('json').loads(job['params']).get('kb_id')==kb_id:
                    raise RAGError('document_busy')
            if lexical_jobs and any(
                __import__('json').loads(row['params']).get('kb_id') == kb_id
                for row in lexical_jobs._active()
            ):
                raise RAGError('document_busy')
            await rag_engine().delete_kb(kb_id)
            return {"deleted": True}
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.post("/api/kb/{kb_id}/documents", status_code=202)
    async def knowledge_upload(kb_id: str, request: Request, file: UploadFile = File(...)):
        require_knowledge()
        guarded_request(request)
        engine = rag_engine()
        try:
            name = file.filename or ''
            # Check Content-Length only as a hint; enforce actual byte count as well.
            raw = await file.read(config.rag.max_document_mb * 1024 * 1024 + 1)
            if len(raw) > config.rag.max_document_mb * 1024 * 1024:
                raise HTTPException(413, detail="document_empty_or_too_large")
            if knowledge_jobs is None:
                raise RAGError('rag_disabled')
            return await knowledge_jobs.submit(kb_id, name, raw)
        except JobError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except RAGError as exc:
            raise rag_http_error(exc) from exc
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.patch("/api/kb/{kb_id}/documents/{doc_id}")
    async def knowledge_rename(kb_id: str, doc_id: str, payload: DocumentNamePayload, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            return await rag_engine().update_document_name(kb_id, doc_id, payload.filename)
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.delete("/api/kb/{kb_id}/documents/{doc_id}")
    async def knowledge_remove_doc(kb_id: str, doc_id: str, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            if lexical_jobs and any(
                __import__('json').loads(row['params']).get('kb_id') == kb_id
                for row in lexical_jobs._active()
            ):
                raise RAGError('document_busy')
            await rag_engine().delete_document(kb_id, doc_id)
            return {"deleted": True}
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.get("/api/kb/{kb_id}/documents/{doc_id}/file")
    def knowledge_download(kb_id: str, doc_id: str):
        require_knowledge()
        try:
            name, raw = rag_engine().get_original(kb_id, doc_id)
            suffix = Path(name).suffix.lower()
            mime = {'pdf': 'application/pdf', '.pdf':'application/pdf', '.txt':'text/plain; charset=utf-8',
                    '.md':'text/plain; charset=utf-8'}.get(suffix, 'application/octet-stream')
            # A .pdf extension alone is not sufficient: without a PDF
            # signature, refuse inline rendering and serve an attachment
            # instead. This ensures the unsandboxed PDF iframe never
            # intentionally loads arbitrary HTML under PHAROS' origin.
            valid_pdf = suffix == '.pdf' and raw.startswith(b'%PDF-')
            disposition = 'inline' if (suffix != '.pdf' or valid_pdf) else 'attachment'
            if suffix == '.pdf' and not valid_pdf:
                mime = 'application/octet-stream'
            return Response(content=raw, media_type=mime,
                 headers={'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(name, safe=''),
                          'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})
        except RAGError as exc:
            raise rag_http_error(exc) from exc

    @app.post("/api/rag/search")
    async def rag_search(payload: SearchPayload, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            return {"results": await rag_engine().search(payload.kb_ids, payload.question, payload.limit,
                                                         retrieval_mode=payload.retrieval_mode)}
        except RAGError as exc:
            raise HTTPException(503 if str(exc) in ("rag_disabled", "vector_store_unavailable", "gateway_disabled") else 422, detail=str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    @app.post("/api/rag/answer")
    async def rag_answer(payload: AnswerPayload, request: Request):
        require_knowledge()
        guarded_request(request)
        try:
            if payload.thinking is not None and not supports_thinking_switch(payload.model):
                raise HTTPException(422, detail='thinking_control_unsupported_model')
            return await rag_engine().answer(payload.kb_ids, payload.question, payload.model,
                                              payload.limit, thinking=payload.thinking,
                                               retrieval_mode=payload.retrieval_mode)
        except RAGError as exc:
            raise HTTPException(503 if str(exc) in ("rag_disabled", "vector_store_unavailable", "gateway_disabled") else 422, detail=str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    return app
