"""Dev8: completion-only notification center and module-local job progress.

These checks protect the durable job record and pre-existing Home tile preferences.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import tomlkit
from fastapi.testclient import TestClient

from pharos.app import create_app
from pharos.config import load_config
from pharos.download_jobs import DownloadJobs
from pharos.home import FEATURES, LEGACY_FEATURES, home_preferences, save_home_preferences
from pharos.kiwix_runtime import KiwixManagedService

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_home_preferences_migrate_on_read_without_touching_toml(prepared):
    """A user's prior order and hidden tile choices survive a new Home tile."""
    raw = tomlkit.parse(prepared.read_text(encoding='utf-8'))
    old_order = list(reversed(LEGACY_FEATURES))
    raw['home'] = {'order': old_order, 'hidden': ['kiwix', 'diagnostics']}
    prepared.write_text(tomlkit.dumps(raw), encoding='utf-8')
    before = prepared.read_bytes()
    cfg = load_config(prepared)
    expected = {'order': old_order + ['notifications', 'translations', 'maps'], 'hidden': ['kiwix', 'diagnostics']}
    assert home_preferences(cfg.source) == expected
    assert prepared.read_bytes() == before  # GET is read-only, no surprise config rewrite
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/home').json() == expected
        assert client.put('/api/home', json=expected, headers={'X-Pharos-Request': '1'}).json() == expected
    assert home_preferences(cfg.source) == expected
    assert list(FEATURES)[-3:] == ['notifications', 'translations', 'maps']


def test_notification_acknowledgement_preserves_job_history(prepared):
    cfg = load_config(prepared)
    jobs = DownloadJobs(cfg, KiwixManagedService(cfg))
    active_id = jobs.create('ingest-document', 'ongoing.pdf', {'kb_id': '123', 'filename': 'ongoing.pdf'})
    completed_id = jobs.create('ingest-document', 'done.pdf', {'kb_id': '123', 'filename': 'done.pdf'})
    failed_id = jobs.create('reindex-rag', 'index', {'kb_id': '123'})
    jobs._change(active_id, status='running', progress=30)
    jobs._change(completed_id, status='completed', progress=100)
    jobs._change(failed_id, status='failed', detail='failure')
    assert jobs.mark_read(completed_id)['status'] == 'completed'
    rows = {r['id']: r for r in jobs.list()}
    assert rows[completed_id]['read_at'] > 0
    assert rows[completed_id]['status'] == 'completed'
    assert rows[active_id]['status'] == 'running' and rows[active_id]['read_at'] == 0
    assert rows[failed_id]['status'] == 'failed' and rows[failed_id]['read_at'] == 0
    restarted = DownloadJobs(cfg, KiwixManagedService(cfg))
    assert next(r for r in restarted.list() if r['id'] == completed_id)['read_at'] > 0


def test_notification_browser_flow_only_lights_for_unread_completion():
    """Exercise real jobs.js through a minimal DOM; don't assert only string presence."""
    if not shutil.which('node'):
        pytest.skip('Node.js unavailable')
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.handlers={};this.hidden=false;this.disabled=false;
    this.flags=new Set();this.classList={add:x=>this.flags.add(x),remove:x=>this.flags.delete(x),
      contains:x=>this.flags.has(x),toggle:(x,flag)=>{if(flag)this.flags.add(x);else this.flags.delete(x)}};}
  append(...nodes){for(const node of nodes){if(node?.tagName==='fragment'){this.append(...node.children);}else{this.children.push(node);node.parent=this;}}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes)}
  remove(){const p=this.parent;if(p)p.children.splice(p.children.indexOf(this),1)}
  addEventListener(event,fn){this.handlers[event]=fn}
  setAttribute(){}
  click(){return this.handlers.click?.()}
}
const nodes=new Map();const el=id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)};
const jobs=[
  {id:'running',kind:'ingest-document',status:'running',label:'Processing.pdf',progress:30,read_at:0,target_view:'knowledge'},
  {id:'zrunning',kind:'download-zim',status:'queued',label:'Wiki.zim',progress:0,read_at:0,target_view:'kiwix'},
  {id:'done',kind:'ingest-document',status:'completed',label:'Done.pdf',progress:100,read_at:0,target_view:'knowledge'},
  {id:'failed',kind:'download-zim',status:'failed',label:'Broken.zim',progress:30,read_at:0,target_view:'kiwix'}
];
const routes=[];const ctx={IT:{},EN:{},window:{},el,t:x=>x,console,Set,JSON,Math,
  document:{createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment')},
  setInterval:()=>{},showView:v=>routes.push(v),api:async(path,opts)=>{
     if(path==='jobs')return {items:jobs};
     const id=path.split('/')[1];assert(path.endsWith('/read'));assert.equal(opts.method,'POST');
     jobs.find(x=>x.id===id).read_at=Date.now();return {id,status:'completed'};
  }
};
vm.runInNewContext(fs.readFileSync('src/pharos/web/jobs.js','utf8'),ctx);
const pause=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
  await pause();
  const indicator=el('notification-indicator');const panel=el('job-list');
  assert.equal(indicator.flags.has('has-unread'),true);
  assert.equal(indicator.flags.has('has-error'),true);
  assert.equal(indicator.flags.has('has-activity'),false);
  assert.equal(panel.children.length,4); // active KB and ZIM + unread terminal jobs
  assert.equal(el('kb-active-jobs').hidden,false);
  assert.equal(el('kiwix-active-jobs').hidden,false);
  const activeKB=panel.children[0];await activeKB.click();await pause();
  assert.equal(routes[0],'knowledge');
  assert.equal(jobs.find(x=>x.id==='running').read_at,0);
  const activeCard=panel.children[1];await activeCard.click();await pause();
  assert.equal(routes[1],'kiwix');
  assert.equal(jobs.find(x=>x.id==='zrunning').read_at,0);
  assert.equal(panel.children.length,4); // active jobs cannot be archived by click
  const first=panel.children[2];await first.click();await pause();
  assert.equal(jobs.find(x=>x.id==='done').read_at>0,true);
  assert.equal(routes[2],'knowledge');
  assert.equal(panel.children.length,3); // active KB and ZIM + unread error
  assert.equal(indicator.flags.has('has-unread'),true);
  assert.equal(indicator.flags.has('has-error'),true);
  await panel.children[2].click();await pause();
  assert.equal(routes[3],'kiwix');
  assert.equal(panel.children.length,2); // active KB and ZIM remain, no terminal alerts
  assert.equal(panel.children[0].tagName,'button');
  assert.equal(indicator.flags.has('has-unread'),false);
  assert.equal(indicator.flags.has('has-error'),false);
  assert.equal(indicator.hidden,true);
  assert.equal(indicator.flags.has('has-activity'),false);
  assert.equal(el('kb-active-jobs').hidden,false); // processing continues in KB
  assert.equal(el('kiwix-active-jobs').hidden,false);
  // A completed download replaces the durable running card with one
  // acknowledgement-only terminal notification.
  jobs.find(x=>x.id==='zrunning').status='completed';
  await ctx.window.pharosLoadJobs();await pause();
  assert.equal(panel.children.length,2);
  assert.equal(panel.children[1].tagName,'button');
  assert.equal(indicator.flags.has('has-unread'),true);
  await panel.children[1].click();await pause();
  assert.equal(jobs.find(x=>x.id==='zrunning').read_at>0,true);
  assert.equal(panel.children.length,1);
  assert.equal(panel.children[0].tagName,'button');
  assert.equal(indicator.hidden,true);
  console.log('Notification flow: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, cwd=ROOT, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Notification flow: PASS' in result.stdout


def test_notification_css_and_home_tile_present():
    html = (ROOT/'src/pharos/web/index.html').read_text(encoding='utf-8')
    css = (ROOT/'src/pharos/web/layout-dev2.css').read_text(encoding='utf-8')
    js = (ROOT/'src/pharos/web/jobs.js').read_text(encoding='utf-8')
    assert 'data-view="notifications"' in html
    assert 'id="kb-active-jobs"' in html and 'id="kiwix-active-jobs"' in html
    assert 'right:1%;top:5px' in css and 'width:98%;height:23px' in css
    assert '.notification-indicator.has-activity .signal-line' not in css
    assert 'if(jobsRefreshing){jobsRefreshPending=true;return;}' in js
