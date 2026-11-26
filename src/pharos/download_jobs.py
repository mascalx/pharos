"""Persisted background operations for ZIM content.

A single persistent FIFO worker downloads one ZIM at a time while accepting
additional requests. Queued and running downloads resume automatically after
PHAROS restarts. Verified .part files are kept for resumable HTTP Range downloads.
Notification events are emitted only when a job reaches a terminal state.
No unrestricted URLs, shell execution or non-official third-party sources.
"""
from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
import os
import re
import sqlite3
import threading
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit, unquote

from .kiwix_runtime import KiwixRuntimeError, _PublisherRedirects
from .storage import check_data_volume

ACTIVE = ('queued', 'running')
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')
OFFICIAL_ZIM_HOSTS = {'download.kiwix.org', 'mirror.download.kiwix.org', 'ftp.fau.de', 'www.ftp.fau.de',
                      'www.ftp.uni-erlangen.de', 'ftp.rrze.uni-erlangen.de'}


class JobError(ValueError):
    pass


def validate_zim_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise JobError('zim_official_url_required') from exc
    if (parsed.scheme != 'https' or parsed.hostname not in OFFICIAL_ZIM_HOSTS or
        parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
        or not parsed.path.startswith(('/zim/', '/kiwix/zim/')) or '..' in unquote(parsed.path).split('/') or parsed.query or parsed.fragment):
        raise JobError('zim_official_url_required')
    name = unquote(parsed.path.rsplit('/', 1)[-1])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,190}\.zim', name) or '..' in name:
        raise JobError('zim_filename_invalid')
    return name


class DownloadJobs:
    """Persistent per-process job scheduler; one download at a time, no UI blocking."""
    def __init__(self, config, kiwix_service):
        self.config = config
        self.kiwix_service = kiwix_service
        self.dbfile = config.storage.database_dir / 'pharos-jobs.sqlite3'
        self._lock = threading.RLock()
        self._cancel: dict[str, threading.Event] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.dbfile.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                label TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                progress INTEGER NOT NULL DEFAULT 0, bytes_done INTEGER NOT NULL DEFAULT 0,
                bytes_total INTEGER, params TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)''')
            # Keep queued and running ZIMs durable. The same URL produces the
            # same .part file; restart can continue from the existing offset.
            # Never revive deliberate user cancellations or failed downloads.
            columns={r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'read_at' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN read_at INTEGER NOT NULL DEFAULT 0')
            db.execute("""UPDATE jobs SET status='queued',detail='resuming_download',updated_at=?
                          WHERE status='running' AND kind='download-zim'""",
                       (int(time.time()),))
            # Migrate older pre-queue deployments that marked an in-flight ZIM
            # as interrupted solely due to a PHAROS restart.
            db.execute("""UPDATE jobs SET status='queued',detail='resuming_download',read_at=0,updated_at=?
                          WHERE kind='download-zim' AND status='interrupted'
                          AND detail='pharos_restarted' AND read_at=0""",(int(time.time()),))
            db.execute("""UPDATE jobs SET status='interrupted',detail='pharos_restarted',updated_at=?
                          WHERE status IN ('queued','running') AND kind='reindex-rag'""",
                       (int(time.time()),))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.dbfile, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _change(self, jobid: str, **items):
        with self._lock, self._db() as db:
            fields = ','.join(f'{key}=?' for key in items)
            db.execute(f'UPDATE jobs SET {fields},updated_at=? WHERE id=?', (*items.values(), int(time.time()), jobid))

    def list(self, limit=60) -> list[dict]:
        with self._lock, self._db() as db:
            rows = db.execute('SELECT rowid AS queue_sequence,id,kind,status,label,detail,progress,bytes_done,bytes_total,created_at,updated_at,read_at,params FROM jobs ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
            results=[]
            for row in rows:
                item=dict(row)
                params=json.loads(item.pop('params'))
                item['target_view'] = ('knowledge' if item['kind'] in ('ingest-document','reindex-rag','build-lexical')
                                       else 'maps' if item['kind'] in ('download-map','index-map') else 'kiwix')
                if item['kind'] in ('ingest-document','build-lexical'):
                    item['kb_id']=params.get('kb_id','')
                results.append(item)
            return results

    def has_active_zim_download(self) -> bool:
        with self._lock, self._db() as db:
            row=db.execute("SELECT 1 FROM jobs WHERE kind='download-zim' "
                           "AND status IN ('queued','running') LIMIT 1").fetchone()
            return row is not None

    def create(self, kind: str, label: str, params: dict) -> str:
        """Reusable durable job/notification entry. Never include secrets in params."""
        if kind not in ('ingest-document', 'reindex-rag', 'build-lexical'):
            raise JobError('job_kind_invalid')
        jobid=uuid.uuid4().hex
        now=int(time.time())
        with self._lock,self._db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,label,params,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                       (jobid,kind,'queued',label,json.dumps(params,ensure_ascii=False),now,now))
        return jobid

    def mark_read(self, jobid: str) -> dict:
        with self._lock,self._db() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(jobid,)).fetchone()
            if row is None:
                raise JobError('job_unknown')
            if row['status'] in TERMINAL:
                db.execute('UPDATE jobs SET read_at=? WHERE id=?',(int(time.time()),jobid))
            return {'id':jobid,'status':row['status']}

    def active_knowledge_jobs(self) -> list[dict]:
        with self._lock,self._db() as db:
            rows=db.execute("SELECT id,label,params,status FROM jobs WHERE kind='ingest-document' AND status IN ('queued','running') ORDER BY created_at,id").fetchall()
            return [dict(row) for row in rows]

    def resume(self) -> None:
        """Start the single FIFO worker, picking up durable jobs after reboot."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._work_loop,daemon=True,
                             name='pharos-zim-download-queue')
            self._worker.start()
            self._wake.set()

    def stop(self) -> None:
        """Request graceful pause, leaving the current .part and queue intact."""
        self._stop.set()
        self._wake.set()
        worker=self._worker
        if worker and worker.is_alive():
            worker.join(timeout=2)

    def submit(self, kind: str, url: str = '') -> dict:
        if kind != 'download-zim':
            raise JobError('job_kind_invalid')
        filename = validate_zim_url(url)
        with self._lock:
            if (self.kiwix_service.zim_dir / filename).exists():
                raise JobError('zim_already_exists')
            with self._db() as db:
                # Reject double clicks, duplicate archive versions and two
                # acquisition URLs with the same destination filename.
                active=db.execute("SELECT label FROM jobs WHERE kind='download-zim' AND status IN ('queued','running')").fetchall()
                if any(row['label']==filename for row in active):
                    raise JobError('zim_already_queued')
                jobid=uuid.uuid4().hex
                now=int(time.time())
                db.execute('INSERT INTO jobs(id,kind,status,label,params,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                           (jobid,kind,'queued',filename,json.dumps({'url':url}),now,now))
            self.resume()
            self._wake.set()
        return {'id':jobid,'status':'queued'}

    def cancel(self, jobid: str):
        with self._lock,self._db() as db:
            row=db.execute('SELECT status,kind FROM jobs WHERE id=?',(jobid,)).fetchone()
            if row is None or row['kind']!='download-zim':
                raise JobError('job_unknown')
            if row['status'] not in ACTIVE:
                return {'id':jobid,'status':row['status']}
            if row['status']=='queued':
                db.execute("UPDATE jobs SET status='cancelled',detail='job_cancelled',updated_at=? WHERE id=?",
                           (int(time.time()),jobid))
                return {'id':jobid,'status':'cancelled'}
            signal=self._cancel.get(jobid)
            if signal:
                signal.set()
            return {'id':jobid,'status':'cancelling'}

    def _work_loop(self):
        while not self._stop.is_set():
            with self._lock,self._db() as db:
                row=db.execute("""SELECT id,params FROM jobs WHERE kind='download-zim'
                     AND status='queued' ORDER BY created_at,rowid LIMIT 1""").fetchone()
                if row is not None:
                    jobid=row['id']
                    url=json.loads(row['params'])['url']
                    cancel=threading.Event()
                    self._cancel[jobid]=cancel
                    db.execute("UPDATE jobs SET status='running',detail='',updated_at=? WHERE id=?",
                               (int(time.time()),jobid))
            if row is None:
                self._wake.wait(1)
                self._wake.clear()
                continue
            try:
                self._download_zim(jobid,url,cancel)
                # A shutdown arriving before final confirmation must not
                # generate a misleading success event.
                self._change(jobid,status='completed',detail='',progress=100)
            except Exception as exc:
                if str(exc)=='job_cancelled':
                    self._change(jobid,status='cancelled',detail='job_cancelled')
                elif str(exc)=='download_paused' or self._stop.is_set():
                    self._change(jobid,status='queued',detail='resuming_download')
                else:
                    reason=str(exc) if isinstance(exc,(JobError,KiwixRuntimeError)) else type(exc).__name__
                    self._change(jobid,status='failed',detail=reason[:128])
            finally:
                with self._lock:
                    self._cancel.pop(jobid,None)

    def _update_progress(self, jobid: str, done: int, total: int | None, cancel: threading.Event):
        if cancel.is_set():
            raise JobError('job_cancelled')
        percentage = min(99, int(done * 100 / total)) if total else 0
        self._change(jobid, progress=percentage, bytes_done=done, bytes_total=total)

    def _run(self, jobid: str, kind: str, url: str, cancel: threading.Event):
        try:
            self._change(jobid, status='running')
            self._download_zim(jobid, url, cancel)
            self._change(jobid, status='completed', detail='', progress=100)
        except Exception as exc:
            status = 'cancelled' if str(exc)=='job_cancelled' else 'failed'
            # Log only known public error types; avoid leaking secrets and local paths.
            reason = str(exc) if isinstance(exc, (JobError, KiwixRuntimeError)) else type(exc).__name__
            self._change(jobid, status=status, detail=reason[:128])
        finally:
            with self._lock:
                self._cancel.pop(jobid,None)

    def _download_zim(self, jobid: str, url: str, cancel: threading.Event):
        filename = validate_zim_url(url)
        service = self.kiwix_service
        service.prepare_data()
        if not check_data_volume(self.config.storage).available:
            raise JobError('data_volume_unavailable')
        destination = service.zim_dir / filename
        if destination.exists() or destination.is_symlink():
            raise JobError('zim_already_exists')
        partdir = service.data_dir / '.downloads'
        if partdir.is_symlink():
            raise JobError('unsafe_download_directory')
        partdir.mkdir(parents=True, exist_ok=True)
        partial = partdir / (hashlib.sha256(url.encode()).hexdigest()[:16] + '-' + filename + '.part')
        if partial.is_symlink():
            raise JobError('unsafe_partial_file')
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {'User-Agent':'PHAROS/0.5 Kiwix official ZIM download'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())
        # Official ZIM artifacts publish SHA-256 checksums; refuse files with
        # missing/malformed checksums rather than publishing unverifiable data.
        try:
            with opener.open(urllib.request.Request(url+'.sha256', headers={'User-Agent':'PHAROS/0.5'}), timeout=45) as checksum_response:
                checksum_text = checksum_response.read(4096).decode('ascii',errors='replace')
            expected = re.search(r'(?<![a-fA-F0-9])[a-fA-F0-9]{64}(?![a-fA-F0-9])',checksum_text)
            if not expected:
                raise JobError('zim_checksum_unavailable')
            expected_sha = expected.group(0).lower()
            # A socket timeout is not a global download deadline. For very
            # large ZIMs, reconnect from the bytes actually written to disk
            # instead of failing a multi-hour transfer on a transient stall.
            retries = 0
            max_retries = 12
            while True:
                if cancel.is_set():
                    raise JobError('job_cancelled')
                if self._stop.is_set():
                    raise JobError('download_paused')
                offset = partial.stat().st_size if partial.exists() else 0
                segment_headers = dict(headers)
                segment_headers.pop('Range', None)
                if offset:
                    segment_headers['Range'] = f'bytes={offset}-'
                try:
                    request = urllib.request.Request(url, headers=segment_headers)
                    with opener.open(request, timeout=45) as reply:
                        content_range = reply.headers.get('Content-Range', '')
                        if not offset and reply.status == 206:
                            raise JobError('zim_unrequested_partial_content')
                        if offset and reply.status == 206 and not content_range.startswith(f'bytes {offset}-'):
                            raise JobError('zim_range_mismatch')
                        # Some servers ignore Range: restart safely rather than
                        # appending a complete response to the partial archive.
                        append = bool(offset and reply.status == 206)
                        if not append:
                            offset = 0
                        declared = reply.headers.get('Content-Length', '')
                        remaining = int(declared) if declared.isdigit() else None
                        total = offset + remaining if remaining is not None else None
                        done = offset
                        with partial.open('ab' if append else 'wb') as output:
                            while True:
                                if cancel.is_set():
                                    raise JobError('job_cancelled')
                                if self._stop.is_set():
                                    raise JobError('download_paused')
                                data = reply.read(1024 * 1024)
                                if not data:
                                    break
                                output.write(data)
                                done += len(data)
                                self._update_progress(jobid, done, total, cancel)
                            output.flush()
                            os.fsync(output.fileno())
                        if total is not None and done != total:
                            raise JobError('zim_download_incomplete')
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError,
                        OSError, JobError) as exc:
                    # Integrity/protocol errors, cancellation and unavailable
                    # storage are not transient connection failures.
                    if isinstance(exc, JobError) and str(exc) != 'zim_download_incomplete':
                        raise
                    if cancel.is_set():
                        raise JobError('job_cancelled') from exc
                    if self._stop.is_set():
                        raise JobError('download_paused') from exc
                    retries += 1
                    if retries > max_retries:
                        raise JobError('zim_download_failed') from exc
                    self._change(jobid, detail='retrying_download')
                    # Interruptible backoff; the partial file remains intact.
                    delay = min(2 ** min(retries, 5), 30)
                    while delay > 0:
                        if cancel.is_set():
                            raise JobError('job_cancelled') from exc
                        if self._stop.wait(min(1, delay)):
                            raise JobError('download_paused') from exc
                        delay -= 1
            if self._stop.is_set():
                raise JobError('download_paused')
            self._change(jobid, detail='verifying_sha256', progress=99)
            digest=hashlib.sha256()
            with partial.open('rb') as src:
                while chunk := src.read(4*1024*1024):
                    if cancel.is_set():
                        raise JobError('job_cancelled')
                    if self._stop.is_set():
                        raise JobError('download_paused')
                    digest.update(chunk)
            if digest.hexdigest().lower() != expected_sha:
                partial.unlink(missing_ok=True)  # stale/corrupt partial must not poison a retry
                raise JobError('zim_checksum_mismatch')
            if not check_data_volume(self.config.storage).available:
                raise JobError('data_volume_unavailable')
            os.replace(partial,destination)
            # Launch/relaunch to discover newly added ZIMs without a Core restart.
            if service.binary_installed():
                service.ensure_running(self.config.kiwix.server_url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JobError('zim_download_failed') from exc
