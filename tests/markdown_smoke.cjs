"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
class Node{
  constructor(tag,value=""){this.tag=tag;this.value=value;this.children=[];this.classList={remove(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;this.value="";}
  set textContent(v){this.value=String(v);this.children=[];}
  get textContent(){return this.value+this.children.map(c=>c.textContent).join("");}
}
const document={createElement:tag=>new Node(tag),createTextNode:v=>new Node("#text",v)};
const ctx={window:{},document,URL};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/pharos/web/markdown.js"),"utf8"),ctx);
const render=s=>{const n=new Node("div");ctx.window.pharosRenderMarkdown(n,s);return n;};
const all=n=>[n,...n.children.flatMap(all)];
const sample=render("### Heading\n\n**bold** and *italic*\n\n- one\n- two\n\n3. three\n4. four\n\n| A | B |\n| --- | --- |\n| x | y |\n\n"+ "~~~js\n<script>alert(1)</script>\n~~~\n\n[docs](https://example.org/docs)");
for(const tag of ["h5","strong","em","ul","ol","table","th","td","pre","code","a"])assert(all(sample).some(n=>n.tag===tag),tag);
assert.equal(all(sample).find(n=>n.tag==="ol").start,3);
assert.equal(all(sample).find(n=>n.tag==="a").rel,"noopener noreferrer");
const unsafe=render('<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n[bad](javascript:alert(1))\n[bad](data:text/html,evil)\n[bad](//example.org)\n[bad](https://user:pass@example.org)\n![remote](https://example.org/image.png)');
assert(!all(unsafe).some(n=>["img","script","a","iframe"].includes(n.tag)));
assert(unsafe.textContent.includes("<script>alert(1)</script>"));
assert(unsafe.textContent.includes("![remote]"));
assert.equal(render("x".repeat(100001)).textContent.length,100001);
console.log("Safe Markdown smoke: PASS");
