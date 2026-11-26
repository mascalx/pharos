"use strict";
// PHAROS offline atlas: MapLibre and PMTiles are served ONLY by this PHAROS
// instance. No CDN, hosted style, remote raster tiles, glyphs or sprites.
let pharosMap=null,pharosMapArchive=null,pharosMapProtocol=false,pharosMapLoading=null,pharosMapProtocolInstance=null;
const pharosMapEl=id=>document.getElementById(id);

const PHAROS_MAP_PALETTE={
  background:"#e6ece8",earth:"#f6f1e8",landcover:"#e2e9d9",
  parks:"#d9e8d2",urban:"#eee6dc",water:"#acd1e1",buildings:"#dbd4c8",
  roadCasing:"#d3c2ad",road:"#fffbf4",arterial:"#f7e9cb",
  major:"#f1d5a5",boundaries:"#9e8f82",rail:"#b1a8a0",
  text:"#50473e",halo:"#fbf7ef",
};
let pharosMapLabelsReady=false,pharosSearchMarker=null;
function pharosMapStyle(url,maxzoom,labels=false){
  const src="offline-archive";
  const polygon=["==",["geometry-type"],"Polygon"];
  const lineString=["==",["geometry-type"],"LineString"];
  const byKind=(geometry,kinds)=>kinds?.length?
    ["all",geometry,["in",["get","kind"],["literal",kinds]]]:geometry;
  function fill(id,layer,color,options={}){
    return {id,type:"fill",source:src,"source-layer":layer,
      filter:byKind(polygon,options.kinds),
      paint:{"fill-color":color,"fill-opacity":options.opacity??1}};
  }
  function line(id,layer,color,width,options={}){
    return {id,type:"line",source:src,"source-layer":layer,
      filter:byKind(lineString,options.kinds),
      layout:{"line-join":"round","line-cap":"round"},
      paint:{"line-color":color,"line-width":width,
             ...(options.dash?{"line-dasharray":options.dash}:{})}};
  }
  // MapLibre requires zoom to be the input of a TOP-LEVEL interpolate/step.
  // Do not wrap zoom-based widths inside arithmetic (e.g. ["+", roadWidth, 1]).
  const roadWidth=["interpolate",["linear"],["zoom"],6,0.3,11,1.4,15,5];
  const majorWidth=["interpolate",["linear"],["zoom"],6,0.6,11,2.6,15,9];
  const highWidth=["interpolate",["linear"],["zoom"],6,1.1,11,4,15,12];
  const roadCasingWidth=["interpolate",["linear"],["zoom"],6,1.3,11,2.4,15,6];
  const majorCasingWidth=["interpolate",["linear"],["zoom"],6,2,11,4,15,10.4];
  const highCasingWidth=["interpolate",["linear"],["zoom"],6,2.8,11,5.7,15,13.7];
  const layers=[
    {id:"offline-background",type:"background",
     paint:{"background-color":PHAROS_MAP_PALETTE.background}},
    fill("earth","earth",PHAROS_MAP_PALETTE.earth),
    fill("landcover","landcover",PHAROS_MAP_PALETTE.landcover,{
      opacity:0.8,kinds:["farmland","forest","grassland","scrub"]}),
    fill("landuse-urban","landuse",PHAROS_MAP_PALETTE.urban,{
      opacity:0.75,kinds:["industrial","commercial","residential"]}),
    fill("landuse-green","landuse",PHAROS_MAP_PALETTE.parks,{
      opacity:0.9,kinds:["park","garden","forest","wood","grass","nature_reserve",
        "recreation_ground","cemetery","meadow","national_park","orchard","wetland"]}),
    fill("water","water",PHAROS_MAP_PALETTE.water),
    line("waterways","water",PHAROS_MAP_PALETTE.water,
      ["interpolate",["linear"],["zoom"],6,0.35,11,1.2,15,2.4]),
    line("boundaries","boundaries",PHAROS_MAP_PALETTE.boundaries,0.7),
    fill("buildings","buildings",PHAROS_MAP_PALETTE.buildings,{opacity:0.86}),
    line("roads-casing","roads",PHAROS_MAP_PALETTE.roadCasing,
      roadCasingWidth,{kinds:["minor_road","path"]}),
    line("roads-small","roads",PHAROS_MAP_PALETTE.road,roadWidth,
      {kinds:["minor_road","path"]}),
    line("roads-major-casing","roads",PHAROS_MAP_PALETTE.roadCasing,
      majorCasingWidth,{kinds:["major_road"]}),
    line("roads-major","roads",PHAROS_MAP_PALETTE.arterial,majorWidth,
      {kinds:["major_road"]}),
    line("roads-highway-casing","roads",PHAROS_MAP_PALETTE.roadCasing,
      highCasingWidth,{kinds:["highway"]}),
    line("roads-highway","roads",PHAROS_MAP_PALETTE.major,highWidth,
      {kinds:["highway"]}),
    line("railways","roads",PHAROS_MAP_PALETTE.rail,
      ["interpolate",["linear"],["zoom"],9,0.5,15,1.6],
      {kinds:["rail"],dash:[3,2]})
  ];
  const style={version:8,name:"PHAROS offline atlas",sources:{
    [src]:{type:"vector",url:"pmtiles://"+url,maxzoom}},layers};
  if(labels){
    // Published Noto Sans glyphs are installed and verified once by PHAROS.
    // This remains same-origin over LAN even without Internet access.
    style.glyphs="/api/maps/fonts/{fontstack}/{range}.pbf";
    const field=["coalesce",["get","name:latin"],["get","name"],""];
    layers.push({
      id:"place-names",type:"symbol",source:src,"source-layer":"places",
      minzoom:5,filter:["==",["geometry-type"],"Point"],
      layout:{"text-field":field,"text-font":["Noto Sans Regular"],
        "text-size":["interpolate",["linear"],["zoom"],5,10,10,12,15,14],
        "text-max-width":9,"text-allow-overlap":false,
        "text-anchor":"center"},
      paint:{"text-color":PHAROS_MAP_PALETTE.text,
        "text-halo-color":PHAROS_MAP_PALETTE.halo,"text-halo-width":1.3}
    });
    layers.push({
      id:"road-names",type:"symbol",source:src,"source-layer":"roads",
      minzoom:12,filter:["all",lineString,["!=",["get","kind"],"rail"],
        ["!=",field,""]],
      layout:{"text-field":field,"text-font":["Noto Sans Regular"],
        "symbol-placement":"line","text-size":["interpolate",["linear"],["zoom"],12,10,15,12],
        "text-max-angle":45,"symbol-spacing":190,
        "text-allow-overlap":false},
      paint:{"text-color":"#796a5e","text-halo-color":PHAROS_MAP_PALETTE.halo,
        "text-halo-width":1.3}
    });
  }
  return style;
}
// Composite view of already downloaded PMTiles. Every source remains an
// independent read-only archive, and the layer ordering is preserved across
// regional boundaries (water, terrain, roads, labels).
function pharosMapMosaicStyle(primary,archives,labels){
  const style=pharosMapStyle(window.location.origin+primary.archive_url,
    primary.viewer.maxzoom,labels);
  const candidates=archives.filter(item=>item.id!==primary.id&&item.viewer?.vector&&
    Array.isArray(item.viewer.bounds)&&item.viewer.bounds.length===4&&
    Number.isInteger(item.viewer.maxzoom));
  const selected=candidates.slice(0,23);
  if(!selected.length)return style;
  const baseLayers=style.layers;
  for(let i=0;i<selected.length;i++){
    const item=selected[i],id="offline-region-"+(i+1);
    style.sources[id]={type:"vector",
      url:"pmtiles://"+window.location.origin+item.archive_url,
      maxzoom:item.viewer.maxzoom,bounds:item.viewer.bounds};
  }
  style.layers=[];
  for(const layer of baseLayers){
    style.layers.push(layer);
    if(layer.type==="background")continue;
    for(let i=0;i<selected.length;i++){
      const clone={...layer,id:layer.id+"-region-"+(i+1),
        source:"offline-region-"+(i+1)};
      style.layers.push(clone);
    }
  }
  return style;
}
async function pharosMapInstalled(primary){
  try{
    const response=await api("maps/regions");
    const items=(response.items||[]).filter(x=>x.viewer?.vector&&x.archive_url&&
      Array.isArray(x.viewer.bounds)&&x.viewer.bounds.length===4&&
      Number.isInteger(x.viewer.maxzoom));
    // Starting archive must always be first and retains the existing source ID.
    const dedup=new Set([primary.id]);
    return [primary,...items.filter(x=>{if(dedup.has(x.id))return false;dedup.add(x.id);return true;}).slice(0,23)];
  }catch(error){
    console.warn("Map region inventory unavailable; using selected archive",error);
    return [primary];
  }
}
function pharosMapOverlaps(a,b){
  return Array.isArray(a)&&Array.isArray(b)&&a.length===4&&b.length===4&&
    a[0]<=b[2]&&a[2]>=b[0]&&a[1]<=b[3]&&a[3]>=b[1];
}
async function pharosOpenMapForBounds(bounds){
  let response;
  try{response=await api("maps/regions");}catch(error){return false;}
  const candidates=(response.items||[]).filter(x=>x.viewer?.vector&&
    pharosMapOverlaps(x.viewer.bounds,bounds));
  if(!candidates.length)return false;
  // Prefer the smallest archive that covers the route's midpoint; a
  // national-scale archive may still serve as fallback.
  const mid=[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2];
  candidates.sort((a,b)=>{
    const score=x=>{
      const c=x.viewer.bounds;
      const inCenter=c[0]<=mid[0]&&c[2]>=mid[0]&&c[1]<=mid[1]&&c[3]>=mid[1];
      return (inCenter?0:100000)+Math.abs((c[2]-c[0])*(c[3]-c[1]));
    };
    return score(a)-score(b);
  });
  return Boolean(await pharosViewerOpen(candidates[0],{focusBounds:bounds}));
}
window.pharosOpenMapForBounds=pharosOpenMapForBounds;

// Waypoint data is stored in a separate local SQLite database, never inside PMTiles.
let pharosWaypointMode="view",pharosWaypointItems=[],pharosWaypointMarkers=[];
let pharosWaypointMarkerById=new Map(),pharosWaypointPreview=null;
let pharosWaypointActive=null,pharosWaypointBusy=false;
let pharosWaypointMoving=false;
function pharosWaypointResetForm(saved=false){
  // A draft relocation is visual only. On cancellation (or leaving the editor)
  // restore the original point; successful Save is followed by a DB reload.
  if(pharosWaypointPreview){
    if(!saved)pharosWaypointPreview.marker.setLngLat(pharosWaypointPreview.original);
    pharosWaypointPreview=null;
  }
  pharosWaypointMoving=false;
  pharosMapEl("maps-waypoint-move-hint").hidden=true;
  pharosWaypointActive=null;
  pharosMapEl("maps-waypoint-form").hidden=true;
  pharosMapEl("maps-waypoint-feedback").textContent="";
}
function pharosWaypointModeSet(mode){
  pharosWaypointMode=mode;
  pharosWaypointResetForm();
  const enabled=mode!=="view";
  pharosMapEl("maps-waypoint-actions").hidden=!enabled;
  const toggle=pharosMapEl("maps-waypoint-toggle");
  toggle.hidden=enabled;
  toggle.setAttribute("aria-pressed",String(enabled));
  for(const kind of ["add","edit"]){
    pharosMapEl("maps-waypoint-"+kind).setAttribute("aria-pressed",
      String(mode===(kind==="edit"?"manage":"add")));
  }
  if(pharosMap?.getCanvas)pharosMap.getCanvas().style.cursor=mode==="add"?"crosshair":"";
}
function pharosWaypointOpenForm(item,coordinate){
  const editing=!!item;
  pharosWaypointActive=editing?item.id:null;
  const form=pharosMapEl("maps-waypoint-form");
  form.hidden=false;
  pharosMapEl("maps-waypoint-heading").textContent=t(editing?"waypointsEdit":"waypointsNew");
  pharosMapEl("maps-waypoint-name").value=item?.name||"";
  pharosMapEl("maps-waypoint-description").value=item?.description||"";
  pharosMapEl("maps-waypoint-latitude").value=String(item?.latitude??coordinate?.lat??"");
  pharosMapEl("maps-waypoint-longitude").value=String(item?.longitude??coordinate?.lng??"");
  pharosMapEl("maps-waypoint-delete").hidden=!editing;
  pharosMapEl("maps-waypoint-move").hidden=!editing;
  pharosMapEl("maps-waypoint-feedback").textContent="";
  // Do not open the virtual keyboard or trigger Safari focus zoom on touch screens.
  if(!window.matchMedia?.("(max-width: 680px), (pointer: coarse)").matches){
    pharosMapEl("maps-waypoint-name").focus();
  }
}
function pharosWaypointClearMarkers(){
  for(const marker of pharosWaypointMarkers)marker.remove();
  pharosWaypointMarkers=[];
  pharosWaypointMarkerById.clear();
  pharosWaypointPreview=null;
}
function pharosWaypointPreviewPosition(id,latitude,longitude){
  const marker=pharosWaypointMarkerById.get(id);
  if(!marker)return;
  if(!pharosWaypointPreview||pharosWaypointPreview.id!==id){
    const original=pharosWaypointItems.find(item=>item.id===id);
    if(!original)return;
    pharosWaypointPreview={id,marker,original:[original.longitude,original.latitude]};
  }
  marker.setLngLat([longitude,latitude]);
}
function pharosWaypointRender(){
  pharosWaypointClearMarkers();
  if(!pharosMap||typeof window.maplibregl?.Marker!=="function")return;
  for(const item of pharosWaypointItems){
    const node=document.createElement("button");
    node.type="button";
    node.className="maps-waypoint-marker";
    node.setAttribute("aria-label",item.name);
    node.title=item.name;
    node.addEventListener("click",event=>{
      event.stopPropagation();
      if(pharosWaypointMoving)return;
      if(pharosWaypointMode==="view"){
        new window.maplibregl.Popup({closeButton:true,maxWidth:"280px"})
          .setLngLat([item.longitude,item.latitude])
          .setText(item.name+(item.description?"\n"+item.description:""))
          .addTo(pharosMap);
      }else{
        pharosWaypointModeSet("manage");
        pharosWaypointOpenForm(item);
      }
    });
    const marker=new window.maplibregl.Marker({element:node})
      .setLngLat([item.longitude,item.latitude]).addTo(pharosMap);
    pharosWaypointMarkers.push(marker);
    pharosWaypointMarkerById.set(item.id,marker);
  }
}
async function pharosWaypointLoad(map){
  const response=await api("maps/waypoints");
  if(pharosMap!==map)return;
  pharosWaypointItems=(response.items||[]).filter(item=>
    Number.isFinite(item.latitude)&&Number.isFinite(item.longitude));
  pharosWaypointRender();
}
function pharosWaypointRefreshLanguage(){
  if(!pharosMapEl("maps-waypoint-form").hidden)
    pharosMapEl("maps-waypoint-heading").textContent=t(pharosWaypointActive?"waypointsEdit":"waypointsNew");
}
window.pharosWaypointRefreshLanguage=pharosWaypointRefreshLanguage;
pharosMapEl("maps-waypoint-toggle").addEventListener("click",()=>pharosWaypointModeSet("add"));
pharosMapEl("maps-waypoint-add").addEventListener("click",()=>pharosWaypointModeSet("add"));
pharosMapEl("maps-waypoint-edit").addEventListener("click",()=>pharosWaypointModeSet("manage"));
pharosMapEl("maps-waypoint-finish").addEventListener("click",()=>pharosWaypointModeSet("view"));
pharosMapEl("maps-waypoint-cancel").addEventListener("click",()=>pharosWaypointResetForm());
// Position picking changes only the form draft. The existing coordinate is
// retained in SQLite until the user explicitly presses Save.
pharosMapEl("maps-waypoint-move").addEventListener("click",()=>{
  if(!pharosWaypointActive||pharosWaypointBusy)return;
  pharosWaypointMoving=true;
  pharosMapEl("maps-waypoint-form").hidden=true;
  pharosMapEl("maps-waypoint-move-hint").hidden=false;
  if(pharosMap?.getCanvas)pharosMap.getCanvas().style.cursor="crosshair";
});
pharosMapEl("maps-waypoint-move-cancel").addEventListener("click",()=>{
  if(!pharosWaypointMoving)return;
  pharosWaypointMoving=false;
  pharosMapEl("maps-waypoint-move-hint").hidden=true;
  pharosMapEl("maps-waypoint-form").hidden=false;
  if(pharosMap?.getCanvas)pharosMap.getCanvas().style.cursor="";
});
pharosMapEl("maps-waypoint-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(pharosWaypointBusy||pharosWaypointMoving)return;
  const form=pharosMapEl("maps-waypoint-form");
  const latitude=Number(pharosMapEl("maps-waypoint-latitude").value);
  const longitude=Number(pharosMapEl("maps-waypoint-longitude").value);
  const name=pharosMapEl("maps-waypoint-name").value.trim();
  if(!name||!Number.isFinite(latitude)||latitude< -90||latitude>90||
      !Number.isFinite(longitude)||longitude< -180||longitude>180){
    pharosMapEl("maps-waypoint-feedback").textContent=t("waypointsInvalid");
    return;
  }
  const identifier=pharosWaypointActive;
  pharosWaypointBusy=true;
  pharosMapEl("maps-waypoint-save").disabled=true;
  try{
    const payload={name,description:pharosMapEl("maps-waypoint-description").value.trim(),
      latitude,longitude};
    await api("maps/waypoints"+(identifier?"/"+encodeURIComponent(identifier):""),{
      method:identifier?"PUT":"POST",body:JSON.stringify(payload)
    });
    pharosWaypointResetForm(true);
    await pharosWaypointLoad(pharosMap);
  }catch(error){
    if(!form.hidden)pharosMapEl("maps-waypoint-feedback").textContent=t("waypointsSaveFailed");
    console.warn("Waypoint save failed",error.message);
  }finally{
    pharosWaypointBusy=false;
    pharosMapEl("maps-waypoint-save").disabled=false;
  }
});
pharosMapEl("maps-waypoint-delete").addEventListener("click",async()=>{
  const identifier=pharosWaypointActive;
  if(!identifier||pharosWaypointBusy||!window.confirm(t("waypointsDeleteConfirm")))return;
  pharosWaypointBusy=true;
  pharosMapEl("maps-waypoint-delete").disabled=true;
  try{
    await api("maps/waypoints/"+encodeURIComponent(identifier),{method:"DELETE"});
    pharosWaypointResetForm();
    await pharosWaypointLoad(pharosMap);
  }catch(error){
    pharosMapEl("maps-waypoint-feedback").textContent=t("waypointsDeleteFailed");
    console.warn("Waypoint deletion failed",error.message);
  }finally{
    pharosWaypointBusy=false;
    pharosMapEl("maps-waypoint-delete").disabled=false;
  }
});

function pharosViewerShowError(key,detail=""){
  const node=pharosMapEl("maps-viewer-error");
  // Never display arbitrary URLs/query parameters or oversized worker output.
  const safe=String(detail).replace(/https?:\/\/[^\s]+/ig,"[URL]").slice(0,210);
  node.hidden=false;node.textContent=t(key)+(safe?" — "+safe:"");
}
function pharosViewerErrorDetails(error){
  const description=typeof error?.message==="string"?error.message:
    typeof error==="string"?error:"";
  return description.replace(/[\r\n\t]+/g," ").slice(0,210);
}
async function pharosViewerPreflight(item,url){
  // Keep the check explicitly read-only. It examines the root directory
  // but never walks every tile or changes the archive on the NAS.
  const check=await api("maps/regions/"+encodeURIComponent(item.id)+"/check");
  if(!check.valid)throw Error(check.reason||"maps_check_invalid_archive");
  const result=await fetch(item.archive_url,{
    method:"GET",headers:{"Range":"bytes=0-16383"},cache:"no-store"
  });
  if(result.status!==206)throw Error("maps_check_http_range: HTTP "+result.status);
  const range=result.headers.get("Content-Range")||"";
  if(!/^bytes 0-\d+\/\d+$/.test(range))throw Error("maps_check_http_range_header");
  const head=await result.arrayBuffer();
  if(head.byteLength<127)throw Error("maps_check_short_header");
  const archive=new window.pmtiles.PMTiles(url);
  const header=await archive.getHeader();
  if(header?.tileType!==1)throw Error("maps_check_wrong_tile_type");
  // Protocol can reuse the same PMTiles instance and its header cache.
  pharosMapProtocolInstance?.add(archive);
  return header;
}
function pharosViewerScript(url){
  return new Promise((resolve,reject)=>{
    const script=document.createElement("script");
    script.src=url;script.async=true;
    script.onload=()=>resolve();
    script.onerror=()=>reject(Error("Unable to load local map library: "+url));
    document.head.append(script);
  });
}
async function pharosViewerLibraries(){
  if(window.maplibregl&&window.pmtiles&&pharosMapProtocol)return;
  if(!pharosMapLoading){
    pharosMapLoading=(async()=>{
      const info=await api("maps/viewer-assets/status");
      if(!info.installed)throw Error("mapsViewerNotPrepared");
      pharosMapLabelsReady=info.labels_installed===true;
      const css=document.createElement("link");
      css.rel="stylesheet";css.href="/api/maps/viewer-assets/maplibre-gl.css";
      document.head.append(css);
      // MapLibre 6 is exclusively ESM; the former maplibre-gl.js UMD
      // bundle does not exist in its official npm distribution.
      if(!window.maplibregl){
        window.maplibregl=await import("/api/maps/viewer-assets/maplibre-gl.mjs");
        window.maplibregl.setWorkerUrl("/api/maps/viewer-assets/maplibre-gl-worker.mjs");
      }
      if(!window.pmtiles){
        await pharosViewerScript("/api/maps/viewer-assets/pmtiles.js");
      }
      if(!window.maplibregl?.Map||!window.pmtiles?.Protocol){
        throw Error("mapsViewerLibraryError");
      }
    })().catch(err=>{pharosMapLoading=null;throw err;});
  }
  await pharosMapLoading;
  if(!pharosMapProtocol){
    const protocol=new window.pmtiles.Protocol();
    window.maplibregl.addProtocol("pmtiles",protocol.tile);
    pharosMapProtocolInstance=protocol;
    pharosMapProtocol=true;
  }
}
function pharosViewerBounds(item){
  const limits=item.viewer?.bounds;
  if(!Array.isArray(limits)||limits.length!==4||
     !limits.every(Number.isFinite)||limits[0]>=limits[2]||limits[1]>=limits[3])
    return null;
  return [[limits[0],limits[1]],[limits[2],limits[3]]];
}
function pharosViewerFit(){
  if(!pharosMap||!pharosMapArchive)return;
  const bounds=pharosViewerBounds(pharosMapArchive);
  if(bounds){
    pharosMap.fitBounds(bounds,{padding:35,maxZoom:Math.max(1,Math.min(13,pharosMapArchive.viewer.maxzoom)),duration:250});
  }
}
function pharosViewerClose(){
  if(pharosSearchMarker){pharosSearchMarker.remove();pharosSearchMarker=null;}
  window.pharosGpxDetach?.();
  pharosWaypointClearMarkers();
  pharosWaypointItems=[];
  pharosWaypointModeSet("view");
  if(pharosMap){pharosMap.remove();pharosMap=null;}
  pharosMapArchive=null;
  pharosMapEl("maps-viewer").hidden=true;
  document.body.classList.remove("maps-viewer-active");
  if(document.fullscreenElement===pharosMapEl("maps-viewer"))
    void document.exitFullscreen?.();
  pharosMapEl("maps-viewer-error").hidden=true;
  pharosMapEl("maps-viewer-canvas").replaceChildren();
}
async function pharosViewerOpen(item,options={}){
  if(!item?.viewer?.vector){
    return;
  }
  pharosViewerClose();
  pharosMapArchive=item;
  const panel=pharosMapEl("maps-viewer");
  panel.hidden=false;
  document.body.classList.add("maps-viewer-active");
  pharosMapEl("maps-viewer-close").focus();
  try{
    await pharosViewerLibraries();
    if(pharosMapArchive!==item)return;
    if(typeof window.maplibregl.supported==='function'&&!window.maplibregl.supported()){
      pharosViewerShowError("mapsViewerWebGLUnsupported");
      return;
    }
    const url=window.location.origin+item.archive_url;
    try{
      await pharosViewerPreflight(item,url);
    }catch(error){
      console.warn("PHAROS PMTiles read-only preflight failed",error);
      pharosViewerShowError("mapsViewerPreflightFailed",pharosViewerErrorDetails(error));
      return;
    }
    if(pharosMapArchive!==item)return;
    const installed=await pharosMapInstalled(item);
    if(pharosMapArchive!==item)return;
    const mosaic=pharosMapMosaicStyle(item,installed,pharosMapLabelsReady);
    const map=new window.maplibregl.Map({
      container:"maps-viewer-canvas",
      style:mosaic,
      center:[(item.viewer.bounds[0]+item.viewer.bounds[2])/2,
              (item.viewer.bounds[1]+item.viewer.bounds[3])/2],
      zoom:Math.max(0,item.viewer.minzoom),
      minZoom:0,
      maxZoom:Math.min(22,Math.max(...installed.map(x=>x.viewer.maxzoom))+2),
      attributionControl:false,
      hash:false,
      preserveDrawingBuffer:false,
    });
    pharosMap=map;
    pharosWaypointModeSet("view");
    void pharosWaypointLoad(map).catch(error=>console.warn("Waypoint inventory unavailable",error.message));
    void window.pharosGpxRefresh?.().catch(error=>console.warn("GPX inventory unavailable",error.message));
    map.addControl(new window.maplibregl.NavigationControl({showCompass:false}),"bottom-right");
    map.addControl(new window.maplibregl.ScaleControl({maxWidth:110,unit:"metric"}),"bottom-left");
    map.on("load",()=>{if(pharosMap===map){
      pharosViewerFit();
      window.pharosGpxAttach?.(map);
      if(Array.isArray(options.focusPoint)&&options.focusPoint.length===2&&
         options.focusPoint.every(Number.isFinite)){
        map.jumpTo({center:options.focusPoint,zoom:Math.min(15,map.getMaxZoom())});
        pharosSearchMarker=new window.maplibregl.Marker({color:"#ad2446"})
          .setLngLat(options.focusPoint).addTo(map);
      }
      if(Array.isArray(options.focusBounds)&&options.focusBounds.length===4){
        const b=options.focusBounds;
        if(b.every(Number.isFinite)){
          map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{
            padding:65,maxZoom:15,duration:250});
        }
      }
    }});
    map.on("error",evt=>{
      if(pharosMap!==map||!evt.error)return;
      console.warn("PHAROS map rendering error",evt.error);
      const description=pharosViewerErrorDetails(evt.error);
      // Display the actual problem rather than blaming the PMTiles file.
      // An absent vector layer in a tile is not necessarily a fatal fault.
      if(/source layer .* does not exist/i.test(description))return;
      pharosViewerShowError("mapsViewerRenderError",description);
    });
    map.on("click",event=>{
      if(pharosWaypointMoving){
        // The map has already handled drag/pan; only a completed click chooses
        // a coordinate. The user can still cancel the entire edit afterwards.
        pharosMapEl("maps-waypoint-latitude").value=String(event.lngLat.lat);
        pharosMapEl("maps-waypoint-longitude").value=String(event.lngLat.lng);
        pharosWaypointPreviewPosition(pharosWaypointActive,event.lngLat.lat,event.lngLat.lng);
        pharosWaypointMoving=false;
        pharosMapEl("maps-waypoint-move-hint").hidden=true;
        pharosMapEl("maps-waypoint-form").hidden=false;
        if(map.getCanvas)map.getCanvas().style.cursor="";
        return;
      }
      if(pharosWaypointMode==="add"){
        pharosWaypointOpenForm(null,event.lngLat);
        return;
      }
      if(pharosWaypointMode==="manage")return;
      const features=map.queryRenderedFeatures(event.point);
      const found=features.find(feature=>
        typeof feature.properties?.name==="string"&&feature.properties.name.trim());
      if(found){
        new window.maplibregl.Popup({closeButton:true,maxWidth:"270px"})
          .setLngLat(event.lngLat).setText(found.properties.name).addTo(map);
      }
    });
    requestAnimationFrame(()=>map.resize());
    return true;
  }catch(err){
    pharosViewerShowError(err.message==="mapsViewerNotPrepared"?
      "mapsViewerNotPrepared":"mapsViewerLibraryError");
    console.warn("PHAROS offline map viewer startup failed",err);
  }
}
window.pharosOpenMapForPoint=async(latitude,longitude)=>{
  if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||
      Math.abs(latitude)>90||Math.abs(longitude)>180)return false;
  const response=await api('maps/regions');
  const candidates=(response.items||[]).filter(item=>{
    const b=item.viewer?.bounds;
    return item.viewer?.vector&&Array.isArray(b)&&b.length===4&&
      longitude>=b[0]&&longitude<=b[2]&&latitude>=b[1]&&latitude<=b[3];
  });
  candidates.sort((a,b)=>(b.viewer.maxzoom||0)-(a.viewer.maxzoom||0));
  if(!candidates.length)return false;
  return await pharosViewerOpen(candidates[0],{focusPoint:[longitude,latitude]})===true;
};
window.pharosOpenMap=pharosViewerOpen;
window.pharosCloseMap=()=>{if(!pharosMapEl("maps-viewer").hidden)pharosViewerClose();};
pharosMapEl("maps-viewer-close").addEventListener("click",pharosViewerClose);
document.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&!pharosMapEl("maps-viewer").hidden)pharosViewerClose();
});
