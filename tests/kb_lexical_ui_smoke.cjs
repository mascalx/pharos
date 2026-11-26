'use strict';
// UI-level static smoke: no server/network, no clicks and no data mutations.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/pharos/web');
const kb=fs.readFileSync(path.join(root,'knowledge.js'),'utf8');
const jobs=fs.readFileSync(path.join(root,'jobs.js'),'utf8');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
new vm.Script(kb,{filename:'knowledge.js'});
new vm.Script(jobs,{filename:'jobs.js'});
assert(kb.includes("api('kb/'+encodeURIComponent(kb.id)+'/lexical')"));
assert(kb.includes("/lexical/build',{method:'POST'}"));
assert(kb.includes("window.confirm(t('kbLexicalConfirm')"));
assert(kb.includes("lexicalBuild.disabled=kb.indexed_documents===0"));
assert(jobs.includes("'build-lexical':'jobsLexical'"));
assert(jobs.includes("job.kind==='build-lexical'"));
assert.match(html, /src="\/static\/knowledge\.js\?v=[\w.-]+"\s+defer/);
assert.match(html, /src="\/static\/jobs\.js\?v=[\w.-]+"\s+defer/);
for(const lang of ['it','en']){
  const t=JSON.parse(fs.readFileSync(path.join(root,'locales',lang+'.json'),'utf8')).messages;
  for(const key of ['kbLexicalReady','kbLexicalMissing','kbLexicalStale',
                    'kbLexicalBuild','kbLexicalRebuild','kbLexicalConfirm',
                    'jobsLexical','jobsLexicalScanning']){
    assert.equal(typeof t[key],'string',lang+': '+key);
    assert(t[key].length>0);
  }
}
console.log('FTS5 KB controls, translations and job integration: PASS');
