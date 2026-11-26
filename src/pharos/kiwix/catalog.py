"""Read the public Kiwix OPDS v2 acquisition catalog independently of kiwix-serve.

Only PHAROS (server-side) contacts the fixed official HTTPS OPDS endpoint. The
existing downloader remains the sole executor and verifies ZIM SHA-256 before
publication. A small, bounded local cache permits previously browsed pages to
be viewed when the Internet is down; it is NOT a promise of an offline global
catalog. Never trust remote XML to supply arbitrary download or browser URLs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

import httpx

from pharos.download_jobs import JobError, validate_zim_url
from .library import KiwixError, local

CATALOG_URL = 'https://opds.library.kiwix.org/catalog/v2/entries'
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024
CACHE_MAX_AGE = 6 * 3600
# The OPDS acquisition feed sometimes advertises a .zim.meta4 metalink and
# sometimes a direct .zim. Both refer to the same file on the official publisher.
ACQUISITION_REL = 'http://opds-spec.org/acquisition/open-access'
LANGUAGE = re.compile(r'^[a-z]{3}$')
CATEGORY = re.compile(r'^[a-z0-9_-]{1,48}$')


def _text(element: ET.Element, name: str, max_chars: int = 400) -> str:
    for child in element:
        if local(child.tag) == name:
            return (child.text or '').strip()[:max_chars]
    return ''


def _official_zim_url(href: str) -> str:
    """Canonicalise catalog acquisition link without trusting redirect hosts.

    Only a Kiwix-published /zim/ path can become a downloader URL. Even if OPDS
    later links to a third-party host, PHAROS does not download from that host.
    """
    try:
        parts = urlsplit(href)
        if (parts.scheme != 'https' or parts.username is not None or
                parts.password is not None or parts.port not in (None, 443) or
                not parts.hostname or not (
                    parts.hostname == 'download.kiwix.org' or
                    parts.hostname.endswith('.download.kiwix.org')) or
                parts.query or parts.fragment):
            return ''
        path = parts.path
        if not path.startswith('/zim/') or '%' in path:
            return ''
        if path.endswith('.zim.meta4'):
            path = path[:-6]
        if not path.endswith('.zim'):
            return ''
        # Reconstruct the official canonical publisher URL; the existing
        # downloader checks allowed hosts, safe filenames and publisher SHA256.
        url = 'https://download.kiwix.org' + path
        validate_zim_url(url)
        return url
    except (ValueError, JobError):
        return ''


def parse_online_catalog(raw: bytes) -> dict:
    """Parse bounded XML; malformed remote records are not selectable."""
    if (not raw or len(raw) > MAX_CATALOG_BYTES or
            b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper()):
        raise KiwixError('kiwix_online_catalog_invalid')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise KiwixError('kiwix_online_catalog_invalid') from exc
    if local(root.tag) != 'feed':
        raise KiwixError('kiwix_online_catalog_invalid')
    items = []
    total = None
    raw_entries = 0
    for child in root:
        kind = local(child.tag)
        if kind == 'totalResults':
            try:
                n = int((child.text or '').strip())
                if 0 <= n <= 2_000_000:
                    total = n
            except ValueError:
                pass
        if kind != 'entry':
            continue
        raw_entries += 1
        title = _text(child, 'title', 240)
        if not title:
            continue
        url = ''
        declared_size = None
        for link in child:
            if (local(link.tag) == 'link' and link.get('rel') == ACQUISITION_REL
                    and link.get('type') == 'application/x-zim'):
                found = _official_zim_url(link.get('href', ''))
                if found:
                    url = found
                    length = link.get('length', '')
                    if length.isdecimal() and 0 < int(length) < 2**62:
                        declared_size = int(length)
                    break
        if not url:
            continue
        items.append({
            'id': _text(child, 'id', 200), 'title': title,
            'description': _text(child, 'summary', 600) or _text(child, 'description', 600),
            'language': _text(child, 'language', 90),
            'category': _text(child, 'category', 80),
            'updated': _text(child, 'updated', 40),
            'url': url, 'filename': unquote(url.rsplit('/', 1)[-1]),
            'size': declared_size,
        })
        if raw_entries >= 100:
            break
    return {'items': items, 'total': total, 'raw_count': raw_entries}


class OnlineKiwixCatalog:
    """Paginated official online catalog with atomically saved cache pages."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def _cache_file(self, params: dict) -> Path:
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode('utf-8')).hexdigest()
        return self.cache_dir / (key + '.json')

    def _read_cache(self, path: Path):
        try:
            if path.is_symlink() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or not isinstance(payload.get('results'), dict):
                return None
            timestamp = payload.get('fetched_at')
            if type(timestamp) is not int or timestamp > int(time.time()) + 60:
                return None
            return payload
        except (OSError, TypeError, ValueError, UnicodeError):
            return None

    def _write_cache(self, path: Path, data: dict):
        try:
            if self.cache_dir.is_symlink() or path.is_symlink():
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            content = json.dumps({'fetched_at': int(time.time()), 'results': data}, ensure_ascii=False)
            if len(content.encode('utf-8')) > MAX_CACHE_BYTES:
                return
            fd, name = tempfile.mkstemp(prefix='.kiwix-catalog-', suffix='.tmp', dir=self.cache_dir)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
        except OSError:
            # Cache is optional and must never make a live catalog fail.
            return

    async def browse(self, *, start: int = 0, count: int = 24, q: str = '',
                     lang: str = '', category: str = '') -> dict:
        if (not 0 <= start <= 100000 or not 1 <= count <= 50 or
                len(q) > 100 or len(lang) > 3 or len(category) > 48 or
                (lang and not LANGUAGE.fullmatch(lang)) or
                (category and not CATEGORY.fullmatch(category))):
            raise KiwixError('kiwix_catalog_filters_invalid')
        params = {'start': start, 'count': count}
        if q:
            params['q'] = q
        if lang:
            params['lang'] = lang
        if category:
            params['category'] = category
        cached = self._read_cache(self._cache_file(params))
        offline_cache = False
        now = int(time.time())
        if cached and now - cached['fetched_at'] < CACHE_MAX_AGE:
            result = cached['results']
        else:
            try:
                # No proxy, redirects or arbitrary host are accepted for OPDS.
                async with httpx.AsyncClient(timeout=httpx.Timeout(12), trust_env=False,
                                             follow_redirects=False) as client:
                    async with client.stream('GET', CATALOG_URL, params=params,
                                             headers={'Accept': 'application/atom+xml'}) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for block in response.aiter_bytes():
                            raw.extend(block)
                            if len(raw) > MAX_CATALOG_BYTES:
                                raise KiwixError('kiwix_online_catalog_too_large')
                result = parse_online_catalog(bytes(raw))
                self._write_cache(self._cache_file(params), result)
            except (httpx.HTTPError, KiwixError):
                if cached:
                    result = cached['results']
                    offline_cache = True
                else:
                    raise KiwixError('kiwix_online_catalog_unavailable') from None
        items = result.get('items', [])
        raw_count = result.get('raw_count', 0)
        total = result.get('total')
        return {'items': items, 'start': start, 'count': count,
                'more': (start + count < total) if total is not None else raw_count >= count,
                'total': total, 'cached': offline_cache, 'source': 'official_kiwix_opds'}
