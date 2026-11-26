"use strict";
// One hierarchical catalog. Geofabrik supplies geographic boundaries,
// while verified Protomaps planet builds supply the actual basemap tiles.
// All downloadable data stays local. No external browser resources.
const mapUi=id=>document.getElementById(id);
const mapBytes=count=>count>=1024**3?(count/(1024**3)).toFixed(2)+" GiB":(count/(1024**2)).toFixed(1)+" MiB";
let mapAreas=[],mapById=new Map(),mapInstalled=new Set(),mapInstalledDetails=[],mapBuild=null,mapIndexed=new Set(),mapIndexMetadata=new Map();
let mapSelectedParent=null,mapFilter="",mapLimit=48,mapLoading=false,mapRequesting=new Set(),mapDesiredZoom=new Map(),mapGeoLoaded=false,mapDeleting=new Set(),mapPreviousActiveJobIds='';

function mapPath(area){
  const path=[area.name],seen=new Set([area.id]);let parent=area.parent;
  while(parent&&mapById.has(parent)&&!seen.has(parent)){
    const a=mapById.get(parent);seen.add(parent);path.unshift(a.name);parent=a.parent;
  }
  return path.join(" / ");
}
function mapBreadcrumbs(){
  const root=mapUi('maps-breadcrumbs');root.replaceChildren();
  let chain=[],id=mapSelectedParent;
  const seen=new Set();
  while(id&&mapById.has(id)&&!seen.has(id)){
    const area=mapById.get(id);chain.unshift(area);seen.add(id);id=area.parent;
  }
  function crumb(label,identifier){
    const b=document.createElement('button');b.type='button';b.className='outline-btn';
    b.textContent=label;b.addEventListener('click',()=>{mapSelectedParent=identifier;mapFilter='';mapUi('maps-filter').value='';mapLimit=48;renderMapCatalog();});
    root.append(b);
  }
  crumb(t('mapsAllContinents'),null);
  for(const area of chain)crumb(area.name,area.id);
}
function mapContains(outer,inner){
  return Array.isArray(outer)&&Array.isArray(inner)&&outer.length===4&&inner.length===4&&
    outer[0]<=inner[0]+1e-6&&outer[1]<=inner[1]+1e-6&&
    outer[2]>=inner[2]-1e-6&&outer[3]>=inner[3]-1e-6;
}
function mapCoverage(area,zoom){
  const known=mapInstalledDetails.filter(item=>item.coverage?.bbox&&Number.isInteger(item.coverage.maxzoom));
  const covers=known.filter(item=>mapContains(item.coverage.bbox,area.bbox));
  const adequate=covers.filter(item=>item.coverage.maxzoom>=zoom);
  const moreDetail=known.filter(item=>mapContains(area.bbox,item.coverage.bbox)&&
    item.coverage.maxzoom>zoom);
  return {covers,adequate,moreDetail};
}
function mapCard(area){
  const card=document.createElement('article');card.className='maps-catalog-card';
  const title=document.createElement('strong');title.textContent=area.name;
  const children=mapAreas.filter(item=>item.parent===area.id).length;
  const zoom=mapDesiredZoom.get(area.id)??area.zoom;
  const archiveId=area.id+(zoom===area.zoom?'':'-z'+zoom)+'.pmtiles';
  const state=document.createElement('small');
  state.textContent=mapInstalled.has(archiveId)?t('mapsAlreadyStored'):
    area.downloadable===false?t('mapsDatelineArea'):
    children?t('mapsChildrenCount').replace('{count}',String(children)):t('mapsAreaReady');
  const coverage=mapCoverage(area,zoom);
  if(!mapInstalled.has(archiveId)&&coverage.adequate.length){
    const info=document.createElement('small');info.className='maps-coverage-warning';
    info.textContent=t('mapsCoverageSufficient').replace('{name}',
      coverage.adequate[0].id.replace(/\.pmtiles$/,'')).replace('{zoom}',
      String(coverage.adequate[0].coverage.maxzoom));
    card.append(info);
  }else if(!mapInstalled.has(archiveId)&&coverage.covers.length){
    const info=document.createElement('small');info.className='maps-coverage-warning';
    info.textContent=t('mapsCoverageLowZoom').replace('{zoom}',
      String(Math.max(...coverage.covers.map(item=>item.coverage.maxzoom))));
    card.append(info);
  }
  if(coverage.moreDetail.length){
    const info=document.createElement('small');info.className='maps-coverage-warning';
    info.textContent=t('mapsExistingHigherDetail').replace('{name}',
      coverage.moreDetail[0].id.replace(/\.pmtiles$/,''))
      .replace('{zoom}',String(coverage.moreDetail[0].coverage.maxzoom));
    card.append(info);
  }
  if(area.downloadable!==false){
    const zoomLabel=document.createElement('label');zoomLabel.className='maps-zoom-label';
    zoomLabel.textContent=t('mapsSelectZoom');
    const select=document.createElement('select');
    select.setAttribute('aria-label',t('mapsSelectZoom')+' — '+area.name);
    for(let z=area.zoom;z<=15;z++){
      const option=document.createElement('option');option.value=String(z);
      option.textContent=t('mapsZoomLevel').replace('{zoom}',String(z));
      if(z===zoom)option.selected=true;
      select.append(option);
    }
    select.addEventListener('change',()=>{
      mapDesiredZoom.set(area.id,Number(select.value));renderMapCatalog();
    });
    zoomLabel.append(select);card.append(zoomLabel);
  }
  const canIndex=Boolean(area.pbf_url&&!mapIndexed.has(area.id));
  const hasLocalMap=mapInstalledDetails.some(item=>item.id===area.id+'.pmtiles'||
    item.id.startsWith(area.id+'-z')&&item.id.endsWith('.pmtiles'));
  let searchCheckbox=null;
  if(canIndex&&!mapInstalled.has(archiveId)){
    const optional=document.createElement('label');optional.className='maps-search-optin';
    searchCheckbox=document.createElement('input');
    searchCheckbox.type='checkbox';searchCheckbox.checked=false;
    const description=document.createElement('span');
    description.textContent=t('mapsSearchInclude');
    optional.append(searchCheckbox,description);card.append(optional);
  }else if(mapIndexed.has(area.id)){
    const ready=document.createElement('small');ready.className='maps-search-ready';
    ready.textContent=t('mapsSearchIndexReady');card.append(ready);
    if(mapIndexMetadata.get(area.id)?.tags_ready!==true){
      const upgrade=document.createElement('small');upgrade.className='maps-coverage-warning';
      upgrade.textContent=t('mapsSearchTagsUpgradeHint');card.append(upgrade);
    }
  }
  const actions=document.createElement('div');actions.className='maps-card-actions';
  if(children){
    const explore=document.createElement('button');explore.type='button';explore.className='outline-btn';
    explore.textContent=t('mapsExplore');
    explore.addEventListener('click',()=>{mapSelectedParent=area.id;mapFilter='';mapUi('maps-filter').value='';mapLimit=48;renderMapCatalog();});
    actions.append(explore);
  }
  const download=document.createElement('button');download.type='button';
  download.className='primary-btn';download.textContent=t('mapsDownload');
  download.disabled=!mapBuild||area.downloadable===false||mapInstalled.has(archiveId)||mapRequesting.has(archiveId);
  download.addEventListener('click',async()=>{
    if(coverage.adequate.length&&!window.confirm(t('mapsCoveredConfirm').replace('{name}',area.name)))return;
    const extent=area.bbox;
    const large=((extent[2]-extent[0])*(extent[3]-extent[1])>4&&zoom>=14)||area.parent===null;
    if(large&&!window.confirm(t('mapsLargeAreaConfirm').replace('{name}',area.name)))return;
    download.disabled=true;
    mapRequesting.add(archiveId);
    const feedback=mapUi('maps-feedback');feedback.textContent='';
    try{
      await api('maps/downloads',{method:'POST',
        body:JSON.stringify({region_id:area.id,source_id:mapBuild.id,zoom,include_search:Boolean(searchCheckbox?.checked)})});
      feedback.textContent=t('mapsQueued');
      await loadMapJobs();
      if(window.pharosLoadJobs)await window.pharosLoadJobs();
    }catch(error){feedback.textContent=t('mapsSubmitError')+': '+error.message;}
    finally{mapRequesting.delete(archiveId);renderMapCatalog();}
  });
  actions.append(download);
  if(canIndex&&hasLocalMap){
    const onlyIndex=document.createElement('button');onlyIndex.type='button';
    onlyIndex.className='outline-btn';onlyIndex.textContent=t('mapsSearchOnly');
    onlyIndex.disabled=mapRequesting.has('index:'+area.id);
    onlyIndex.addEventListener('click',async()=>{
      if(!window.confirm(t('mapsSearchIndexConfirm').replace('{name}',area.name)))return;
      const key='index:'+area.id;mapRequesting.add(key);onlyIndex.disabled=true;
      try{
        await api('maps/search/indexes',{method:'POST',
          body:JSON.stringify({region_id:area.id})});
        mapUi('maps-feedback').textContent=t('mapsQueued');
        await loadMapJobs();await window.pharosLoadJobs?.();
      }catch(error){
        mapUi('maps-feedback').textContent=t('mapsSubmitError')+': '+error.message;
      }finally{mapRequesting.delete(key);renderMapCatalog();}
    });
    actions.append(onlyIndex);
  }
  if(area.pbf_url&&hasLocalMap&&mapIndexed.has(area.id)){
    const rebuild=document.createElement('button');rebuild.type='button';
    rebuild.className='outline-btn';rebuild.textContent=t('mapsSearchRebuild');
    const key='index:'+area.id;rebuild.disabled=mapRequesting.has(key);
    rebuild.addEventListener('click',async()=>{
      if(!window.confirm(t('mapsSearchRebuildConfirm').replace('{name}',area.name)))return;
      mapRequesting.add(key);rebuild.disabled=true;
      try{
        await api('maps/search/indexes',{method:'POST',
          body:JSON.stringify({region_id:area.id,refresh:true})});
        mapUi('maps-feedback').textContent=t('mapsQueued');
        await loadMapJobs();await window.pharosLoadJobs?.();
      }catch(error){
        mapUi('maps-feedback').textContent=t('mapsSubmitError')+': '+error.message;
      }finally{mapRequesting.delete(key);renderMapCatalog();}
    });
    actions.append(rebuild);
  }
  card.append(title,state,actions);return card;
}
function renderMapCatalog(){
  mapBreadcrumbs();
  const root=mapUi('maps-catalog-list');root.replaceChildren();
  const query=mapFilter.trim().toLocaleLowerCase();
  const entries=mapAreas.filter(area=>query?mapPath(area).toLocaleLowerCase().includes(query):area.parent===mapSelectedParent);
  entries.sort((a,b)=>a.name.localeCompare(b.name));
  if(!entries.length){
    const empty=document.createElement('p');
    empty.textContent=!mapGeoLoaded?t('mapsAreasLoading'):(mapAreas.length?t('mapsNoMatches'):t('mapsAreaCatalogUnavailable'));
    root.append(empty);return;
  }
  for(const area of entries.slice(0,mapLimit))root.append(mapCard(area));
  if(entries.length>mapLimit){
    const more=document.createElement('button');more.type='button';more.className='outline-btn maps-more';
    more.textContent=t('kiwixMore');more.addEventListener('click',()=>{mapLimit+=48;renderMapCatalog();});
    root.append(more);
  }
}
async function loadMapCatalog({refresh=false}={}){
  if(mapLoading)return;
  mapLoading=true;
  const feedback=mapUi('maps-feedback');
  // Start the local request FIRST and render as soon as its own response arrives.
  // A slow or unavailable publisher must never delay the installed map list.
  void window.pharosGpxRefresh?.().catch(error=>console.warn("GPX library unavailable",error.message));
  const localPromise=api('maps/regions').then(archives=>{
    mapInstalledDetails=archives.items||[];
    mapInstalled=new Set(mapInstalledDetails.map(item=>item.id));
    renderInstalledMaps(mapInstalledDetails);
    renderMapCatalog();
    return archives;
  }).catch(error=>{
    renderInstalledMaps(mapInstalledDetails);
    console.warn('PHAROS local map inventory unavailable:',error.message);
    return null;
  });
  const indexPromise=api('maps/search/indexes').then(geo=>{
    mapIndexed=new Set((geo.items||[]).map(item=>item.area));
    mapIndexMetadata=new Map((geo.items||[]).map(item=>[item.area,item]));
    renderMapCatalog();renderInstalledMaps(mapInstalledDetails);
  }).catch(error=>console.warn('Map search index inventory unavailable:',error.message));
  const geoPromise=api('maps/areas').then(geo=>{
    mapGeoLoaded=true;
    mapAreas=geo.items||[];
    mapById=new Map(mapAreas.map(area=>[area.id,area]));
    if(mapSelectedParent&&!mapById.has(mapSelectedParent))mapSelectedParent=null;
    renderMapCatalog();
    // Update human-friendly names when the region directory becomes ready.
    renderInstalledMaps(mapInstalledDetails);
    if(geo.status==='unavailable')feedback.textContent=t('mapsAreaCatalogUnavailable');
    else if(feedback.textContent===t('mapsAreaCatalogUnavailable'))feedback.textContent='';
  }).catch(error=>{
    mapGeoLoaded=true;
    renderMapCatalog();
    feedback.textContent=t('mapsAreaCatalogUnavailable');
    console.warn('PHAROS geographic directory unavailable:',error.message);
  });
  const sourcePromise=api('maps/catalog'+(refresh?'?refresh=true':'')).then(sources=>{
    mapBuild=(sources.items||[])[0]||null;
    mapUi('maps-source-status').textContent=mapBuild?
      t('mapsSourceAvailable').replace('{date}',mapBuild.date):
      t('mapsSourceNotVerified');
    renderMapCatalog();
  }).catch(error=>{
    mapBuild=null;
    mapUi('maps-source-status').textContent=t('mapsSourceNotVerified');
    renderMapCatalog();
    console.warn('PHAROS publisher source verification unavailable:',error.message);
  });
  try{
    await Promise.allSettled([localPromise,geoPromise,sourcePromise,indexPromise]);
  }finally{
    mapLoading=false;
  }
}
function mapArchiveAreaName(item){
  const name=item.id.replace(/\.pmtiles$/,"");
  const original=name.replace(/-z\d{1,2}$/,"");
  return mapById.get(original)?.name||original.replace(/-/g," ");
}
function mapArchiveHierarchy(item){
  const original=item.id.replace(/\.pmtiles$/,"").replace(/-z\d{1,2}$/,"");
  const area=mapById.get(original);
  if(!area)return "";
  // Parent path only: the clickable title already gives the selected region.
  const full=mapPath(area).split(" / ");
  return full.length>1?full.slice(0,-1).join(" / "):"";
}
function mapArchiveDetail(card,label,value){
  if(!value)return;
  const detail=document.createElement("div");detail.className="maps-library-detail";
  const caption=document.createElement("span");caption.textContent=label;
  const field=document.createElement("strong");field.textContent=String(value);
  detail.append(caption,field);card.append(detail);
}
function mapArchiveDate(iso){
  if(!iso)return "";
  const date=new Date(iso);
  if(Number.isNaN(date.getTime()))return "";
  return date.toLocaleDateString((document.documentElement.lang||"it").toLowerCase(),{
    year:"numeric",month:"short",day:"numeric"
  });
}
async function mapDeleteArchive(item,button){
  if(mapDeleting.has(item.id))return;
  const confirmation=t("mapsDeleteConfirm")
    .replace("{name}",item.id).replace("{size}",mapBytes(item.size_bytes));
  if(!window.confirm(confirmation))return;
  mapDeleting.add(item.id);button.disabled=true;
  const feedback=mapUi("maps-feedback");
  feedback.textContent="";
  try{
    const result=await api("maps/regions/"+encodeURIComponent(item.id),
      {method:"DELETE"});
    const listed=await api("maps/regions");
    mapInstalledDetails=listed.items||[];
    mapInstalled=new Set(mapInstalledDetails.map(entry=>entry.id));
    renderInstalledMaps(mapInstalledDetails);
    renderMapCatalog();
    feedback.textContent=t("mapsDeleteSuccess").replace("{name}",result.deleted);
    if(result.metadata_removed===false)
      feedback.textContent+=" · "+t("mapsDeleteMetadataWarning");
  }catch(error){
    const message=String(error.message||error);
    feedback.textContent=message.includes("maps_delete_download_active")?
      t("mapsDeleteBusy"):t("mapsDeleteFailed")+" ("+message+")";
  }finally{
    mapDeleting.delete(item.id);
    button.disabled=false;
  }
}
function renderInstalledMaps(items){
  const root=mapUi("maps-region-list");root.replaceChildren();
  if(!items.length){
    const p=document.createElement("p");p.className="help-text";
    p.textContent=t("mapsEmpty");root.append(p);return;
  }
  for(const item of items){
    const card=document.createElement("article");
    card.className="maps-library-card";
    const title=document.createElement(item.viewer?.vector?"button":"strong");
    title.className="maps-library-title";
    title.textContent=mapArchiveAreaName(item);
    if(item.viewer?.vector){
      title.type="button";
      title.setAttribute("aria-label",t("mapsView")+" — "+mapArchiveAreaName(item));
      title.addEventListener("click",()=>window.pharosOpenMap?.(item));
    }
    card.append(title);
    const hierarchy=mapArchiveHierarchy(item);
    if(hierarchy){
      const path=document.createElement("small");
      path.className="maps-library-hierarchy";
      path.textContent=hierarchy;
      card.append(path);
    }
    const filename=document.createElement("small");
    filename.className="maps-library-filename";
    filename.textContent=item.id;
    card.append(filename);
    const details=document.createElement("div");details.className="maps-library-details";
    card.append(details);
    const zoom=item.viewer?.maxzoom??item.coverage?.maxzoom;
    if(Number.isInteger(zoom))
      mapArchiveDetail(details,t("mapsLibraryZoom"),String(zoom));
    const updated=mapArchiveDate(item.modified_at);
    if(updated){
      const date=document.createElement("small");
      date.className="maps-library-date";
      date.textContent=t("mapsLibraryDate")+": "+updated;
      card.append(date);
    }
    const regionId=item.id.replace(/\.pmtiles$/,'').replace(/-z\d{1,2}$/,'');
    if(mapIndexed.has(regionId)){
      const ready=document.createElement('small');ready.className='maps-search-ready';
      ready.textContent=t('mapsSearchIndexReady');card.append(ready);
    }
    if(item.coverage?.potentially_redundant){
      const warning=document.createElement("small");
      warning.className="maps-coverage-warning";
      warning.textContent=t("mapsRedundantPossible").replace("{name}",
        (item.coverage.covered_by||[]).map(other=>other.id).join(", "));
      card.append(warning);
    }
    if(!item.coverage){
      const note=document.createElement("small");note.className="maps-library-note";
      note.textContent=t("mapsCoverageUnknown");card.append(note);
    }
    const actions=document.createElement("div");actions.className="maps-library-actions";
    if(!item.viewer?.vector){
      const unsupported=document.createElement("small");
      unsupported.textContent=t("mapsViewerUnsupportedArchive");
      card.append(unsupported);
    }
    const remove=document.createElement("button");remove.type="button";
    remove.className="outline-btn maps-library-delete";
    remove.textContent=t("mapsDelete");
    remove.disabled=mapDeleting.has(item.id);
    remove.addEventListener("click",()=>mapDeleteArchive(item,remove));
    if(mapIndexed.has(regionId)){
      const delIndex=document.createElement('button');delIndex.type='button';
      delIndex.className='outline-btn';delIndex.textContent=t('mapsSearchDelete');
      delIndex.addEventListener('click',async()=>{
        if(!window.confirm(t('mapsSearchDeleteConfirm').replace('{name}',regionId)))return;
        delIndex.disabled=true;
        try{
          await api('maps/search/indexes/'+encodeURIComponent(regionId),{method:'DELETE'});
          mapIndexed.delete(regionId);
          renderMapCatalog();renderInstalledMaps(mapInstalledDetails);
          mapUi('maps-feedback').textContent=t('mapsSearchDeleted');
        }catch(error){
          mapUi('maps-feedback').textContent=t('mapsSearchDeleteFailed')+': '+error.message;
          delIndex.disabled=false;
        }
      });
      actions.append(delIndex);
    }
    actions.append(remove);
    card.append(actions);root.append(card);
  }
}
async function loadMapJobs(){
  try{
    const result=await api('jobs');
    const active=(result.items||[]).filter(job=>['download-map','index-map'].includes(job.kind)&&
      (job.status==='queued'||job.status==='running'));
    const currentActiveIds=active.map(job=>job.id).join(',');
    if(mapPreviousActiveJobIds&&currentActiveIds!==mapPreviousActiveJobIds){
      void api('maps/search/indexes').then(data=>{
        mapIndexed=new Set((data.items||[]).map(item=>item.area));
        mapIndexMetadata=new Map((data.items||[]).map(item=>[item.area,item]));
        renderMapCatalog();renderInstalledMaps(mapInstalledDetails);
      }).catch(error=>console.warn('Map indexes refresh unavailable:',error.message));
    }
    mapPreviousActiveJobIds=currentActiveIds;
    const panel=mapUi('maps-active-panel'),root=mapUi('maps-active-list');
    root.replaceChildren();panel.hidden=!active.length;
    for(const job of active){
      const row=document.createElement('div');row.className='maps-job';
      const heading=document.createElement('strong');
      heading.textContent=mapById.get(job.label)?.name||job.label;
      const description=document.createElement('small');
      description.textContent=t(job.status==='queued'?'jobsPending':'jobsRunning')+
        (job.detail?' · '+(t(job.detail)===job.detail?t('mapsWorking'):t(job.detail)):'');
      const size=document.createElement('small');
      const stage=String(job.detail||'');
      const indexing=['maps_search_importing','maps_search_building_fts',
        'maps_search_building_rtree','maps_search_building_tags',
        'maps_search_verifying'].includes(stage);
      const count=Number(job.bytes_done||0);
      const total=Number(job.bytes_total||0);
      size.textContent=indexing?
        (total>0?t('mapsSearchIndexingProgress')
          .replace('{done}',count.toLocaleString())
          .replace('{total}',total.toLocaleString()):
          t('mapsSearchIndexingCount').replace('{count}',count.toLocaleString())):
        job.bytes_done?mapBytes(job.bytes_done):t('mapsWaiting');
      const cancel=document.createElement('button');cancel.className='outline-btn';cancel.type='button';
      cancel.textContent=t('jobsCancel');
      cancel.addEventListener('click',async()=>{
        cancel.disabled=true;
        try{await api('jobs/'+encodeURIComponent(job.id)+'/cancel',{method:'POST'});await loadMapJobs();}
        catch(error){description.textContent=error.message;cancel.disabled=false;}
      });
      row.append(heading,description,size,cancel);root.append(row);
    }
  }catch(error){console.warn('Map jobs unavailable:',error.message);}
}
window.pharosLoadMaps=()=>loadMapCatalog();
window.pharosRefreshMapsLabels=()=>{renderMapCatalog();};
mapUi('maps-refresh').addEventListener('click',()=>loadMapCatalog({refresh:true}));
mapUi('maps-filter').addEventListener('input',event=>{
  mapFilter=event.target.value;mapLimit=48;renderMapCatalog();
});
setInterval(()=>{
  if(mapUi('view-maps')?.classList.contains('active'))void loadMapJobs();
},5000);
