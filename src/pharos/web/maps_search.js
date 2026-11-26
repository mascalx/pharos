"use strict";
// Searches only local SQLite indexes. No Tiiny, online geocoder or remote tiles.
const mapSearchInput=document.getElementById('maps-search-query');
const mapSearchForm=document.getElementById('maps-search-form');
const mapSearchResults=document.getElementById('maps-search-results');
const mapSearchFeedback=document.getElementById('maps-search-feedback');
let mapSearchSerial=0;

function mapSearchRender(items){
  mapSearchResults.replaceChildren();
  for(const item of items){
    const row=document.createElement('article');row.className='maps-search-result';
    const info=document.createElement('div');
    const title=document.createElement('strong');title.textContent=item.name;
    const meta=document.createElement('small');
    const categoryKey={
      place:'mapsSearchPlace',address:'mapsSearchAddress',
      street:'mapsSearchStreet',poi:'mapsSearchPoi'
    }[item.category]||'mapsSearchPoi';
    meta.textContent=t(categoryKey)+(item.detail?' · '+item.detail:'')+
      (item.locality_hint?' · '+t('mapsSearchNearby').replace('{name}',item.locality_hint):'');
    info.append(title,meta);
    if(item.approximate_street&&item.requested_number){
      const caution=document.createElement('small');
      caution.className='maps-search-approximate';
      caution.textContent=t('mapsSearchApproximateNumber')
        .replace('{number}',String(item.requested_number));
      info.append(caution);
    }
    const open=document.createElement('button');
    open.type='button';open.className='outline-btn';
    open.textContent=t('mapsSearchShow');
    open.addEventListener('click',async()=>{
      open.disabled=true;
      try{
        const success=await window.pharosOpenMapForPoint?.(item.latitude,item.longitude);
        if(!success)mapSearchFeedback.textContent=t('mapsSearchNoMap');
        else mapSearchFeedback.textContent='';
      }catch(error){
        mapSearchFeedback.textContent=t('mapsSearchOpenFailed')+': '+error.message;
      }finally{open.disabled=false;}
    });
    row.append(info,open);mapSearchResults.append(row);
  }
}

mapSearchForm?.addEventListener('submit',async event=>{
  event.preventDefault();
  const query=mapSearchInput.value.trim();
  const serial=++mapSearchSerial;
  if(query.length<2){mapSearchFeedback.textContent=t('mapsSearchTooShort');return;}
  mapSearchFeedback.textContent=t('mapsSearchSearching');
  try{
    const data=await api('maps/search?q='+encodeURIComponent(query)+'&limit=30');
    if(serial!==mapSearchSerial)return;
    mapSearchRender(data.items||[]);
    mapSearchFeedback.textContent=data.indexes===0?
      t('mapsSearchNoIndexes'):(data.items||[]).length===0?
      t('mapsSearchNoResults'):t('mapsSearchResultCount').replace('{count}',String(data.items.length));
  }catch(error){
    if(serial!==mapSearchSerial)return;
    mapSearchResults.replaceChildren();
    mapSearchFeedback.textContent=t('mapsSearchFailed')+': '+error.message;
  }
});
window.pharosRefreshMapSearch=()=>{};
