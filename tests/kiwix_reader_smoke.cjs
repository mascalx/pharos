"use strict";
// Isolated DOM simulation: ZIM content must open inside PHAROS but never
// gain same-origin access to its parent dashboard.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
class Node{
  constructor(){this.hidden=true;this.events={};this.src="";this.value="";this.textContent="";}
  addEventListener(kind,fn){this.events[kind]=fn;}
  focus(){}
}
const elements=new Map();
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Node());return elements.get(id);},
  addEventListener(){},querySelectorAll(){return []},
  body:{classList:{add(){},remove(){}}}
};
let closedMaps=0;
const window={location:{origin:"http://pharos.local",href:"http://pharos.local/#kiwix"},
  pharosCloseMap(){closedMaps++},confirm(){return true}};
const globals={document,window,console,t:key=>key,el:id=>document.getElementById(id),
  api:async()=>({}),lang:"it",URL,URLSearchParams,FormData};
vm.createContext(globals);
const code=fs.readFileSync(path.join(__dirname,"../src/pharos/web/kiwix.js"),"utf8");
vm.runInContext(code,globals);
assert.equal(window.pharosOpenKiwix("http://evil.example/kiwix/content/test"),false);
assert.equal(window.pharosOpenKiwix("/api/config"),false);
assert.equal(window.pharosOpenKiwix("//evil.example/kiwix"),false);
assert.equal(window.pharosOpenKiwix("/kiwix/content/wiki_it"),true);
assert.equal(elements.get("kiwix-viewer").hidden,false);
assert.equal(elements.get("kiwix-viewer-frame").src,"/kiwix/content/wiki_it");
assert.equal(closedMaps,1,"Kiwix must close a previously open map");
window.pharosCloseKiwix();
assert.equal(elements.get("kiwix-viewer").hidden,true);
assert.equal(elements.get("kiwix-viewer-frame").src,"about:blank");
const html=fs.readFileSync(path.join(__dirname,"../src/pharos/web/index.html"),"utf8");
assert(html.includes('id="kiwix-viewer-frame"'));
const iframe=html.match(/<iframe id="kiwix-viewer-frame"[\s\S]*?<\/iframe>/)?.[0]||"";
assert(iframe.includes('sandbox="allow-scripts allow-forms allow-downloads"'));
assert(!iframe.includes("allow-same-origin"),
  "Untrusted ZIM pages must not share PHAROS script origin");
assert(!html.includes('id="kiwix-search-form" class="kiwix-search-form" target="_blank"'));
const py=fs.readFileSync(path.join(__dirname,"../src/pharos/app.py"),"utf8");
assert(py.includes('response.headers["X-Frame-Options"] = "SAMEORIGIN"'));
assert(py.includes('response.headers["X-Frame-Options"] = "DENY"'));
assert(py.includes('"frame-ancestors \'self\''), "Kiwix only may be embedded");
// Chat citation links to verified ZIM articles must open this same reader.
// Ordinary KB documents retain their separate download / preview behavior.
const appJS=fs.readFileSync(path.join(__dirname,
  "../src/pharos/web/app.js"),"utf8");
assert(appJS.includes("window.pharosOpenKiwix?.(source.url)"));
assert(appJS.includes("if(source.url){"));
assert(appJS.includes("link.target='_blank';link.rel='noopener noreferrer';"));
const proxy=fs.readFileSync(path.join(__dirname,
  "../src/pharos/kiwix_proxy.py"),"utf8");
assert(proxy.includes("KIWIX_PHAROS_THEME"));
assert(proxy.includes("html_theme = (request.method == 'GET'"),
  "Only eligible HTML GETs may be rewritten for the palette");
assert(proxy.includes("upstream.aiter_raw()"),
  "Binary assets must continue to stream unchanged");
console.log("Embedded offline Kiwix reader and security smoke: pass");
