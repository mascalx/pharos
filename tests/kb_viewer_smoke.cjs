"use strict";
// Standalone test of the KB viewer; no external network/browser required.
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
class Node{
 constructor(){this.hidden=true;this.src="";this.textContent="";this.href="";this.events={};this.attrs={};}
 addEventListener(k,f){this.events[k]=f;}setAttribute(k,v){this.attrs[k]=v;}
 focus(){}
}
const items=new Map();
const document={getElementById(id){if(!items.has(id))items.set(id,new Node());return items.get(id);},
 body:{classList:{add(){},remove(){}}},addEventListener(){}};
let mapClosed=0,kiwixClosed=0,reads=0;
const window={
 location:{origin:"http://pharos.local",href:"http://pharos.local/#knowledge"},
 pharosCloseMap(){mapClosed++;},pharosCloseKiwix(){kiwixClosed++;}
};
class TestURL{
 constructor(value){
  if(value.startsWith("/")){this.origin="http://pharos.local";this.pathname=value;this.search="";this.hash="";}
  else{const m=value.match(/^(https?:\/\/[^/]+)(\/[^?#]*)(\?[^#]*)?(#.*)?$/);
    if(!m)throw Error("bad URL");this.origin=m[1];this.pathname=m[2];
    this.search=m[3]||"";this.hash=m[4]||"";}
 }
}
const ctx={document,window,URL:TestURL,t:k=>k,
 fetch:async()=>{reads++;return {ok:true,text:async()=>"# Text\nUntrusted <script>"};}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/pharos/web/kb_viewer.js"),"utf8"),ctx);
(async()=>{
 assert.equal(await window.pharosOpenKBDocument("https://evil.example/doc.pdf","doc.pdf"),false);
 assert.equal(await window.pharosOpenKBDocument("/api/private/other","private.pdf"),false);
 const doc="/api/kb/abc/documents/def/file";
 assert.equal(await window.pharosOpenKBDocument(doc,"notes.md"),true);
 assert.equal(items.get("kb-document-viewer-text").textContent,"# Text\nUntrusted <script>");
 assert.equal(reads,1);
 assert.equal(items.get("kb-document-viewer").hidden,false);
 assert.equal(await window.pharosOpenKBDocument(doc,"report.pdf"),true);
 assert.equal(items.get("kb-document-viewer-pdf").src,doc);
 assert.equal(items.get("kb-document-viewer-pdf").hidden,false);
 assert.equal(reads,1,"PDF must use browser, not fetch/read data twice");
 assert.equal(await window.pharosOpenKBDocument(doc,"notes.docx"),true);
 assert.equal(items.get("kb-document-viewer-status").hidden,false);
 assert.equal(mapClosed,3);assert.equal(kiwixClosed,3);
 window.pharosCloseKBDocument();
 assert.equal(items.get("kb-document-viewer-pdf").src,"about:blank");
 assert.equal(items.get("kb-document-viewer").hidden,true);
 const html=fs.readFileSync(path.join(__dirname,"../src/pharos/web/index.html"),"utf8");
 assert(html.includes('id="kb-document-viewer"'));
 const pdfFrame=html.match(/<iframe id="kb-document-viewer-pdf"[^>]*>/)?.[0]||"";
 assert(pdfFrame.includes('title="Documento PDF"'));
 assert(!pdfFrame.includes('sandbox='),
   "Chrome PDF extension must not run inside iframe sandbox");
 // Kiwix remains sandboxed; do not weaken its untrusted ZIM isolation.
 const zimFrame=html.match(/<iframe id="kiwix-viewer-frame"[\s\S]*?<\/iframe>/)?.[0]||"";
 assert(zimFrame.includes('sandbox="allow-scripts allow-forms allow-downloads"'));
 const app=fs.readFileSync(path.join(__dirname,"../src/pharos/web/app.js"),"utf8");
 const kb=fs.readFileSync(path.join(__dirname,"../src/pharos/web/knowledge.js"),"utf8");
 assert(app.includes("window.pharosOpenKBDocument(link.href,source.filename)"));
 assert(kb.includes("window.pharosOpenKBDocument(a.href,doc.filename)"));
 const backend=fs.readFileSync(path.join(__dirname,"../src/pharos/app.py"),"utf8");
 assert(backend.includes('response.headers.get("content-type", "").lower().startswith("application/pdf")'));
 assert(backend.includes("raw.startswith(b'%PDF-')"),
   "The unsandboxed iframe must only receive a signature-verified PDF");
 assert(!backend.includes('"sandbox allow-scripts allow-downloads; object-src'),
   "Chrome blocks CSP-sandboxed PDF responses");
 assert(backend.includes(`"frame-ancestors 'self'; base-uri 'none'"`));
 console.log("KB embedded viewer smoke: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
