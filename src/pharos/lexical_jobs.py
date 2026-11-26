"""Restartable, background FTS5 indexing using PHAROS' existing durable jobs DB.

Jobs are rebuilt from scratch after a restart; a previously published lexical
generation is retained until a complete replacement passes validation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from pharos.download_jobs import JobError
from pharos.knowledge_contract import RAGError

logger = logging.getLogger(__name__)


class LexicalJobs:
    def __init__(self, engine, jobs):
        self.engine = engine
        self.jobs = jobs
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(1)
        self._shutdown = False

    def _active(self) -> list[dict]:
        with self.jobs._db() as db:
            rows = db.execute(
                "SELECT id,status,params FROM jobs WHERE kind='build-lexical' "
                "AND status IN ('queued','running') ORDER BY rowid").fetchall()
        return [dict(row) for row in rows]

    def submit(self, kb_id: str) -> dict:
        self.engine._enabled()
        self.engine._check_volume()
        kb_id = self.engine.get_kb(kb_id)['id']
        if self.engine.reindex_in_progress:
            raise RAGError('reindex_in_progress')
        if any(doc['status'] == 'indexing' for doc in self.engine.documents(kb_id)):
            raise RAGError('document_busy')
        for row in self._active():
            if json.loads(row['params']).get('kb_id') == kb_id:
                return {'id': row['id'], 'status': row['status']}
        if len(self._active()) >= 8:
            raise JobError('lexical_queue_full')
        job_id = self.jobs.create('build-lexical', self.engine.get_kb(kb_id)['name'],
                                  {'kb_id': kb_id})
        self._schedule(job_id, kb_id)
        return {'id': job_id, 'status': 'queued'}

    def _schedule(self, job_id, kb_id):
        if job_id in self._tasks:
            return
        task = asyncio.create_task(self._run(job_id, kb_id), name='pharos-lexical-'+job_id[:8])
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    def resume(self):
        for row in self._active():
            try:
                kb_id = json.loads(row['params'])['kb_id']
                if str(UUID(kb_id)) != kb_id:
                    raise ValueError('invalid_id')
                self.engine.get_kb(kb_id)
                self.jobs._change(row['id'], status='queued', detail='resuming_lexical',
                                  progress=0, bytes_done=0)
                self._schedule(row['id'], kb_id)
            except (RAGError, ValueError, KeyError, TypeError):
                self.jobs._change(row['id'], status='interrupted', detail='lexical_kb_unavailable')

    async def _run(self, job_id: str, kb_id: str):
        try:
            async with self._semaphore:
                while self.engine.reindex_in_progress:
                    self.jobs._change(job_id, detail='waiting_for_reindex')
                    await asyncio.sleep(1)
                self.jobs._change(job_id, status='running', detail='lexical_scanning', progress=0)
                def progress(done, total):
                    self.jobs._change(job_id, detail=f'lexical_scanning: {done}/{total}',
                                      progress=min(99, int(done*100/total)),
                                      bytes_done=done, bytes_total=total)
                await self.engine.lexical.build(kb_id, job_id, progress)
                self.jobs._change(job_id, status='completed', progress=100,
                                  detail='lexical_ready')
        except asyncio.CancelledError:
            if self._shutdown:
                self.jobs._change(job_id, status='queued', detail='pharos_restarted')
            else:
                self.jobs._change(job_id, status='cancelled', detail='job_cancelled')
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (RAGError,JobError)) else type(exc).__name__
            logger.exception('Lexical index build failed (%s)', type(exc).__name__)
            self.jobs._change(job_id, status='failed', detail=reason[:128])

    def cancel(self, job_id: str) -> dict:
        with self.jobs._db() as db:
            row = db.execute('SELECT kind,status FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['kind'] != 'build-lexical':
            raise JobError('job_unknown')
        if row['status'] not in ('queued', 'running'):
            return {'id': job_id, 'status': row['status']}
        task = self._tasks.get(job_id)
        # Mark the job before cancelling: a queued task may never enter _run()
        # and therefore never reach its CancelledError handler.
        self.jobs._change(job_id, status='cancelled', detail='job_cancelled')
        if task:
            task.cancel()
        return {'id': job_id, 'status': 'cancelling'}

    def stop(self):
        self._shutdown = True
        for task in list(self._tasks.values()):
            task.cancel()
