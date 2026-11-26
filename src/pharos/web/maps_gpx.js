/* GPX library and MapLibre overlays. All data stays on the local PHAROS Core. */
"use strict";
let pharosGpxItems=[],pharosGpxMap=null,pharosGpxVisible=new Set();
let pharosGpxGeometry=new Map(),pharosGpxLoadSerial=0,pharosGpxUploading=false;
const gpxEl=id=>document.getElementById(id);
const gpxMsg=(key)=>typeof t==="function"?t(key):key;
function gpxFeedback(key,viewer=false){
  gpxEl(viewer?"maps-gpx-viewer-feedback":"maps-gpx-feedback").textContent=key?gpxMsg(key):"";
}
function gpxStats(item){
  const distance=(item.distance_m/1000).toLocaleString(document.documentElement.lang||"it",{
    maximumFractionDigits:1,minimumFractionDigits:1})+" km";
  const elevation=item.gain_m===null?"":(" · +"+Math.round(item.gain_m)+" m");
  return distance+elevation+" · "+item.point_count+" "+gpxMsg("gpxPoints");
}
function gpxButton(label,action,style="outline-btn"){
  const button=document.createElement("button");button.type="button";
  button.className=style;button.textContent=gpxMsg(label);
  button.addEventListener("click",action);return button;
}
async function gpxShowTrack(item,forViewer){
  if(!pharosGpxMap){
    pharosGpxVisible.add(item.id);
    try{
      const opened=await window.pharosOpenMapForBounds?.(item.bounds);
      if(!opened){
        pharosGpxVisible.delete(item.id);
        gpxFeedback("gpxNoCoverage",forViewer);
      }else{
        gpxFeedback("",forViewer);
        gpxRenderLists();
      }
    }catch(error){
      pharosGpxVisible.delete(item.id);
      gpxFeedback("gpxDisplayFailed",forViewer);
      console.warn("GPX map opening failed",error);
    }
    return;
  }
  pharosGpxVisible.add(item.id);
  try{
    await gpxRenderMap();
    const b=item.bounds;
    if(b?.length===4&&b.every(Number.isFinite)&&b[2]-b[0]<180){
      const lon=Math.max(0.005,(b[2]-b[0])*0.08);
      const lat=Math.max(0.005,(b[3]-b[1])*0.08);
      pharosGpxMap.fitBounds([[b[0]-lon,b[1]-lat],[b[2]+lon,b[3]+lat]],{
        padding:55,maxZoom:15,duration:250
      });
    }
    gpxFeedback("",forViewer);
    gpxRenderLists();
  }catch(error){gpxFeedback("gpxDisplayFailed",forViewer);}
}
async function gpxRenameTrack(item,forViewer){
  const value=window.prompt(gpxMsg("gpxRenamePrompt"),item.name);
  if(value===null)return;
  const name=value.trim();
  if(!name||name.length>120){gpxFeedback("gpxNameInvalid",forViewer);return;}
  try{
    await api("maps/gpx/"+encodeURIComponent(item.id),{
      method:"PATCH",body:JSON.stringify({name})});
    await pharosGpxRefresh();
    gpxFeedback("gpxRenamed",forViewer);
  }catch(error){gpxFeedback("gpxRenameFailed",forViewer);}
}
function gpxItemRow(item,forViewer){
  const row=document.createElement("div");row.className="maps-gpx-row";
  const top=document.createElement("div");top.className="maps-gpx-row-head";
  const title=document.createElement("button");title.type="button";
  title.className="maps-gpx-title";title.textContent=item.name;
  title.setAttribute("aria-label",gpxMsg("gpxCenter")+" — "+item.name);
  title.addEventListener("click",()=>gpxShowTrack(item,forViewer));
  top.append(title);
  if(forViewer){
    const label=document.createElement("label");label.className="maps-gpx-visibility";
    const input=document.createElement("input");input.type="checkbox";
    input.checked=pharosGpxVisible.has(item.id);
    input.setAttribute("aria-label",gpxMsg("gpxShowTrack")+": "+item.name);
    input.addEventListener("change",async()=>{
      if(input.checked)pharosGpxVisible.add(item.id);
      else pharosGpxVisible.delete(item.id);
      try{await gpxRenderMap();gpxFeedback("",true);}
      catch(error){gpxFeedback("gpxDisplayFailed",true);console.warn("GPX map render failed",error);}
    });
    label.append(input);
    const labelText=document.createElement("span");labelText.textContent=gpxMsg("gpxVisible");
    label.append(labelText);top.append(label);
  }
  const stats=document.createElement("small");stats.textContent=gpxStats(item);
  const controls=document.createElement("div");controls.className="maps-gpx-actions";
  controls.append(gpxButton("gpxRename",()=>gpxRenameTrack(item,forViewer)));
  controls.append(gpxButton("gpxDelete",async()=>{
    if(!window.confirm(gpxMsg("gpxDeleteConfirm").replace("{name}",item.name)))return;
    try{
      await api("maps/gpx/"+encodeURIComponent(item.id),{method:"DELETE"});
      pharosGpxVisible.delete(item.id);pharosGpxGeometry.delete(item.id);
      await pharosGpxRefresh();gpxFeedback("gpxDeleted");
    }catch(error){gpxFeedback("gpxDeleteFailed",forViewer);}
  }));
  row.append(top,stats,controls);return row;
}
function gpxRenderLists(){
  const manager=gpxEl("maps-gpx-library"),viewer=gpxEl("maps-gpx-visible-list");
  manager.replaceChildren();viewer.replaceChildren();
  if(!pharosGpxItems.length){
    const empty=document.createElement("p");empty.className="help-text";
    empty.textContent=gpxMsg("gpxEmpty");manager.append(empty);
    const another=document.createElement("p");another.className="help-text";
    another.textContent=gpxMsg("gpxEmpty");viewer.append(another);
    return;
  }
  for(const item of pharosGpxItems){
    manager.append(gpxItemRow(item,false));
    viewer.append(gpxItemRow(item,true));
  }
}
async function pharosGpxRefresh(){
  const result=await api("maps/gpx");
  pharosGpxItems=result.items||[];
  const known=new Set(pharosGpxItems.map(item=>item.id));
  for(const id of pharosGpxVisible)if(!known.has(id))pharosGpxVisible.delete(id);
  for(const id of pharosGpxGeometry.keys())if(!known.has(id))pharosGpxGeometry.delete(id);
  gpxRenderLists();
  await gpxRenderMap();
}
window.pharosGpxRefresh=pharosGpxRefresh;
async function gpxRenderMap(){
  const map=pharosGpxMap;
  if(!map||!map.getSource?.("pharos-gpx"))return;
  const serial=++pharosGpxLoadSerial;
  const features=[];
  for(const item of pharosGpxItems){
    if(!pharosGpxVisible.has(item.id))continue;
    let segments=pharosGpxGeometry.get(item.id);
    if(!segments){
      const result=await api("maps/gpx/"+encodeURIComponent(item.id));
      if(map!==pharosGpxMap||serial!==pharosGpxLoadSerial)return;
      segments=result.segments||[];
      pharosGpxGeometry.set(item.id,segments);
    }
    for(const segment of segments){
      if(segment.length<2)continue;
      features.push({type:"Feature",properties:{track_id:item.id,name:item.name},
        geometry:{type:"LineString",coordinates:segment}});
    }
  }
  if(map!==pharosGpxMap||serial!==pharosGpxLoadSerial)return;
  map.getSource("pharos-gpx").setData({type:"FeatureCollection",features});
}
function pharosGpxAttach(map){
  pharosGpxMap=map;
  map.addSource("pharos-gpx",{type:"geojson",
    data:{type:"FeatureCollection",features:[]}});
  map.addLayer({id:"pharos-gpx-lines",type:"line",source:"pharos-gpx",
    paint:{"line-color":"#b32645","line-width":3.5,"line-opacity":0.9},
    layout:{"line-cap":"round","line-join":"round"}});
  gpxRenderMap().catch(error=>console.warn("Unable to render stored GPX",error));
}
window.pharosGpxAttach=pharosGpxAttach;
window.pharosGpxDetach=()=>{
  pharosGpxLoadSerial++;
  pharosGpxMap=null;
  gpxEl("maps-gpx-panel").hidden=true;
  gpxEl("maps-gpx-toggle").setAttribute("aria-expanded","false");
};
gpxEl("maps-gpx-toggle").addEventListener("click",()=>{
  const panel=gpxEl("maps-gpx-panel");panel.hidden=!panel.hidden;
  gpxEl("maps-gpx-toggle").setAttribute("aria-expanded",String(!panel.hidden));
});
async function pharosGpxImportSelected(){
  if(pharosGpxUploading)return;
  const input=gpxEl("maps-gpx-file"),file=input.files?.[0];
  if(!file)return;
  if(file.size>5*1024*1024){
    gpxFeedback("gpxTooLarge");
    input.value="";
    return;
  }
  // Use the filename, not potentially duplicated internal GPX metadata;
  // users can rename later. Keep UTF-8 names, trim only the .gpx suffix.
  const name=file.name.replace(/\.gpx$/i,"").trim().slice(0,120);
  const data=new FormData();
  data.append("file",file,file.name);
  if(name)data.append("name",name);
  pharosGpxUploading=true;
  input.disabled=true;
  gpxEl("maps-gpx-choose").disabled=true;
  gpxEl("maps-gpx-import-status").textContent=gpxMsg("gpxImporting");
  gpxFeedback("");
  try{
    await api("maps/gpx",{method:"POST",body:data});
    await pharosGpxRefresh();
    gpxFeedback("gpxImported");
  }catch(error){
    gpxFeedback("gpxImportFailed");
    console.warn("GPX import failed",error.message);
  }finally{
    // Allow selecting the same file after an error and uploading another
    // immediately after success; never leave a stale selected filename.
    input.value="";
    input.disabled=false;
    gpxEl("maps-gpx-choose").disabled=false;
    pharosGpxUploading=false;
    gpxEl("maps-gpx-import-status").textContent="";
  }
}
gpxEl("maps-gpx-import-form").addEventListener("submit",event=>{
  event.preventDefault();
  void pharosGpxImportSelected();
});
gpxEl("maps-gpx-choose").addEventListener("click",()=>{
  if(!pharosGpxUploading)gpxEl("maps-gpx-file").click();
});
gpxEl("maps-gpx-file").addEventListener("change",()=>void pharosGpxImportSelected());

window.pharosGpxRefreshLanguage=gpxRenderLists;
