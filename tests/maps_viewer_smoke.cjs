"use strict";
// DOM-free smoke test for maps_viewer.js: confirms full local PMTiles URL,
// no remote styles/glyphs and correct teardown in desktop/mobile shell.
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const path=require("node:path");
class Node{
  constructor(){this.hidden=true;this.textContent="";this.children=[];this.classList={
    add(){},remove(){},contains(){return false;}
  };this.events={};}
  addEventListener(k,fn){this.events[k]=fn;}
  setAttribute(k,v){this[k]=v;}
  replaceChildren(...nodes){this.children=nodes;}
  focus(){this.focused=true;}
}
const nodes=new Map(),document={
  getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},
  createElement(){return new Node();},
  head:{append(){}},
  body:{classList:{add(){},remove(){}}},
  addEventListener(){},
  fullscreenElement:null,
};
let mapStyle,activeMap;
const markers=[];
class FakeMarker{
  constructor(){this.position=null;markers.push(this);}
  setLngLat(coords){this.position=coords;return this;}
  addTo(){return this;}
  remove(){}
}
class FakeMap{
  constructor(opts){mapStyle=opts.style;this.handlers={};activeMap=this;}
  addControl(){}
  on(k,fn){this.handlers[k]=fn;if(k==="load")fn();}
  fitBounds(){}
  resize(){}
  remove(){}
}
const fakeLib={supported:()=>true,Map:FakeMap,
  NavigationControl:class{},ScaleControl:class{},Marker:FakeMarker};
const globals={
  document,console,api:async endpoint=>endpoint==="maps/waypoints"?
    {items:[{id:"test-waypoint",name:"Test",description:"",latitude:44,longitude:12}]}:
    endpoint.includes("/check")?{valid:true}:{installed:true},
  fetch:async()=>({status:206,headers:{get:()=>("bytes 0-16383/20000")},
    arrayBuffer:async()=>new ArrayBuffer(16384)}),
  t:k=>k,
  window:{matchMedia:()=>({matches:true}),location:{origin:"http://pharos.lan"},maplibregl:fakeLib,
    pmtiles:{Protocol:class{tile(){}add(){}},
      PMTiles:class{async getHeader(){return {tileType:1};}}}},
  requestAnimationFrame:fn=>fn(),setTimeout:fn=>fn(),
};
globals.window.maplibregl.addProtocol=()=>{};
vm.createContext(globals);
vm.runInContext(fs.readFileSync(path.join(__dirname,
  "../src/pharos/web/maps_viewer.js"),"utf8"),globals);
(async()=>{
  const item={id:"nord-est-z15.pmtiles",
    archive_url:"/api/maps/regions/nord-est-z15.pmtiles/archive",
    viewer:{vector:true,bounds:[10,43,14,46],minzoom:0,maxzoom:15}};
  await globals.window.pharosOpenMap(item);
  await Promise.resolve();
  assert.equal(markers.length,1,"Stored waypoints should render with a marker");
  assert.deepEqual(Array.from(markers[0].position),[12,44]);
  assert.equal(nodes.get("maps-viewer").hidden,false);
  assert.equal(mapStyle.sources["offline-archive"].url,
    "pmtiles://http://pharos.lan/api/maps/regions/nord-est-z15.pmtiles/archive");
  assert.equal(mapStyle.sources["offline-archive"].maxzoom,15);
  // Mosaic combines all locally downloaded vector archives without merging
  // the PMTiles files or altering the starting archive's original layer IDs.
  const mosaic=vm.runInContext(`pharosMapMosaicStyle(
    {id:"nord-est-z15.pmtiles",archive_url:"/api/maps/regions/nord-est-z15.pmtiles/archive",
     viewer:{vector:true,bounds:[10,43,14,46],maxzoom:15}},
    [{id:"nord-est-z15.pmtiles",archive_url:"/api/maps/regions/nord-est-z15.pmtiles/archive",
      viewer:{vector:true,bounds:[10,43,14,46],maxzoom:15}},
     {id:"centro-z14.pmtiles",archive_url:"/api/maps/regions/centro-z14.pmtiles/archive",
      viewer:{vector:true,bounds:[9,41,14,44],maxzoom:14}},
     {id:"raster.pmtiles",archive_url:"/api/maps/regions/raster.pmtiles/archive",
      viewer:{vector:false,bounds:[9,41,14,44],maxzoom:14}}],
    true)`,globals);
  assert.equal(Object.keys(mosaic.sources).length,2,"Non-vector archives must be excluded");
  assert.equal(mosaic.sources["offline-region-1"].bounds[1],41);
  assert.equal(mosaic.sources["offline-region-1"].maxzoom,14);
  assert(mosaic.layers.some(l=>l.id==="roads-small-region-1"&&l.source==="offline-region-1"));
  assert(mosaic.layers.some(l=>l.id==="roads-major-region-1"&&l.source==="offline-region-1"));
  const firstRoad=mosaic.layers.findIndex(l=>l.id==="roads-small");
  assert.equal(mosaic.layers[firstRoad+1].id,"roads-small-region-1",
    "Draw both regions in the same layer order to preserve adjoining roads");
  assert.equal(mosaic.layers.filter(l=>l.type==="background").length,1);
  assert.equal(mosaic.layers.find(l=>l.id==="place-names-region-1")?.source,
    "offline-region-1");

  assert.equal(Object.hasOwn(mapStyle,"glyphs"),false,
    "A glyphs key with undefined value violates MapLibre style validation");
  assert.equal(Object.hasOwn(mapStyle,"sprite"),false,
    "Sprite must also be omitted rather than explicitly undefined");
  assert(mapStyle.layers.some(layer=>layer["source-layer"]==="roads"));
  assert(mapStyle.layers.some(layer=>layer["source-layer"]==="water"));
  // Protomaps 'water' is mixed-geometry: a fill without an explicit Polygon
  // filter previously flooded large inland areas on real map screenshots.
  const waterFill=mapStyle.layers.find(layer=>layer.id==="water");
  const waterways=mapStyle.layers.find(layer=>layer.id==="waterways");
  assert.equal(waterFill.type,"fill");
  assert.deepEqual(JSON.parse(JSON.stringify(waterFill.filter)),
    ["==",["geometry-type"],"Polygon"]);
  assert.equal(waterways.type,"line");
  assert.deepEqual(JSON.parse(JSON.stringify(waterways.filter)),
    ["==",["geometry-type"],"LineString"]);
  function hasGeometry(filter,geometry){
    if(!Array.isArray(filter))return false;
    if(JSON.stringify(filter)===JSON.stringify(["==",["geometry-type"],geometry]))return true;
    return filter.some(part=>Array.isArray(part)&&hasGeometry(part,geometry));
  }
  for(const layer of mapStyle.layers){
    if(layer.type!=="fill"&&layer.type!=="line")continue;
    assert(hasGeometry(layer.filter,layer.type==="fill"?"Polygon":"LineString"),
      "All fill/line layers must restrict geometry, even with kind filters");
  }
  const detailed=vm.runInContext('pharosMapStyle("http://pharos.lan/local.pmtiles",15,true)',globals);
  assert.equal(detailed.glyphs,"/api/maps/fonts/{fontstack}/{range}.pbf");
  assert(detailed.layers.some(layer=>layer.id==="place-names"));
  const roadNames=detailed.layers.find(layer=>layer.id==="road-names");
  assert(roadNames);
  assert.equal(roadNames.layout["symbol-spacing"],190);
  assert.equal(roadNames.layout["text-max-angle"],45);
  assert(!JSON.stringify(roadNames.filter).includes("minor_road"));
  assert(JSON.stringify(roadNames.filter).includes("rail"));
  assert.equal(roadNames.layout["text-allow-overlap"],false);
  assert(!detailed.layers.some(layer=>layer.type==="symbol"&&
    !layer.layout?.["text-font"]));
  assert(detailed.layers.some(layer=>layer.id==="roads-highway"));
  assert(detailed.layers.some(layer=>layer.id==="railways"));
  // MapLibre rejects zoom expressions nested inside arithmetic. All paint
  // values containing zoom must have a top-level interpolate/step expression.
  function containsZoom(value){
    return Array.isArray(value)&&(
      (value.length===1&&value[0]==="zoom")||
      value.some(part=>containsZoom(part)));
  }
  for(const layer of detailed.layers){
    for(const [property,value] of Object.entries(layer.paint||{})){
      if(!containsZoom(value))continue;
      assert(["interpolate","step"].includes(value[0]),
        layer.id+"."+property+": zoom expression must be top-level");
      assert.equal(value[2]?.[0],"zoom",
        layer.id+"."+property+": zoom must be the top-level expression input");
    }
    for(const [property,value] of Object.entries(layer.layout||{})){
      if(!containsZoom(value))continue;
      assert(["interpolate","step"].includes(value[0]),
        layer.id+"."+property+": zoom expression must be top-level");
      assert.equal(value[2]?.[0],"zoom");
    }
  }
  const body=fs.readFileSync(path.join(__dirname,
    "../src/pharos/web/maps_viewer.js"),"utf8");
  assert(!/https:\/\/(?:unpkg|cdn\.jsdelivr|api\.maptiler)/.test(body));
  assert(body.includes('import("/api/maps/viewer-assets/maplibre-gl.mjs")'),
    "MapLibre 6 requires its browser ESM distribution");
  assert(body.includes('setWorkerUrl("/api/maps/viewer-assets/maplibre-gl-worker.mjs")'),
    "MapLibre 6 worker must be available locally");
  assert(!body.includes('pharosViewerScript("/api/maps/viewer-assets/maplibre-gl.js")'),
    "The obsolete MapLibre UMD bundle must not be requested");
  assert.equal(nodes.get("maps-viewer-full")?.events?.click,undefined,
    "Fullscreen button handler must not be registered");
  assert.equal(nodes.get("maps-viewer-fit")?.events?.click,undefined,
    "Removed fit button must have no listener; automatic fit still applies");
  assert.equal(nodes.get("maps-viewer-archive"),undefined,
    "Viewer no longer relies on archive heading");
  assert(!body.includes('maps-viewer-fit").addEventListener'),
    "No DOM reference to removed fit button");
  const html=fs.readFileSync(path.join(__dirname,"../src/pharos/web/index.html"),"utf8");
  const css=fs.readFileSync(path.join(__dirname,"../src/pharos/web/layout-dev2.css"),"utf8");
  assert(html.includes('id="maps-waypoint-toggle" class="maps-waypoint-icon"'),
    "Waypoint mode must use a square, icon-only button");
  for(const id of ["maps-waypoint-add","maps-waypoint-edit","maps-waypoint-finish"]){
    assert(html.includes('id="'+id+'" class="maps-waypoint-icon"'));
  }
  assert(html.includes('id="maps-waypoint-move"'),
    "Existing waypoint editor must allow map-based repositioning");
  assert(html.includes('aria-modal="false"'),
    "PHAROS topbar remains usable while the atlas is open");
  assert(css.includes(".maps-viewer{top:32px;z-index:90}"),
    "Keep the approved 32px PHAROS bar visible over the atlas");
  assert(css.includes(".maps-waypoint-tools{")&&css.includes("bottom:auto"),
    "Waypoint tools must be at the top-left rather than the bottom");

  // Deliberate edit mode: touching a map must not silently add waypoints.
  nodes.get("maps-waypoint-toggle").events.click();
  assert.equal(nodes.get("maps-waypoint-actions").hidden,false);
  activeMap.handlers.click({lngLat:{lat:44.4,lng:12.2}});
  assert.equal(nodes.get("maps-waypoint-form").hidden,false);
  assert.equal(nodes.get("maps-waypoint-name").focused,undefined,
    "Touch editor must not autofocus the name field");
  assert.equal(nodes.get("maps-waypoint-latitude").value,"44.4");
  assert.equal(nodes.get("maps-waypoint-longitude").value,"12.2");
  nodes.get("maps-waypoint-cancel").events.click();
  assert.equal(nodes.get("maps-waypoint-form").hidden,true);
  nodes.get("maps-waypoint-edit").events.click();
  activeMap.handlers.click({lngLat:{lat:44.4,lng:12.2}});
  assert.equal(nodes.get("maps-waypoint-form").hidden,true,
    "manage mode must not create points by clicking empty map areas");
  // Editing an existing waypoint permits a map click to change the draft
  // coordinate; no backend write occurs until Save is explicitly pressed.
  vm.runInContext('pharosWaypointModeSet("manage");pharosWaypointOpenForm({id:"test-waypoint",name:"Test",description:"",latitude:44,longitude:12});',globals);
  assert.equal(nodes.get("maps-waypoint-move").hidden,false);
  nodes.get("maps-waypoint-move").events.click();
  assert.equal(nodes.get("maps-waypoint-form").hidden,true);
  assert.equal(nodes.get("maps-waypoint-move-hint").hidden,false);
  activeMap.handlers.click({lngLat:{lat:45.1,lng:13.3}});
  assert.deepEqual(Array.from(markers[0].position),[13.3,45.1],
    "Point marker must preview the new location before Save");
  assert.equal(nodes.get("maps-waypoint-form").hidden,false);
  assert.equal(nodes.get("maps-waypoint-move-hint").hidden,true);
  assert.equal(nodes.get("maps-waypoint-latitude").value,"45.1");
  assert.equal(nodes.get("maps-waypoint-longitude").value,"13.3");
  nodes.get("maps-waypoint-move").events.click();
  nodes.get("maps-waypoint-move-cancel").events.click();
  assert.equal(nodes.get("maps-waypoint-form").hidden,false);
  assert.equal(nodes.get("maps-waypoint-latitude").value,"45.1");
  nodes.get("maps-waypoint-cancel").events.click();
  assert.deepEqual(Array.from(markers[0].position),[12,44],
    "Cancel must restore the original marker, not just the form coordinates");
  assert.equal(nodes.get("maps-waypoint-form").hidden,true);
  nodes.get("maps-waypoint-finish").events.click();
  assert.equal(nodes.get("maps-waypoint-actions").hidden,true);
  nodes.get("maps-viewer-close").events.click();
  assert.equal(nodes.get("maps-viewer").hidden,true);
  console.log("Offline map renderer smoke: pass");
})().catch(error=>{console.error(error);process.exitCode=1;});
