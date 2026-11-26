"""Shadow-index migration for all Knowledge Bases.

Existing indexes stay active until every document is embedded in a separate
Qdrant collection. Never destroy existing data on an unsuccessful migration.
"""
from __future__ import annotations
from pharos.ai import background_inference

import asyncio
import json
import logging
from uuid import uuid4, uuid5, NAMESPACE_URL

from pharos.config_edit import save_settings, settings_values
from pharos.config import load_config
from .rag import RAGEngine, RAGError, extract_pages, chunk_pages

log = logging.getLogger(__name__)


class ReindexService:
    def __init__(self, engine: RAGEngine):
        self.engine = engine
        self.task: asyncio.Task | None = None
        with self.engine._db() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(reindex_jobs)')}
            if 'staging_collections' not in columns:
                db.execute("ALTER TABLE reindex_jobs ADD COLUMN staging_collections TEXT NOT NULL DEFAULT '[]'")
            # Only the service owner marks jobs stale at server startup; an
            # unrelated CLI process creating RAGEngine must not reset active jobs.
            db.execute("UPDATE reindex_jobs SET state='interrupted',error='server_restarted',finished=CURRENT_TIMESTAMP WHERE state='running'")

    def status(self) -> dict:
        with self.engine._db() as db:
            row = db.execute('SELECT * FROM reindex_jobs ORDER BY started DESC, rowid DESC LIMIT 1').fetchone()
        return {'active': self.engine.reindex_in_progress, 'job': dict(row) if row else None}

    async def start(self, model: str, expected_revision: str) -> dict:
        if self.engine.reindex_in_progress:
            raise RAGError('reindex_in_progress')
        if not self.engine.config.rag.enabled:
            raise RAGError('rag_disabled')
        model = model.strip()
        if not model or len(model) > 256:
            raise RAGError('invalid_embedding_model')
        has_unmigrated = any(kb['indexed_documents'] and kb['embedding_model'] != model
                             for kb in self.engine.list_kbs())
        if model == self.engine.embedding_model and not has_unmigrated:
            raise RAGError('embedding_model_unchanged')
        # Never build indexes with stale chunking, storage, gateway or Qdrant parameters.
        on_disk = load_config(self.engine.config.source)
        from dataclasses import replace
        effective = replace(on_disk, rag=replace(on_disk.rag, embedding_model=self.engine.config.rag.embedding_model))
        if effective != self.engine.config:
            raise RAGError('restart_required_before_reindex')
        cfg = settings_values(self.engine.config.source)
        if cfg['revision'] != expected_revision:
            raise RAGError('config_changed_reload')
        # Clean staging indexes left behind by a previous interrupted job.
        # Never delete a collection referenced by an active KB.
        active = {self.engine._collection(kb['id']) for kb in self.engine.list_kbs()}
        with self.engine._db() as db:
            abandoned = list(db.execute("SELECT id,staging_collections FROM reindex_jobs WHERE state IN ('interrupted','failed') AND staging_collections!='[]'"))
        for stale in abandoned:
            names = json.loads(stale['staging_collections'])
            for name in names:
                if name not in active:
                    await self.engine._qdrant('DELETE', '/collections/' + name, missing_ok=True)
            with self.engine._db() as db:
                db.execute('UPDATE reindex_jobs SET staging_collections=? WHERE id=?', ('[]', stale['id']))
        # Confirm the candidate is actually supported for embeddings, not merely listed.
        probe = (await self.engine.gateway.embeddings(model, ['Test embedding PHAROS']))[0]
        if not probe or len(probe) > 8192:
            raise RAGError('invalid_embedding_model')
        self.engine._check_volume()
        with self.engine._db() as db:
            busy = db.execute("SELECT COUNT(*) FROM documents WHERE status='indexing'").fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM documents WHERE status='indexed'").fetchone()[0]
            if busy:
                raise RAGError('document_busy')
            job_id = str(uuid4())
            db.execute('INSERT INTO reindex_jobs (id,model,state,total) VALUES (?,?,?,?)',
                       (job_id, model, 'running', total))
        self.engine.reindex_in_progress = True
        self.task = asyncio.create_task(self._run(job_id, model, expected_revision))
        return {'id': job_id, 'model': model, 'total': total, 'state': 'running'}

    async def _run(self, job_id: str, model: str, expected_revision: str):
        engine = self.engine
        staged: dict[str, tuple[str, int]] = {}
        previous: list[str] = []
        switched = False
        error = ''
        try:
            for kb in engine.list_kbs():
                documents = [doc for doc in engine.documents(kb['id']) if doc['status'] == 'indexed']
                if not documents:
                    continue
                collection = 'pharos_kb_' + kb['id'].replace('-', '') + '_r_' + job_id[:12].replace('-', '')
                dimensions = 0
                for doc in documents:
                    # Re-extract originals to avoid depending on stale chunk payloads.
                    filename, original = engine.get_original(kb['id'], doc['id'])
                    chunks = chunk_pages(extract_pages(filename, original),
                                         engine.config.rag.chunk_chars, engine.config.rag.overlap_chars)
                    if not chunks:
                        raise RAGError('no_extractable_text')
                    for start in range(0, len(chunks), 8):
                        engine._check_volume()
                        segment = chunks[start:start + 8]
                        with background_inference():
                            vectors = await engine.gateway.embeddings(model, [content for _, content in segment])
                        if len(vectors) != len(segment):
                            raise RAGError('invalid_embedding_response')
                        if not dimensions:
                            dimensions = len(vectors[0])
                            staged[kb['id']] = (collection, dimensions)
                            with engine._db() as db:
                                db.execute('UPDATE reindex_jobs SET staging_collections=? WHERE id=?',
                                           (json.dumps([c for c, _ in staged.values()]), job_id))
                            await engine._qdrant('PUT', '/collections/' + collection,
                                                 {'vectors': {'size': dimensions, 'distance': 'Cosine'}})
                        if any(len(vec) != dimensions for vec in vectors):
                            raise RAGError('embedding_dimensions_mismatch')
                        points = [{'id': str(uuid5(NAMESPACE_URL, f'{doc["id"]}:{start + i}')),
                                   'vector': vector,
                                   'payload': {'document_id': doc['id'], 'kb_id': kb['id'],
                                               'filename': filename, 'page': chunk[0],
                                               'chunk_index': start + i, 'text': chunk[1]}}
                                  for i, (chunk, vector) in enumerate(zip(segment, vectors))]
                        await engine._qdrant('PUT', '/collections/' + collection + '/points?wait=true',
                                             {'points': points})
                    with engine._db() as db:
                        db.execute('UPDATE reindex_jobs SET completed=completed+1 WHERE id=?', (job_id,))
            # Settings are saved before the atomic index mapping switch. If interrupted
            # between these steps, per-KB model metadata still makes old indexes usable.
            values = settings_values(engine.config.source)['values']
            values['rag']['embedding_model'] = model
            save_settings(engine.config, values, expected_revision, )
            with engine._db() as db:
                for kb in engine.list_kbs():
                    if kb['id'] in staged:
                        collection, dims = staged[kb['id']]
                        previous.append(engine._collection(kb['id']))
                        db.execute('UPDATE kb SET embedding_model=?,dimensions=?,active_collection=? WHERE id=?',
                                   (model, dims, collection, kb['id']))
                    elif kb['dimensions'] and kb['indexed_documents'] == 0:
                        # A KB with all documents deleted must not retain a stale model.
                        previous.append(engine._collection(kb['id']))
                        db.execute("UPDATE kb SET embedding_model='',dimensions=NULL,active_collection='' WHERE id=?",
                                   (kb['id'],))
                db.execute('UPDATE reindex_jobs SET state=?,finished=CURRENT_TIMESTAMP WHERE id=?', ('complete', job_id))
            engine.embedding_model = model
            # The shadow collections were switched successfully.  The new
            # embedding identity is now authoritative in the live engine too;
            # no server restart is needed for the next reindex/setting edit.
            from dataclasses import replace
            engine.config = replace(engine.config, rag=replace(engine.config.rag, embedding_model=model))
            switched = True
        except asyncio.CancelledError:
            error = 'server_restarted'
        except Exception as exc:
            error = str(exc)[:180]
            log.exception('Knowledge Base reindex failed')
        finally:
            if not switched:
                with engine._db() as db:
                    db.execute('UPDATE reindex_jobs SET state=?,error=?,finished=CURRENT_TIMESTAMP WHERE id=?',
                               ('interrupted' if error == 'server_restarted' else 'failed', error, job_id))
                for collection, _ in staged.values():
                    try:
                        await engine._qdrant('DELETE', '/collections/' + collection, missing_ok=True)
                    except Exception:
                        log.warning('Unable to clean up staging collection %s', collection)
            engine.reindex_in_progress = False
            if switched:
                for collection in previous:
                    try:
                        await engine._qdrant('DELETE', '/collections/' + collection, missing_ok=True)
                    except Exception:
                        log.warning('Old collection retained after successful reindex: %s', collection)
