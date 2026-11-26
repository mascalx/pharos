"""Federated, bounded on-demand RAG evidence from selected local ZIM archives.

No full-ZIM extraction, copying, embedding or duplicate vector index. Kiwix's
existing full-text index selects articles at query time; PHAROS reads only
small, validated result documents from the trusted loopback kiwix-serve.
"""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

import httpx

from .library import managed_kiwix_root

MAX_SEARCH_XML = 512 * 1024
MAX_ARTICLE_HTML = 256 * 1024
MAX_ARTICLE_TEXT = 5000
MAX_ZIM_SEARCHES = 8
ZIM_FILENAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,190}\.zim\Z')
_SKIP_TERMS = {'il','lo','la','i','gli','le','un','una','uno','di','del','della','degli',
    'delle','dei','da','a','al','alla','su','per','con','che','come','sono','quale',
    'quali','cosa','cos','e','ed','si','in','nel','nella','nell','nelle','sul','sulla',
    'puoi','dire','descrivi','fammi','mostra','dove','quando','quanto','quanti','mi'}


def trimmed_query(question: str) -> str:
    words=[w for w in re.findall(r"[^\W_]{3,}",question,flags=re.UNICODE)
           if w.lower() not in _SKIP_TERMS]
    return ' '.join(words[:8])[:200] or question[:200]


class _PlainHTML(HTMLParser):
    """Extract article paragraphs without script/style or navigation markup."""
    BLOCKED={'script','style','noscript','template','nav','footer','header','form'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocked=0;self.paragraphs=[];self.active=0;self.current=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.BLOCKED:
            self.blocked+=1
        if tag=='p' and not self.blocked:
            self.active+=1
            if self.active==1:self.current=[]
    def handle_endtag(self,tag):
        if tag=='p' and self.active and not self.blocked:
            self.active-=1
            if self.active==0:
                content=re.sub(r'\s+',' ',' '.join(self.current)).strip()
                if len(content)>25:self.paragraphs.append(content)
                self.current=[]
        if tag in self.BLOCKED and self.blocked:self.blocked-=1
    def handle_data(self,data):
        if self.active and not self.blocked:self.current.append(data)


def strip_markup(text: str) -> str:
    class Plain(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.bits=[]
        def handle_data(self,data):self.bits.append(data)
    parser=Plain()
    try:parser.feed(text)
    except (ValueError,AssertionError):return ''
    return re.sub(r'\s+',' ',' '.join(parser.bits)).strip()


def parse_search_results(raw: bytes, filename: str) -> list[dict]:
    """Parse the officially tested RSS 2.0 /search?format=xml response."""
    if (not raw or len(raw)>MAX_SEARCH_XML or
            b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper()):
        return []
    try:root=ET.fromstring(raw)
    except ET.ParseError:return []
    if root.tag!='rss':return []
    channel=root.find('channel')
    if channel is None:return []
    expected='/kiwix/content/'+filename[:-4]+'/'
    results=[]
    for node in channel.findall('item')[:8]:
        raw_url=(node.findtext('link') or '').strip()
        url=urlsplit(raw_url)
        # All evidence is fetched through local kiwix-serve; remote or arbitrary
        # file paths from the XML must not be treated as trusted.
        if url.scheme or url.netloc or url.query or url.fragment or not url.path.startswith(expected):
            continue
        tail=url.path[len(expected):]
        decoded=unquote(tail)
        if (not decoded or len(decoded)>700 or decoded.startswith('/') or
            any(piece in ('','.','..') for piece in decoded.split('/')) or
            any(ord(ch)<32 or ord(ch)==127 for ch in decoded)):
            continue
        title=strip_markup(node.findtext('title') or '')[:220]
        snippet=strip_markup(node.findtext('description') or '')[:1200]
        if not title:continue
        results.append({'title':title,'url':url.path,'snippet':snippet})
    return results


class KiwixRAGSource:
    def __init__(self,config):
        self.config=config

    def _local_root(self) -> str | None:
        url=self.config.kiwix.server_url
        parts=urlsplit(url)
        if parts.scheme!='http' or parts.hostname not in ('127.0.0.1','localhost') or not parts.port:
            return None
        return managed_kiwix_root(url)

    async def _read_bounded(self,client,url,*,params=None,max_bytes=MAX_ARTICLE_HTML):
        async with client.stream('GET',url,params=params) as response:
            if response.status_code!=200:return b''
            body=bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body)>max_bytes:return b''
            return bytes(body)

    async def search_one(self,kb:dict,filename:str,question:str)->list[dict]:
        root=self._local_root()
        if not root or not ZIM_FILENAME_RE.fullmatch(filename):return []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12),trust_env=False,
                                         follow_redirects=False) as client:
                results=parse_search_results(await self._read_bounded(
                    client,root+'/search',params={'pattern':trimmed_query(question),
                      'books.name':filename[:-4],'pageLength':3,'format':'xml'},
                    max_bytes=MAX_SEARCH_XML),filename)
                hits=[]
                for record in results[:3]:
                    raw=await self._read_bounded(client,root+'/content/'+record['url'].split('/content/',1)[1])
                    if raw:
                        parser=_PlainHTML()
                        parser.feed(raw.decode('utf-8',errors='replace'))
                        article=' '.join(parser.paragraphs)[:MAX_ARTICLE_TEXT]
                    else:article=''
                    context=(record['snippet']+'\n'+article).strip()[:MAX_ARTICLE_TEXT]
                    if len(context)<40:continue
                    hits.append({'kb_id':kb['id'],'kb_name':kb['name'],
                        'document_id':'zim:'+filename,'filename':filename,
                        'page':None,'chunk_index':None,'text':record['title']+'\n'+context,
                        'score':None,'source_type':'zim','url':record['url'],
                        'title':record['title']})
                return hits
        except (httpx.HTTPError,ValueError,UnicodeError,ET.ParseError):
            return []

    async def search(self,books:list[tuple[dict,str]],question:str,limit:int)->list[dict]:
        # Each archive has its own full-text index; cap total work per query.
        semaphore=asyncio.Semaphore(3)
        async def lookup(kb,filename):
            async with semaphore:
                return await self.search_one(kb,filename,question)
        tasks=[lookup(kb,filename) for kb,filename in books[:MAX_ZIM_SEARCHES]]
        if not tasks:return []
        groups=await asyncio.gather(*tasks,return_exceptions=True)
        hits=[]
        for group in groups:
            if not isinstance(group,list):continue
            for hit in group:
                if hit['url'] not in {r['url'] for r in hits}:
                    hits.append(hit)
                if len(hits)>=limit:return hits
        return hits
