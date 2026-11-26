"""Integrated Kiwix catalog routes (no dynamic module lifecycle)."""
from __future__ import annotations
import re
from fastapi import APIRouter, HTTPException, Query
from pharos.config import load_config
from .library import KiwixLibrary, KiwixError, safe_browser_url
from .catalog import OnlineKiwixCatalog
from pharos.kiwix_runtime import KiwixRuntimeError


def make_router(config, service):
    router = APIRouter(prefix='/extensions/kiwix')
    online_catalog = OnlineKiwixCatalog(config.storage.database_dir / 'kiwix-catalog-cache')

    @router.get('/status')
    def status():
        details = service.status()
        return {'service': 'kiwix', 'available': details['installed'], **details}

    @router.get('/catalog')
    async def catalog(start: int = Query(0, ge=0, le=100000),
                      count: int = Query(24, ge=1, le=50),
                      q: str = Query('', max_length=100),
                      lang: str = Query('', max_length=3),
                      category: str = Query('', max_length=48)):
        """Search Kiwix's official ONLINE OPDS index, independently of local server.

        Only sanitized .zim URLs from the publisher appear in the response;
        download itself still requires the existing guarded job endpoint.
        """
        try:
            result = await online_catalog.browse(start=start, count=count, q=q,
                                                 lang=lang, category=category)
            # A detached or unavailable data volume must not prevent browsing
            # metadata online; downloading remains forbidden until it is mounted.
            try:
                installed = {p.name for p in service._zims()}
            except (OSError, KiwixRuntimeError):
                installed = set()
            for item in result['items']:
                item['installed'] = item['filename'] in installed
            return result
        except KiwixError as exc:
            raise HTTPException(503 if str(exc) == 'kiwix_online_catalog_unavailable'
                                else 422, detail=str(exc)) from exc

    @router.get('/books')
    async def books(start: int = Query(0, ge=0, le=100000),
                    count: int = Query(30, ge=1, le=100),
                    q: str = Query('', max_length=100)):
        cfg = load_config(config.source).kiwix
        try:
            if not service.ensure_running(cfg.server_url):
                return {'items': [], 'start': start, 'count': count,
                        'more': False, 'browser_url': '/kiwix'}
            from .library import managed_kiwix_root
            local_managed = managed_kiwix_root(cfg.server_url) != cfg.server_url.rstrip('/')
            filenames = [item.name for item in service._zims()] if local_managed else None
            return await KiwixLibrary(cfg.server_url, '/kiwix').books(
                start=start, count=count, q=q, installed_filenames=filenames)
        except (KiwixError, KiwixRuntimeError) as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    @router.get('/search-link')
    def search_link(pattern: str = Query(..., min_length=1, max_length=200),
                    name: str = Query(..., min_length=1, max_length=200)):
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,200}', name):
            raise HTTPException(422, detail='kiwix_invalid_book_name')
        return {'url': safe_browser_url('/kiwix', 'search',
                {'pattern': pattern, 'books.name': name})}

    return router
