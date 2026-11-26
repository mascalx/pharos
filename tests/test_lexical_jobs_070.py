"""Hermetic tests for durable lexical indexing jobs (no Qdrant/Tiiny)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from pharos.lexical_jobs import LexicalJobs


class FakeJobs:
    def __init__(self, root: Path):
        self.path = root / 'jobs.sqlite3'
        with self._db() as db:
            db.execute('CREATE TABLE jobs (id TEXT,kind TEXT,status TEXT,label TEXT,'
                       'detail TEXT DEFAULT "", progress INTEGER DEFAULT 0,'
                       'bytes_done INTEGER DEFAULT 0, bytes_total INTEGER,params TEXT)')

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def create(self, kind, label, params):
        job_id = uuid4().hex
        with self._db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,label,params) VALUES(?,?,?,?,?)',
                       (job_id,kind,'queued',label,json.dumps(params)))
        return job_id

    def _change(self, job_id, **updates):
        with self._db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in updates) + ' WHERE id=?',
                       (*updates.values(), job_id))

    def get(self, job_id):
        with self._db() as db:
            return dict(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


class FakeLexical:
    def __init__(self):
        self.runs = 0
        self.inflight = asyncio.Event()

    async def build(self, kb_id, job_id, progress):
        self.runs += 1
        progress(1, 2)
        await asyncio.sleep(0.02)
        progress(2, 2)
        return {'state': 'ready', 'chunks': 2}


class FakeEngine:
    def __init__(self):
        self.kb_id = str(uuid4())
        self.reindex_in_progress = False
        self.lexical = FakeLexical()

    def _enabled(self):
        pass

    def _check_volume(self):
        pass

    def get_kb(self, kb_id):
        if str(kb_id).lower() != self.kb_id:
            raise ValueError('bad KB')
        return {'id': self.kb_id, 'name': 'test'}

    def documents(self, kb_id):
        return [{'status': 'indexed'}]


async def wait_terminal(jobs, job_id):
    deadline = time.monotonic() + 5
    while True:
        record = jobs.get(job_id)
        if record['status'] not in ('queued', 'running'):
            return record
        if time.monotonic() >= deadline:
            raise AssertionError(f"Job did not finish: {record}")
        await asyncio.sleep(0.02)


def test_background_build_and_terminal_job_status(tmp_path):
    async def scenario():
        engine, jobs = FakeEngine(), FakeJobs(tmp_path)
        coordinator = LexicalJobs(engine, jobs)
        entry = coordinator.submit(engine.kb_id)
        duplicate = coordinator.submit(engine.kb_id)
        assert duplicate['id'] == entry['id']
        record = await wait_terminal(jobs, entry['id'])
        assert record['status'] == 'completed'
        assert record['progress'] == 100
        assert engine.lexical.runs == 1
        coordinator.stop()
    asyncio.run(scenario())


def test_queued_job_recovers_after_restart(tmp_path):
    async def scenario():
        engine, jobs = FakeEngine(), FakeJobs(tmp_path)
        jobid = jobs.create('build-lexical', 'test', {'kb_id':engine.kb_id})
        jobs._change(jobid, status='running', detail='interrupted_by_process')
        recovered = LexicalJobs(engine, jobs)
        recovered.resume()
        assert (await wait_terminal(jobs, jobid))['status'] == 'completed'
        assert engine.lexical.runs == 1
        recovered.stop()
    asyncio.run(scenario())


def test_cancel_before_first_execution_is_terminal(tmp_path):
    async def scenario():
        engine, jobs = FakeEngine(), FakeJobs(tmp_path)
        coordinator = LexicalJobs(engine, jobs)
        entry = coordinator.submit(engine.kb_id)
        coordinator.cancel(entry['id'])
        await asyncio.sleep(0)
        assert jobs.get(entry['id'])['status'] == 'cancelled'
        coordinator.stop()
    asyncio.run(scenario())
