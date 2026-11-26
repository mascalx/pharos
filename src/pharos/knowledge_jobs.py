"""Durable background ingestion jobs; one worker, nonblocking chat.

The file is staged before the API sends 202. Job metadata shares the existing
PHAROS jobs SQLite DB so all lengthy operations appear in one notification UI.
A process restart resumes queued/running uploads from the verified spool.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import UUID

from .download_jobs import DownloadJobs, JobError
from .knowledge_contract import RAGError
from .knowledge.rag import _safe_name
from .storage import check_data_volume

logger=logging.getLogger(__name__)


class KnowledgeJobs:
    def __init__(self, config, engine, jobs: DownloadJobs):
        self.config=config
        self.engine=engine
        self.jobs=jobs
        self.spool=config.storage.data_dir/'knowledge'/'.ingest-jobs'
        self._semaphore=asyncio.Semaphore(1)
        self._tasks: dict[str,asyncio.Task] = {}
        self._shutdown=False

    def _path(self, jobid: str) -> Path:
        if len(jobid)!=32 or any(c not in '0123456789abcdef' for c in jobid):
            raise JobError('job_unknown')
        return self.spool/(jobid+'.upload')

    async def submit(self,kb_id:str,filename:str,raw:bytes)->dict:
        self.engine._enabled()
        self.engine._write_allowed()
        kb_id=self.engine.get_kb(kb_id)['id']
        filename=_safe_name(filename)
        if not raw or len(raw)>self.engine.config.rag.max_document_mb*1024*1024:
            raise RAGError('document_empty_or_too_large')
        if not check_data_volume(self.config.storage).available:
            raise RAGError('data_volume_unavailable')
        if self.spool.is_symlink():
            raise JobError('unsafe_upload_directory')
        self.spool.mkdir(parents=True,exist_ok=True)
        sha=hashlib.sha256(raw).hexdigest()
        # One job for each content+KB pair. Avoid stacking duplicate requests.
        active=self.jobs.active_knowledge_jobs()
        for row in active:
            params=json.loads(row['params'])
            if params.get('kb_id')==kb_id and params.get('sha256')==sha:
                return {'id':row['id'],'status':row['status']}
        if len(active)>=24:
            raise JobError('ingest_queue_full')
        jobid=self.jobs.create('ingest-document',filename,{'kb_id':kb_id,'filename':filename,'sha256':sha})
        staged=self._path(jobid)
        # File is durable before worker scheduling; no background work relies on
        # a FastAPI UploadFile or a browser staying connected.
        try:
            fd,name=tempfile.mkstemp(prefix='.upload-',suffix='.tmp',dir=self.spool)
            try:
                with os.fdopen(fd,'wb') as out:
                    out.write(raw);out.flush();os.fsync(out.fileno())
                os.replace(name,staged)
            finally:
                Path(name).unlink(missing_ok=True)
        except Exception:
            self.jobs._change(jobid,status='failed',detail='ingest_stage_failed')
            raise
        self._schedule(jobid,{'kb_id':kb_id,'filename':filename,'sha256':sha})
        return {'id':jobid,'status':'queued'}

    def _schedule(self,jobid:str,params:dict):
        if jobid in self._tasks:
            return
        task=asyncio.create_task(self._run(jobid,params),name='pharos-ingest-'+jobid[:8])
        self._tasks[jobid]=task
        task.add_done_callback(lambda _task:self._tasks.pop(jobid,None))

    def resume(self):
        if self.engine is None:
            return
        if self.spool.is_symlink():
            for row in self.jobs.active_knowledge_jobs():
                self.jobs._change(row['id'],status='interrupted',detail='unsafe_upload_directory')
            return
        for row in self.jobs.active_knowledge_jobs():
            try:
                params=json.loads(row['params'])
                path=self._path(row['id'])
                if (not path.is_file() or path.is_symlink() or
                    str(UUID(params['kb_id']))!=params['kb_id']):
                    raise ValueError('unsafe_or_missing_spool')
                self.jobs._change(row['id'],status='queued',detail='resuming_ingest')
                self._schedule(row['id'],params)
            except (KeyError,ValueError,OSError,JobError):
                self.jobs._change(row['id'],status='interrupted',detail='ingest_spool_missing')

    async def _run(self,jobid:str,params:dict):
        try:
            async with self._semaphore:
                while self.engine.reindex_in_progress:
                    self.jobs._change(jobid,detail='waiting_for_reindex')
                    await asyncio.sleep(1)
                path=self._path(jobid)
                if path.is_symlink() or not path.is_file():
                    raise JobError('ingest_spool_missing')
                if not check_data_volume(self.config.storage).available:
                    raise JobError('data_volume_unavailable')
                raw=await asyncio.to_thread(path.read_bytes)
                if hashlib.sha256(raw).hexdigest()!=params['sha256']:
                    raise JobError('ingest_spool_hash_mismatch')
                self.jobs._change(jobid,status='running',detail='extracting',progress=0)
                ingest_task=asyncio.create_task(self.engine.ingest(params['kb_id'],params['filename'],raw))
                try:
                    while not ingest_task.done():
                        try:
                            docs=self.engine.documents(params['kb_id'])
                            matched=next((doc for doc in docs if doc['sha256']==params['sha256']),None)
                            if matched and matched['status']=='indexing':
                                total=matched.get('progress_total') or 0
                                done=matched.get('progress_done') or 0
                                stage=matched.get('progress_stage') or 'embedding'
                                percent=(int(done*45/total)+(45 if stage=='indexing' else 0)) if total else 0
                                self.jobs._change(jobid,detail=f'{stage}: {done}/{total}',
                                                  progress=min(percent,99),bytes_done=done,bytes_total=total)
                        except (OSError,RAGError):
                            pass
                        await asyncio.sleep(0.6)
                    result=await ingest_task
                except asyncio.CancelledError:
                    ingest_task.cancel()
                    try:
                        await ingest_task
                    except asyncio.CancelledError:
                        pass
                    raise
                self.jobs._change(jobid,status='completed',detail='',progress=100,
                                  bytes_done=result.get('chunks',0),bytes_total=result.get('chunks',0))
                path.unlink(missing_ok=True)
        except asyncio.CancelledError:
            if self._shutdown:
                # Retain the spool and job for resume after a service restart.
                self.jobs._change(jobid,status='queued',detail='pharos_restarted')
            else:
                self.jobs._change(jobid,status='cancelled',detail='job_cancelled')
                self._path(jobid).unlink(missing_ok=True)
        except Exception as exc:
            logger.exception('Knowledge background ingestion failed: %s',type(exc).__name__)
            reason=str(exc) if isinstance(exc,(RAGError,JobError)) else type(exc).__name__
            self.jobs._change(jobid,status='failed',detail=reason[:128])
            # Preserve the spool on failure so a restart/retry can be diagnosed.

    def cancel(self,jobid:str)->dict:
        with self.jobs._db() as db:
            row=db.execute('SELECT status,kind FROM jobs WHERE id=?',(jobid,)).fetchone()
        if not row or row['kind']!='ingest-document':
            raise JobError('job_unknown')
        if row['status'] not in ('queued','running'):
            return {'id':jobid,'status':row['status']}
        task=self._tasks.get(jobid)
        if task:
            task.cancel()
        else:
            self.jobs._change(jobid,status='cancelled',detail='job_cancelled')
            self._path(jobid).unlink(missing_ok=True)
        return {'id':jobid,'status':'cancelling'}

    def stop(self):
        self._shutdown=True
        for task in list(self._tasks.values()):
            task.cancel()
