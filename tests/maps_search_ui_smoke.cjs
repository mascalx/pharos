"use strict";
// PHAROS offline search UI smoke without browser, network or running backend.
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const assert=require("node:assert/strict");

class Element{
  constructor(tag="div"){this.tagName=tag;this.children=[];this.value="";this.textContent="";
    this.disabled=false;this.listeners={};this.className="";}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  addEventListener(event,handler){this.listeners[event]=handler;}
}
const nodes=new Map();
const document={
  getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
  createElement(tag){return new Element(tag);}
};
let queried="",opened=null;
const api=async path=>{
  queried=path;
  return {indexes:1,items:[{
    name:"Museo <script>unsafe</script>",category:"poi",detail:"Ravenna",
    latitude:44.42,longitude:12.2,area:"nord-est",osm_id:"n123"
  }]};
};
const context=vm.createContext({document,console,api,t:key=>key,
  encodeURIComponent,window:{
    pharosOpenMapForPoint:async(lat,lon)=>{opened=[lat,lon];return true;}
  }});
vm.runInContext(fs.readFileSync(
  path.join(__dirname,"../src/pharos/web/maps_search.js"),"utf8"),context);
(async()=>{
  const input=document.getElementById("maps-search-query");
  input.value="Museo Ravenna";
  await document.getElementById("maps-search-form").listeners.submit({
    preventDefault(){}
  });
  assert.equal(queried,"maps/search?q=Museo%20Ravenna&limit=30");
  const result=document.getElementById("maps-search-results");
  assert.equal(result.children.length,1);
  const title=result.children[0].children[0].children[0];
  assert.equal(title.textContent,"Museo <script>unsafe</script>",
    "Untrusted OpenStreetMap names must be rendered as text, not HTML");
  await result.children[0].children[1].listeners.click();
  assert.deepEqual(opened,[44.42,12.2]);
  console.log("Local geographic search UI smoke test: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
