"use strict";
// No network and no rendering engine required: regression for the GPX UI state.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
class Node {
  constructor(tag="div"){
    this.tagName=tag;this.hidden=false;this.children=[];this.events={};
    this.value="";this.textContent="";this.disabled=false;this.checked=false;
    this.attrs={};this.className="";
  }
  addEventListener(name,fn){this.events[name]=fn;}
  setAttribute(name,value){this.attrs[name]=value;}
  replaceChildren(...items){this.children=items;}
  append(...items){this.children.push(...items);}
}
const elements=new Map();
const document={
  documentElement:{lang:"it"},
  getElementById(id){if(!elements.has(id))elements.set(id,new Node());return elements.get(id);},
  createElement(tag){return new Node(tag);}
};
const track={id:"00000000-0000-4000-8000-000000000001",name:"Test river",
  original_file:"test.gpx",distance_m:1234,point_count:3,
  gain_m:30,loss_m:12,bounds:[12.1,44.1,12.3,44.3]};
let catalog=[track],deletes=0,geometryLoads=0,mapUpdates=[],renamedTo=null;
const api=async(name,options={})=>{
  if(name==="maps/gpx")return {items:catalog};
  if(name==="maps/gpx/"+track.id){
    if(options.method==="PATCH"){
      renamedTo=JSON.parse(options.body).name;
      catalog=[{...track,name:renamedTo}];
      return catalog[0];
    }
    if(options.method==="DELETE"){
      deletes++;catalog=[];return {deleted:track.id};
    }
    geometryLoads++;
    return {segments:[[[12.1,44.1],[12.2,44.2],[12.3,44.3]]]};
  }
  throw Error("Unexpected GPX API: "+name);
};
const map={
  layers:[],sources:{},
  addSource(id,source){this.sources[id]={...source,setData(data){mapUpdates.push(data);}};},
  addLayer(layer){this.layers.push(layer);},
  getSource(id){return this.sources[id];},
  fitBounds(bounds){this.lastBounds=bounds;}
};
let openedBounds=null;
const global={document,window:{confirm:()=>true,prompt:()=>("Percorso personalizzato"),
  pharosOpenMapForBounds:async bounds=>{openedBounds=bounds;return true;}},console,api,t:x=>x};
vm.createContext(global);
vm.runInContext(fs.readFileSync(path.join(__dirname,
  "../src/pharos/web/maps_gpx.js"),"utf8"),global);
(async()=>{
  await global.window.pharosGpxRefresh();
  assert.equal(elements.get("maps-gpx-library").children.length,1);
  const fromLibrary=elements.get("maps-gpx-library").children[0];
  await fromLibrary.children[0].children[0].events.click();
  assert.equal(fromLibrary.children[0].children[0].tagName,"button",
    "The GPX name itself is the map-opening action, not an extra button");
  assert.deepEqual(Array.from(openedBounds),[12.1,44.1,12.3,44.3],
    "Show on map in the management page must open a covering map");
  await fromLibrary.children.at(-1).children[0].events.click();
  assert.equal(renamedTo,"Percorso personalizzato");
  assert.equal(elements.get("maps-gpx-library").children[0].children[0].children[0].textContent,
    "Percorso personalizzato");
  global.window.pharosGpxAttach(map);
  assert.equal(map.layers[0].id,"pharos-gpx-lines");
  // Opening from the library already selects the track. Attach starts its
  // asynchronous render; wait for that render before simulating user input.
  await new Promise(resolve=>setImmediate(resolve));
  const visibleRow=elements.get("maps-gpx-visible-list").children[0];
  const label=visibleRow.children[0].children[1];
  const checkbox=label.children[0];
  assert.equal(checkbox.checked,true,"Library selection must remain visible");
  assert.equal(geometryLoads,1,"Opening the selected track loads its geometry");
  assert.equal(mapUpdates.at(-1).features.length,1);
  assert.equal(mapUpdates.at(-1).features[0].geometry.type,"LineString");
  checkbox.checked=false;
  await checkbox.events.change();
  assert.equal(mapUpdates.at(-1).features.length,0);
  checkbox.checked=true;
  await checkbox.events.change();
  assert.equal(geometryLoads,1,"Re-enabling a track reuses cached geometry");
  assert.equal(mapUpdates.at(-1).features.length,1);
  const card=elements.get("maps-gpx-library").children[0];
  await card.children.at(-1).children[1].events.click();
  assert.equal(deletes,1);
  assert.equal(elements.get("maps-gpx-library").children[0].textContent,"gpxEmpty");
  global.window.pharosGpxDetach();
  assert.equal(elements.get("maps-gpx-panel").hidden,true);
  console.log("Offline GPX UI smoke: pass");
})().catch(error=>{console.error(error);process.exitCode=1;});
