"""Regression: multi-ZIM persistent FIFO, shutdown recovery and job notification semantics."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from pharos.config import load_config
from pharos.download_jobs import DownloadJobs, JobError
from pharos.kiwix_runtime import KiwixManagedService

BASE='https://download.kiwix.org/zim/wikipedia/'

def jobs_for(prepared):
    cfg=load_config(prepared)
    return DownloadJobs(cfg,KiwixManagedService(cfg))

def until(predicate,timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.02)
    return False


def test_two_zims_are_fifo_and_second_is_queued(prepared,monkeypatch):
    started=threading.Event();release=threading.Event();called=[]
    def download(self,jobid,url,cancel):
        called.append(url)
        if 'first' in url:
            started.set();assert release.wait(3)
    monkeypatch.setattr(DownloadJobs,'_download_zim',download)
    jobs=jobs_for(prepared)
    try:
        first=jobs.submit('download-zim',BASE+'first.zim')['id']
        assert started.wait(3)
        second=jobs.submit('download-zim',BASE+'second.zim')['id']
        assert {r['id']:r['status'] for r in jobs.list()}[second]=='queued'
        assert called==[BASE+'first.zim']
        assert jobs.has_active_zim_download()
        release.set()
        assert until(lambda:all(r['status']=='completed' for r in jobs.list() if r['id'] in (first,second)))
        assert called==[BASE+'first.zim',BASE+'second.zim']
        assert not jobs.has_active_zim_download()
        assert all(r['read_at']==0 for r in jobs.list())
    finally:
        release.set();jobs.stop()


def test_restart_preserves_fifo_and_unread_state(prepared,monkeypatch):
    original_resume=DownloadJobs.resume
    monkeypatch.setattr(DownloadJobs,'resume',lambda self:None)
    jobs=jobs_for(prepared)
    first=jobs.submit('download-zim',BASE+'first.zim')['id']
    second=jobs.submit('download-zim',BASE+'second.zim')['id']
    jobs._change(first,status='running',progress=47,bytes_done=1925)
    assert {r['id']:r['status'] for r in jobs.list()}[second]=='queued'
    # Simulate process restart: constructor must restore running job to queue,
    # never produce an unread interrupted error notification.
    rebooted=jobs_for(prepared)
    state={r['id']:r for r in rebooted.list()}
    assert state[first]['status']=='queued' and state[second]['status']=='queued'
    assert state[first]['detail']=='resuming_download' and state[first]['read_at']==0
    assert state[first]['bytes_done']==1925
    called=[]
    monkeypatch.setattr(DownloadJobs,'resume',original_resume)
    monkeypatch.setattr(DownloadJobs,'_download_zim',lambda self,jobid,url,cancel:called.append(url))
    try:
        rebooted.resume()
        assert until(lambda:len(called)==2)
        assert called==[BASE+'first.zim',BASE+'second.zim']
        assert until(lambda:all(x['status']=='completed' for x in rebooted.list()))
    finally:rebooted.stop()


def test_duplicate_and_queued_cancellation(prepared,monkeypatch):
    started=threading.Event();release=threading.Event();called=[]
    def download(self,jobid,url,cancel):
        called.append(url);started.set();assert release.wait(3)
    monkeypatch.setattr(DownloadJobs,'_download_zim',download)
    jobs=jobs_for(prepared)
    try:
        jobs.submit('download-zim',BASE+'one.zim')
        assert started.wait(3)
        with pytest.raises(JobError,match='zim_already_queued'):
            jobs.submit('download-zim',BASE+'one.zim')
        queued=jobs.submit('download-zim',BASE+'two.zim')['id']
        assert jobs.cancel(queued)['status']=='cancelled'
        release.set();assert until(lambda:not jobs.has_active_zim_download())
        assert called==[BASE+'one.zim']
        assert any(r['id']==queued and r['status']=='cancelled' for r in jobs.list())
    finally:release.set();jobs.stop()


def test_worker_graceful_pause_then_resume_without_false_completion(prepared,monkeypatch):
    started=threading.Event();seen=[]
    def download(self,jobid,url,cancel):
        started.set()
        assert until(lambda:self._stop.is_set())
        raise JobError('download_paused')
    monkeypatch.setattr(DownloadJobs,'_download_zim',download)
    jobs=jobs_for(prepared)
    jobid=jobs.submit('download-zim',BASE+'long.zim')['id']
    assert started.wait(3)
    jobs.stop()
    assert until(lambda:jobs.list()[0]['status']=='queued')
    assert jobs.list()[0]['read_at']==0
    assert jobs.list()[0]['detail']=='resuming_download'
    monkeypatch.setattr(DownloadJobs,'_download_zim',lambda self,jobid,url,cancel:seen.append(url))
    new=jobs_for(prepared)
    try:
        new.resume()
        assert until(lambda:seen)
        assert seen==[BASE+'long.zim']
        assert until(lambda:new.list()[0]['status']=='completed')
    finally:new.stop()

class FakeHTTPReply:
    def __init__(self,body,code=200,headers=None):
        from io import BytesIO
        self.file=BytesIO(body);self.status=code;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):self.file.close()
    def read(self,size=-1):return self.file.read(size)


@pytest.mark.parametrize('range_status',[206,200])
def test_partial_download_resumes_or_restarts_safely(prepared,monkeypatch,range_status):
    """Range resumes bytes; a server ignoring Range forces a clean overwrite."""
    from pharos import download_jobs as module
    import hashlib
    cfg=load_config(prepared);service=KiwixManagedService(cfg)
    service.prepare_data()
    monkeypatch.setattr(service,'binary_installed',lambda:False)
    jobs=DownloadJobs(cfg,service)
    url=BASE+'long.zim'
    payload=b'wiki:article:'*1000
    partial_dir=service.data_dir/'.downloads';partial_dir.mkdir(exist_ok=True)
    partial=partial_dir/(hashlib.sha256(url.encode()).hexdigest()[:16]+'-long.zim.part')
    partial.write_bytes(payload[:1000])
    seen=[]
    class FakeOpener:
        def open(self,request,timeout):
            target=request.full_url
            if target.endswith('.sha256'):
                return FakeHTTPReply((hashlib.sha256(payload).hexdigest()+'  long.zim').encode())
            assert target==url
            seen.append(request.get_header('Range'))
            if range_status==206:
                return FakeHTTPReply(payload[1000:],206,{
                    'Content-Range':f'bytes 1000-{len(payload)-1}/{len(payload)}',
                    'Content-Length':str(len(payload)-1000)})
            return FakeHTTPReply(payload,200,{'Content-Length':str(len(payload))})
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *args:FakeOpener())
    jobs._download_zim('test',url,threading.Event())
    assert seen==['bytes=1000-']
    assert (service.zim_dir/'long.zim').read_bytes()==payload
    assert not partial.exists()


def test_corrupted_partial_is_never_published(prepared,monkeypatch):
    from pharos import download_jobs as module
    import hashlib
    cfg=load_config(prepared);service=KiwixManagedService(cfg);service.prepare_data()
    monkeypatch.setattr(service,'binary_installed',lambda:False)
    jobs=DownloadJobs(cfg,service)
    url=BASE+'corrupt.zim';real=b'correct archive content'*100
    partdir=service.data_dir/'.downloads';partdir.mkdir(exist_ok=True)
    partial=partdir/(hashlib.sha256(url.encode()).hexdigest()[:16]+'-corrupt.zim.part')
    partial.write_bytes(b'wrong----')
    class FakeOpener:
        def open(self,request,timeout):
            if request.full_url.endswith('.sha256'):
                return FakeHTTPReply(hashlib.sha256(real).hexdigest().encode())
            return FakeHTTPReply(real[9:],206,{
                'Content-Range':f'bytes 9-{len(real)-1}/{len(real)}',
                'Content-Length':str(len(real)-9)})
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *args:FakeOpener())
    with pytest.raises(JobError,match='zim_checksum_mismatch'):
        jobs._download_zim('test',url,threading.Event())
    assert not (service.zim_dir/'corrupt.zim').exists()
    assert not partial.exists()
