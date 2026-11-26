"""Optional full-corpus FTS5 over immutable Qdrant text payloads.

No database schema migration and no vector-index mutation. All lexical files
are derived from active Qdrant collections and can be safely rebuilt.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
from uuid import uuid4

from pharos.knowledge_contract import RAGError

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# An identifier is materially different from an ordinary two-word request:
# its exact appearance is a strong lexical signal even if vectors miss it.
# Complete identifiers, including dotted numeric versions/protocol names.
# Ordinary dates such as 2026 are not treated as special identifiers.
IDENTIFIER_RE = re.compile(r"(?<![\w/-])(?<!\w\.)[^\W_]+(?:[-._/][^\W_]+)*(?![\w/-]|\.\w)", re.UNICODE)


def _identifiers(question: str) -> list[str]:
    return list(dict.fromkeys(
        item for item in IDENTIFIER_RE.findall(question.casefold())
        if len(item) >= 4 and any(char.isdigit() for char in item)
        and (any(char.isalpha() for char in item)
             or any(char in '-._/' for char in item)
             or (item.isdigit() and (item.startswith('0') or len(item) >= 6)))
    ))[:3]


def _has_identifier(text: str, identifier: str) -> bool:
    return bool(re.search(r'(?<![\w/-])(?<!\w\.)' + re.escape(identifier)
                          + r'(?![\w/-]|\.\w)', text.casefold()))
MAX_INDEXED_CHUNKS = 300_000

def _distinctive_terms(db: sqlite3.Connection, question: str) -> list[str]:
    """Choose uncommon tokens using *this KB's* FTS5 posting frequencies.

    No hardcoded Italian/English stopword list: the same rule applies across
    languages supported by unicode61. An uncommon word that does not appear in
    the KB contributes nothing. Existing FTS5 generations need no rebuild.
    """
    total = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    if not total:
        return []
    terms = list(dict.fromkeys(
        word for word in TOKEN_RE.findall(question.casefold())
        if len(word) >= 3 or word.isdigit()
    ))[:16]
    threshold = max(2, int(total * 0.18))
    identifier_parts = {
        part for code in _identifiers(question) for part in TOKEN_RE.findall(code)
    }
    selected = []
    for word in terms:
        expression = '"' + word.replace('"', '""') + '"'
        frequency = db.execute('SELECT COUNT(*) FROM chunks WHERE chunks MATCH ?',
                               (expression,)).fetchone()[0]
        if 0 < frequency and (frequency <= threshold or word in identifier_parts):
            selected.append(word)
    return selected


def _fold_term(value: str) -> str:
    """Match diacritic variants that SQLite unicode61 normally folds."""
    return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(char))


def _lexical_strength(text: str, question: str, selected: list[str]) -> float:
    """Bound weak lexical influence; reserve strong promotion for exact evidence.

    Ordinary two-token overlaps are not reliably relevant in long manuals.
    They can only break *close* semantic rank ties, while exact technical
    identifiers or three distinctive consecutive query terms can add a new
    chunk missing from the vector pool.
    """
    identifiers = _identifiers(question)
    if any(_has_identifier(text, code) for code in identifiers):
        return 2.4

    present = {_fold_term(token) for token in TOKEN_RE.findall(text.casefold())}
    matched = {_fold_term(word) for word in selected
               if _fold_term(word) in present}
    if not matched:
        return 0.0

    # Explicitly avoid treating Wi-Fi as a one-term query merely because
    # the individual tokens 'wi' and 'fi' are short.
    if len(TOKEN_RE.findall(question.casefold())) <= 2:
        return 0.012
    if len(matched) < 2:
        return 0.0

    normalized = _fold_term(text)
    filtered = [_fold_term(word) for word in selected]
    for first, second, third in zip(filtered, filtered[1:], filtered[2:]):
        if all(term in matched for term in (first, second, third)):
            exact = (r'(?<!\w)' + re.escape(first) + r'\s+'
                     + re.escape(second) + r'\s+'
                     + re.escape(third) + r'(?!\w)')
            if re.search(exact, normalized):
                return 1.25

    # Two distinctive adjacent words are a modest corroboration, never
    # enough by themselves to displace the first semantic result.
    for first, second in zip(filtered, filtered[1:]):
        if first in matched and second in matched:
            pattern = (r'(?<!\w)' + re.escape(first) + r'\s+'
                       + re.escape(second) + r'(?!\w)')
            if re.search(pattern, normalized):
                return 0.035
    return 0.008


def _query_terms(question: str) -> str:
    """Build an escaped, bounded FTS5 expression; never run raw user MATCH."""
    terms = list(dict.fromkeys(
        word for word in TOKEN_RE.findall(question.casefold())
        if len(word) >= 2 or word.isdigit()
    ))[:12]
    # Unicode61 tokenizer handles the supported token classes; never pass syntax.
    return ' OR '.join('"' + word.replace('"', '""') + '"' for word in terms)


def fuse_full_corpus(vectors: list[dict], lexical: list[dict]) -> list[dict]:
    """Rank fusion without pretending cosine and BM25 scores are comparable."""
    qualified = [hit for hit in lexical if float(hit.get('_lexical_weight', 1.0)) > 0]
    if not qualified:
        # A missing/stale/broken FTS index (or weak/no lexical evidence) must
        # give exactly the pre-existing vector ordering.
        return [dict(hit) for hit in vectors]
    found: dict[tuple, dict] = {}
    scores: dict[tuple, float] = {}

    def key(hit):
        kb_id, doc_id, idx = hit.get('kb_id'), hit.get('document_id'), hit.get('chunk_index')
        if isinstance(doc_id, str) and type(idx) is int:
            return (kb_id, doc_id, idx)
        # Old Qdrant payloads may predate chunk_index. Never discard evidence.
        return (kb_id, doc_id, 'legacy', hit.get('page'),
                hit.get('filename'), hit.get('text'))

    for rank, hit in enumerate(vectors, 1):
        identity = key(hit)
        if identity not in found:
            found[identity] = dict(hit)
        scores[identity] = scores.get(identity, 0) + 1.0 / (60 + rank)

    for rank, hit in enumerate(qualified, 1):
        identity = key(hit)
        if identity not in found:
            found[identity] = {**hit, 'score': None, 'retrieval': 'lexical'}
        weight = float(hit.get('_lexical_weight', 1.0))
        scores[identity] = scores.get(identity, 0) + weight / (60 + rank)
    ordered = sorted(found, key=lambda k: (-scores[k], str(k)))
    # Confidence is an internal ranking detail, not a Qdrant score.
    return [{**{field: value for field, value in found[k].items()
                if field != '_lexical_weight'}, 'retrieval': 'hybrid_fts'}
            for k in ordered]


class LexicalIndex:
    def __init__(self, engine):
        self.engine = engine
        self.base = engine.config.storage.database_dir / 'rag-lexical'

    def _folder(self, kb_id: str) -> Path:
        return self.base / self.engine._uuid(kb_id)

    def _snapshot(self, kb_id: str) -> tuple[dict, str]:
        kb = self.engine.get_kb(kb_id)
        with closing(self.engine._db()) as db:
            rows = db.execute(
                'SELECT id,filename,sha256,status,chunks FROM documents WHERE kb_id=? ORDER BY id',
                (kb['id'],)).fetchall()
        snapshot = {
            'schema': 1, 'kb_id': kb['id'],
            'collection': self.engine._collection(kb['id']),
            'embedding_model': kb['embedding_model'],
            'dimensions': kb['dimensions'],
            'documents': [dict(row) for row in rows],
        }
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return snapshot, digest

    def _safe_directory(self, kb_id: str, create: bool = False) -> Path:
        directory = self._folder(kb_id)
        if self.base.is_symlink() or directory.is_symlink():
            raise RAGError('lexical_unsafe_directory')
        if create:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def _active_file(self, kb_id: str) -> Path | None:
        directory = self._safe_directory(kb_id)
        manifest = directory / 'manifest.json'
        if not manifest.is_file() or manifest.is_symlink():
            return None
        try:
            meta = json.loads(manifest.read_text(encoding='utf-8'))
            snap, signature = self._snapshot(kb_id)
            if meta.get('schema') != 1 or meta.get('snapshot') != signature:
                return None
            name = meta.get('file')
            if not isinstance(name, str) or not re.fullmatch(r'index-[0-9a-f]{32}\.sqlite3', name):
                return None
            path = directory / name
            if not path.is_file() or path.is_symlink():
                return None
            return path
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def status(self, kb_id: str) -> dict:
        self.engine.get_kb(kb_id)
        path = self._active_file(kb_id)
        if path:
            return {'state': 'ready'}
        manifest = self._folder(kb_id) / 'manifest.json'
        return {'state': 'stale' if manifest.is_file() else 'missing'}

    def purge(self, kb_id: str) -> None:
        """Delete *only derived lexical files* after explicit KB/document deletion.

        Fail closed if the directory contains symlinks or unexpected files:
        never follow paths outside the dedicated KB lexical directory.
        """
        directory = self._safe_directory(kb_id)
        if not directory.exists():
            return
        if not directory.is_dir():
            raise RAGError('lexical_cleanup_unsafe')
        known = re.compile(
            r'(?:index-[0-9a-f]{32}\.sqlite3|\.[0-9a-f]{32}\.building)'
            r'(?:-journal|-wal|-shm)?|manifest\.json|'
            r'\.manifest-[A-Za-z0-9_.-]+\.tmp')
        for item in directory.iterdir():
            if item.is_symlink() or not item.is_file() or not known.fullmatch(item.name):
                raise RAGError('lexical_cleanup_unsafe')
        try:
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()
        except OSError as exc:
            raise RAGError('lexical_cleanup_failed') from exc

    async def build(self, kb_id: str, job_id: str, progress) -> dict:
        """Build isolated generation; publish only after all consistency checks."""
        self.engine._enabled()
        if self.engine.reindex_in_progress:
            raise RAGError('reindex_in_progress')
        kb_id = self.engine._uuid(kb_id)
        snapshot, signature = self._snapshot(kb_id)
        documents = snapshot['documents']
        if any(row['status'] == 'indexing' for row in documents):
            raise RAGError('document_busy')
        indexed = {row['id']: row for row in documents if row['status'] == 'indexed'}
        total = sum(row['chunks'] for row in indexed.values())
        if not total:
            raise RAGError('lexical_no_indexed_documents')
        if total > MAX_INDEXED_CHUNKS:
            raise RAGError('lexical_too_many_chunks')
        self.engine._check_volume()
        folder = self._safe_directory(kb_id, create=True)
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise RAGError('lexical_invalid_job')
        staging = folder / ('.' + job_id + '.building')
        for suffix in ('', '-journal', '-wal', '-shm'):
            staging.with_name(staging.name + suffix).unlink(missing_ok=True)
        seen: set[tuple[str, int]] = set()
        counts: Counter[str] = Counter()
        conn = None
        try:
            conn = sqlite3.connect(staging)
            if os.name == 'posix':
                os.chmod(staging, 0o600)
            conn.execute("CREATE VIRTUAL TABLE chunks USING fts5("
                         "document_id UNINDEXED, filename UNINDEXED, page UNINDEXED,"
                         "chunk_index UNINDEXED, text, tokenize='unicode61')")
            offset = None
            iteration = 0
            while True:
                body = {'limit': 128, 'with_payload': True, 'with_vector': False}
                if offset is not None:
                    body['offset'] = offset
                response = await self.engine._qdrant(
                    'POST', '/collections/' + snapshot['collection'] + '/points/scroll', body)
                result = response.get('result', {})
                if not isinstance(result, dict) or not isinstance(result.get('points'), list):
                    raise RAGError('lexical_invalid_vector_response')
                points = result['points']
                if not points and result.get('next_page_offset') is not None:
                    raise RAGError('lexical_invalid_vector_response')
                batch = []
                for point in points:
                    payload = point.get('payload') if isinstance(point, dict) else None
                    if not isinstance(payload, dict):
                        continue
                    doc = indexed.get(payload.get('document_id'))
                    if doc is None:
                        continue
                    chunk = payload.get('chunk_index')
                    page = payload.get('page')
                    text = payload.get('text')
                    if (type(chunk) is not int or chunk < 0 or chunk >= doc['chunks']
                            or type(page) is not int or page < 1
                            or not isinstance(text, str) or not text.strip()
                            or payload.get('kb_id') != kb_id):
                        raise RAGError('lexical_invalid_payload')
                    key = (doc['id'], chunk)
                    if key in seen:
                        raise RAGError('lexical_duplicate_chunk')
                    seen.add(key)
                    counts[doc['id']] += 1
                    # The canonical SQLite catalog controls display names;
                    # Qdrant may still hold the former name during a rename.
                    batch.append((doc['id'], doc['filename'], page, chunk, text[:4000]))
                conn.executemany(
                    'INSERT INTO chunks(document_id,filename,page,chunk_index,text) VALUES(?,?,?,?,?)', batch)
                progress(len(seen), total)
                iteration += 1
                if len(seen) > total or iteration > (MAX_INDEXED_CHUNKS // 128 + 200):
                    raise RAGError('lexical_inconsistent_collection')
                next_offset = result.get('next_page_offset')
                if next_offset is None:
                    break
                if next_offset == offset:
                    raise RAGError('lexical_invalid_vector_response')
                offset = next_offset
                await asyncio.sleep(0)
            if len(seen) != total or any(counts[docid] != doc['chunks'] for docid, doc in indexed.items()):
                raise RAGError('lexical_incomplete_index')
            # FTS5 has its own posting-list integrity operation, distinct
            # from the SQLite file-level PRAGMA check.
            conn.execute("INSERT INTO chunks(chunks) VALUES('integrity-check')")
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RAGError('lexical_integrity_failed')
            conn.commit()
            conn.close()
            conn = None
            # Never activate data built against a changed collection or catalog.
            if self.engine.reindex_in_progress or self._snapshot(kb_id)[1] != signature:
                raise RAGError('lexical_source_changed')
            self.engine._check_volume()
            generation = 'index-' + uuid4().hex + '.sqlite3'
            target = folder / generation
            os.replace(staging, target)
            payload = {'schema': 1, 'file': generation, 'snapshot': signature, 'chunks': total}
            fd, tmp = tempfile.mkstemp(prefix='.manifest-', suffix='.tmp', dir=folder)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(payload, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Readers use the immutable index referenced by this manifest.
                os.replace(tmp, folder / 'manifest.json')
            finally:
                Path(tmp).unlink(missing_ok=True)
            return {'state': 'ready', 'chunks': total}
        finally:
            if conn is not None:
                conn.close()
            for suffix in ('', '-journal', '-wal', '-shm'):
                staging.with_name(staging.name + suffix).unlink(missing_ok=True)

    def search(self, kb_id: str, question: str, limit: int) -> list[dict]:
        """Optional local retrieval; missing/stale/broken indexes simply fall back."""
        try:
            path = self._active_file(kb_id)
            if path is None:
                return []
            # Immutable generations never need SQLite writes, even on Windows.
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1',
                                         uri=True, timeout=5)) as db:
                selected = _distinctive_terms(db, question)
                if not selected:
                    return []
                # Multi-term OR is only candidate discovery. Every candidate is
                # independently checked for strong lexical evidence below.
                expression = ' OR '.join('"' + t.replace('"', '""') + '"'
                                         for t in selected)
                sql = ('SELECT document_id,filename,page,chunk_index,text,bm25(chunks) '
                       'FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?')
                row_limit = min(48, max(1, limit))
                rows = list(db.execute(sql, (expression, row_limit)))
                # Search full technical identifiers independently. Their parts
                # might be common enough for an OR query to miss the full code
                # within its first 48 candidates, despite an exact match.
                identifier_rows = []
                for code in _identifiers(question):
                    parts = TOKEN_RE.findall(code)
                    if not parts:
                        continue
                    # FTS5 phrase matching requires adjacent tokens. The
                    # final check below also verifies the original punctuation
                    # and boundaries, so HJX-987 cannot match HJX-9876 or
                    # HJX ... 987 elsewhere in the same troubleshooting page.
                    exact_query = '"' + ' '.join(parts).replace('"', '""') + '"'
                    identifier_rows.extend(db.execute(sql, (exact_query, row_limit)))
                unique_codes = {}
                for row in identifier_rows:
                    unique_codes.setdefault((row[0], row[3]), row)
                rows = list(unique_codes.values()) + [
                    row for row in rows if (row[0], row[3]) not in unique_codes
                ]
            kb = self.engine.get_kb(kb_id)
            with closing(self.engine._db()) as db:
                valid = {r['id'] for r in db.execute(
                    "SELECT id FROM documents WHERE kb_id=? AND status='indexed'", (kb['id'],))}
            matches = []
            for docid, filename, page, chunk, content, _ in rows:
                if docid not in valid:
                    continue
                weight = _lexical_strength(content, question, selected)
                if weight <= 0:
                    continue
                matches.append({'kb_id': kb['id'], 'kb_name': kb['name'],
                                'document_id': docid, 'filename': filename,
                                'page': int(page), 'chunk_index': int(chunk),
                                'text': content, 'score': None,
                                'retrieval': 'lexical', '_lexical_weight': weight})
            return matches
        except (OSError, sqlite3.Error, RAGError, ValueError, KeyError):
            # A lexical failure cannot make an otherwise healthy Chat unusable.
            return []
