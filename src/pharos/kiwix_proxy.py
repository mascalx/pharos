"""Same-origin, read-only gateway to one explicitly configured Kiwix server.

No user-controlled destination is accepted. Raw response bytes are streamed so
large offline articles and images need not be buffered in PHAROS memory.
"""
from __future__ import annotations

import httpx
import re
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from urllib.parse import quote, urlsplit, urljoin
from .kiwix.library import managed_kiwix_root

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade',
    'set-cookie', 'content-security-policy', 'x-frame-options'}


# Late stylesheet override: keep the ZIM's own layout and navigation while
# harmonizing base text, paper, links and controls with PHAROS. Never rewrite
# archive files, replace article scripts or remove iframe sandboxing.
KIWIX_PHAROS_THEME = b"""<style id="pharos-kiwix-palette">
:root{color-scheme:only light!important;background:#faebdc!important;
  --background-color:#faebdc;--text-color:#201b18;--link-color:#ad2446}
html,body{background-color:#faebdc!important;color:#201b18!important;
  color-scheme:only light!important}
a,a:link{color:#ad2446!important}
a:visited{color:#8d3151!important}
input,textarea,select,button{color-scheme:only light!important;
  background-color:#fff7ed;color:#201b18}
</style>"""
_HTML_HEAD_CLOSE = re.compile(br"</head\s*>", re.I)
_HTML_BODY_OPEN = re.compile(br"<body\b[^>]*>", re.I)
_THEME_SCAN_LIMIT = 128 * 1024


def _insert_kiwix_theme(prefix: bytes) -> bytes:
    head = _HTML_HEAD_CLOSE.search(prefix)
    if head:
        return prefix[:head.start()] + KIWIX_PHAROS_THEME + prefix[head.start():]
    body = _HTML_BODY_OPEN.search(prefix)
    if body:
        return prefix[:body.end()] + KIWIX_PHAROS_THEME + prefix[body.end():]
    return prefix


def install_kiwix_proxy(app, config, service):
    @app.api_route('/kiwix', methods=['GET','HEAD'])
    @app.api_route('/kiwix/{path:path}', methods=['GET','HEAD'])
    async def proxy(request: Request, path: str = ''):
        from .config import load_config
        cfg = load_config(config.source).kiwix
        try:
            if not service.ensure_running(cfg.server_url):
                raise HTTPException(503, detail='kiwix_library_empty')
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            from .kiwix_runtime import KiwixRuntimeError
            if isinstance(exc, KiwixRuntimeError):
                raise HTTPException(503, detail=str(exc)) from exc
            raise
        # Only one fixed upstream, configured in TOML; no absolute client URLs.
        target = managed_kiwix_root(cfg.server_url) + '/' + quote(path, safe='/-._~!$&\'()*+,;=:@')
        if request.url.query:
            target += '?' + request.url.query
        client = httpx.AsyncClient(timeout=httpx.Timeout(35.0), follow_redirects=False, trust_env=False)
        headers = {'Accept': request.headers.get('accept', '*/*'),
                   'Accept-Encoding': 'identity'}
        if request.headers.get('range'):
            headers['Range'] = request.headers['range']
        try:
            upstream = await client.send(client.build_request(request.method, target,
                headers=headers), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(502, detail='kiwix_server_unavailable') from exc
        response_headers = {k: v for k, v in upstream.headers.items()
                            if k.lower() not in HOP_HEADERS and k.lower() != 'location'}
        location = upstream.headers.get('location')
        if location:
            absolute = urljoin(target, location)
            parsed = urlsplit(absolute)
            configured = urlsplit(cfg.server_url)
            if (parsed.scheme, parsed.netloc) == (configured.scheme, configured.netloc):
                root = urlsplit(managed_kiwix_root(cfg.server_url)).path.rstrip('/')
                local_path = parsed.path or '/'
                if root and local_path.startswith(root + '/'):
                    local_path = local_path[len(root):]
                elif root and local_path == root:
                    local_path = '/'
                response_headers['Location'] = '/kiwix' + local_path + (('?' + parsed.query) if parsed.query else '')
            # Off-host redirects are deliberately dropped.
        # HTML only. Non-HTML assets and range responses remain byte-exact.
        # Kiwix generally serves plain UTF-8 HTML; leave compressed/UTF-16
        # responses untouched instead of corrupting their representation.
        mime = upstream.headers.get('content-type', '').lower()
        html_theme = (request.method == 'GET' and upstream.status_code == 200
                      and mime.startswith('text/html')
                      and 'utf-16' not in mime and 'utf-32' not in mime
                      and not request.headers.get('range')
                      and upstream.headers.get('content-encoding', 'identity').lower()
                      in ('', 'identity'))
        if html_theme:
            for header in ('content-length', 'etag', 'content-md5', 'accept-ranges'):
                response_headers.pop(header, None)
            response_headers['Cache-Control'] = 'private, no-store'

        async def stream():
            try:
                if html_theme:
                    pending = bytearray()
                    source = upstream.aiter_raw()
                    async for part in source:
                        pending.extend(part)
                        if (_HTML_HEAD_CLOSE.search(pending)
                                or _HTML_BODY_OPEN.search(pending)
                                or len(pending) >= _THEME_SCAN_LIMIT):
                            yield _insert_kiwix_theme(bytes(pending))
                            break
                    else:
                        if pending:
                            yield _insert_kiwix_theme(bytes(pending))
                        return
                    async for part in source:
                        yield part
                else:
                    async for part in upstream.aiter_raw():
                        yield part
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)
