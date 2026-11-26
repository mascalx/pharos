"""Safe OPDS v2 catalog read; content remains served by the separate Kiwix server."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urlsplit

import httpx

class KiwixError(ValueError):
    pass

MAX_XML=2*1024*1024


def managed_kiwix_root(server_url: str) -> str:
    """Managed kiwix-serve uses --urlRootLocation=/kiwix.

    External Kiwix instances retain the historical root URL so existing
    deployments with a separately managed server are not reconfigured.
    """
    parsed = urlsplit(server_url)
    if parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '0.0.0.0'} and parsed.path in ('', '/'):
        return server_url.rstrip('/') + '/kiwix'
    return server_url.rstrip('/')


def safe_browser_url(base:str, suffix:str, params:dict|None=None)->str:
    # base URL has already been validated by the central TOML validator.
    target=base.rstrip('/')+'/'+suffix.lstrip('/')
    return target+('?' + urlencode(params) if params else '')


def local(tag:str)->str:
    return tag.rsplit('}',1)[-1].rsplit(':',1)[-1]


def field(node:ET.Element, name:str)->str:
    for part in node:
        if local(part.tag)==name:
            return (part.text or '').strip()
    return ''


def parse_books(raw:bytes)->list[dict]:
    if not raw or len(raw)>MAX_XML or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise KiwixError('kiwix_catalog_invalid')
    try:
        tree=ET.fromstring(raw)
    except ET.ParseError as exc:
        raise KiwixError('kiwix_catalog_invalid') from exc
    if local(tree.tag)!='feed':
        raise KiwixError('kiwix_catalog_invalid')
    books=[]
    for entry in tree:
        if local(entry.tag)!='entry': continue
        title=field(entry,'title')[:300]
        name=field(entry,'name')[:200]
        identifier=field(entry,'id')[:200]
        description=field(entry,'summary')[:500] or field(entry,'description')[:500]
        language=field(entry,'language')[:70]
        # <name> is the descriptive *book* name, often shared by multiple
        # editions.  /content/ needs the unique *ZIM* name instead.  Prefer
        # the content link advertised by kiwix-serve, even if <name> exists.
        for link in entry:
            if local(link.tag)!='link':
                continue
            href=link.get('href','')
            match=re.fullmatch(r'/?(?:kiwix/)?content/([A-Za-z0-9_.+\-]{1,200})/?', href)
            if match:
                name=match.group(1)
                break
        if not title: continue
        if name and not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,200}',name):name=''
        books.append({'title':title,'name':name,'id':identifier,
                      'description':description,'language':language})
        if len(books)>=100:break
    return books

class KiwixLibrary:
    def __init__(self,server_url:str,browser_url:str=''):
        self.server_url=managed_kiwix_root(server_url)
        self.browser_url=(browser_url or server_url).rstrip('/')

    async def books(self,*,start:int=0,count:int=30,q:str='',
                    installed_filenames: list[str] | None = None)->dict:
        params={'start':start,'count':count}
        if q:params['q']=q
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0),trust_env=False,
                                         follow_redirects=False) as client:
                async with client.stream('GET',safe_browser_url(self.server_url,'catalog/v2/entries'),params=params,
                                         headers={'Accept':'application/atom+xml'}) as response:
                    response.raise_for_status()
                    raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>MAX_XML:raise KiwixError('kiwix_catalog_too_large')
        except httpx.HTTPError as exc:
            raise KiwixError('kiwix_server_unavailable') from exc
        books=parse_books(bytes(raw))
        if installed_filenames is not None:
            # On a managed Kiwix instance the actual .zim files are the source
            # of truth, not the duplicate/non-unique OPDS descriptive names.
            # This also exposes two editions of the same title independently.
            by_name={b['name'].lower():b for b in books if b['name']}
            selected=[]
            for filename in sorted(installed_filenames):
                if not filename.endswith('.zim') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,190}\.zim',filename):
                    continue
                stem=filename[:-4]
                match=by_name.get(stem.lower())
                if match is None:
                    candidates=[b for b in books if b['name'] and
                                stem.lower().startswith(b['name'].lower()+'_')]
                    # Reuse generic metadata for known prefix, but NEVER use
                    # the descriptive OPDS name as the content URL.
                    match=max(candidates,key=lambda b:len(b['name'])) if candidates else None
                book=dict(match) if match else {
                    'title':stem.replace('_',' '), 'description':'',
                    'language':'', 'id':''}
                book['name']=stem
                book['filename']=filename
                book['url']=safe_browser_url(self.browser_url,'content/'+quote(stem,safe=''))
                if q and q.casefold() not in (book['title']+' '+filename).casefold():
                    continue
                selected.append(book)
            page=selected[start:start+count]
            return {'items':page,'start':start,'count':count,'more':start+count<len(selected),
                    'browser_url':self.browser_url}
        # An external, independently managed server may not have local ZIMs.
        # In that mode, the server's content link is the best source available.
        for book in books:
            book['url']=safe_browser_url(self.browser_url,'content/'+quote(book['name'],safe='')) if book['name'] else ''
        return {'items':books,'start':start,'count':count,'more':len(books)>=count,
                'browser_url':self.browser_url}
