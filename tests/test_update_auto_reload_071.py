"""Browser-only regression: reload once after a watched update completes."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_update_browser_reloads_once_only_after_active_to_completed():
    if not shutil.which('node'):
        pytest.skip('Node.js not available')
    js=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function el(id){
  if(!nodes.has(id))nodes.set(id,{
    hidden:false,disabled:false,href:'',
    classList:{toggle(){},add(){},remove(){}},addEventListener(){},
  });
  return nodes.get(id);
}
let reloads=0,checks=0;
let status='queued';
const ctx={
  IT:{},EN:{},el,t:x=>x,lang:'it',console,Date,Math,Set,JSON,
  window:{location:{reload:()=>{reloads++;}},confirm:()=>true},
  setInterval:()=>{},
  api:async path=>{
    if(path==='updates/installation')return {status,phase:status};
    if(path==='updates')return {state:'available',available:true,remote_sha:'a'.repeat(40)};
    throw Error('unexpected path '+path);
  }
};
vm.runInNewContext(fs.readFileSync('src/pharos/web/updates.js','utf8'),ctx);
(async()=>{
  await new Promise(resolve=>setTimeout(resolve,0)); // startup GETs settle
  assert.equal(reloads,0);
  status='completed';
  await ctx.window.pharosLoadInstallation();
  assert.equal(reloads,1);
  await ctx.window.pharosLoadInstallation();
  assert.equal(reloads,1, 'Never reload repeatedly on persisted completed status');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True,timeout=12)
    assert result.returncode==0,result.stderr
