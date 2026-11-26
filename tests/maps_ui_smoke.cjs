"use strict";
// Dependency-free DOM smoke test for maps.js. A syntax-only check missed
// archiveId being accessed before initialization in mapCard (dev18).
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const assert=require("node:assert/strict");

class Element {
  constructor(tag="div"){
    this.tagName=tag;
    this.children=[];
    this.value="";
    this.textContent="";
    this.disabled=false;
    this.hidden=false;
    this.className="";
    this.classList={contains:()=>false};
    this.listeners={};
  }
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(type, fn){this.listeners[type]=fn;}
  setAttribute(){}
  scrollIntoView(){}
  focus(){}
}
const elements=new Map();
const document={
  documentElement:{lang:"it"},
  getElementById(id){
    if(!elements.has(id))elements.set(id,new Element());
    return elements.get(id);
  },
  createElement(tag){return new Element(tag);}
};
const region={
  id:"italy",name:"Italia",parent:"europe",
  bbox:[6,35,19,48],zoom:11,downloadable:true
};
const continent={
  id:"europe",name:"Europa",parent:null,
  bbox:[-11,34,33,72],zoom:9,downloadable:true
};
const northEast={
  id:"nord-est",name:"Nord-Est",parent:"italy",
  bbox:[10,43,14,46],zoom:12,downloadable:true
};
let sourceFailure=false;
let sourceDelayed=false,pendingSource=null;
let installed=[];
const api=async(endpoint,options={})=>{
  if(endpoint.startsWith("maps/regions/")&&options.method==="DELETE"){
    const id=decodeURIComponent(endpoint.slice("maps/regions/".length));
    installed=installed.filter(item=>item.id!==id);
    return {deleted:id,metadata_removed:true};
  }
  if(endpoint==="maps/areas")return {items:[continent,region,northEast],status:"available"};
  if(endpoint==="maps/regions")return {items:installed};
  if(endpoint.startsWith("maps/catalog")){
    if(sourceDelayed)return new Promise(resolve=>{pendingSource=resolve;});
    if(sourceFailure)throw Error("publisher temporarily unreachable");
    return {items:[],status:"unavailable"};
  }
  if(endpoint==="jobs")return {items:[]};
  throw Error("unexpected endpoint "+endpoint);
};
const context=vm.createContext({
  document, window:{confirm:()=>true}, console, api, setInterval(){},
  t:key=>key,
});
vm.runInContext(fs.readFileSync(
  path.join(__dirname,"../src/pharos/web/maps.js"),"utf8"),context,
  {filename:"maps.js"});
(async()=>{
  await vm.runInContext("loadMapCatalog()",context);
  const root=document.getElementById("maps-catalog-list");
  assert.equal(root.children.length,1,"continent should be visible even with no map source");
  const card=root.children[0];
  assert.equal(card.tagName,"article");
  assert.equal(card.children.some(n=>n.tagName==="strong"&&n.textContent==="Europa"),true);
  const buttons=card.children.flatMap(n=>n.children || []);
  assert.equal(buttons.some(x=>x.tagName==="button"&&x.disabled),true,"download must stay disabled when source unverified");
  sourceFailure=true;
  await vm.runInContext("loadMapCatalog({refresh:true})",context);
  assert.equal(document.getElementById("maps-catalog-list").children.length,1,
    "catalog must still render after isolated source failure");
  vm.runInContext("mapSelectedParent='europe';renderMapCatalog()",context);
  assert.equal(document.getElementById("maps-catalog-list").children.length,1,
    "drilling down must render a country");
  const label=document.getElementById("maps-catalog-list").children[0].children.find(x=>x.tagName==="strong");
  assert.equal(label.textContent,"Italia");
  installed=[{id:"nord-est-z15.pmtiles",size_bytes:1024}];
  sourceFailure=false;
  sourceDelayed=true;
  const delayed=vm.runInContext("loadMapCatalog({refresh:true})",context);
  // Local inventory has finished, publisher metadata is still pending.
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(document.getElementById("maps-region-list").children.length,1,
    "installed NAS maps must render before publisher catalog is ready");
  assert.equal(typeof pendingSource,"function");
  pendingSource({items:[],status:"unavailable"});
  await delayed;
  sourceDelayed=false;
  const lib=document.getElementById("maps-region-list");
  assert.equal(lib.children[0].className,"maps-library-card");
  assert.equal(lib.children[0].children.find(c=>c.className==="maps-library-hierarchy")?.textContent,
    "Europa / Italia","geographic ancestors belong in the library card");
  assert.equal(lib.children[0].children.some(c=>c.className==="maps-library-area"),false,
    "raw coordinates must not crowd archive cards");
  assert.equal(lib.children[0].children.some(c=>c.className==="maps-library-details"&&
    c.children.some(d=>d.children.some(v=>v.textContent==="mapsLibrarySize"))),false,
    "file size is available to backend but is not displayed in the card");
  assert.equal(lib.children[0].children.find(c=>c.className==="maps-library-filename").textContent,
    "nord-est-z15.pmtiles");
  const controls=lib.children[0].children.find(c=>c.className==="maps-library-actions");
  const mapTitle=lib.children[0].children.find(c=>c.className==="maps-library-title");
  assert.equal(mapTitle.tagName,"strong","non-vector archives should not open the viewer");
  assert.equal(controls.children.length,1,"only Delete remains as an archive action");
  const deleteButton=controls.children.find(c=>c.textContent==="mapsDelete");
  assert(deleteButton&&deleteButton.listeners?.click);
  installed=[{id:"nord-est-z15.pmtiles",size_bytes:1024,
    viewer:{vector:true,bounds:[10,43,14,46],minzoom:0,maxzoom:15}}];
  let opened=null;
  context.window.pharosOpenMap=item=>{opened=item.id;};
  vm.runInContext("renderInstalledMaps("+JSON.stringify([{
    id:"nord-est-z15.pmtiles",size_bytes:1024,
    viewer:{vector:true,bounds:[10,43,14,46],minzoom:0,maxzoom:15}
  }])+")",context);
  const link=lib.children[0].children.find(c=>c.className==="maps-library-title");
  assert.equal(link.tagName,"button");
  assert.equal(typeof link.listeners.click,"function");
  link.listeners.click();
  assert.equal(opened,"nord-est-z15.pmtiles");
  const vectorActions=lib.children[0].children.find(c=>c.className==="maps-library-actions");
  assert.equal(vectorActions.children.length,1,"View button is no longer displayed");
  await vectorActions.children[0].listeners.click();
  assert.equal(document.getElementById("maps-region-list").children.length,1);
  assert.equal(document.getElementById("maps-region-list").children[0].textContent,"mapsEmpty");
  console.log("Map catalog, detailed library and confirmed deletion smoke tests: pass");
})().catch(err=>{console.error(err);process.exitCode=1;});
