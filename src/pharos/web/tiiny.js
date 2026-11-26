'use strict';
// Native Tiiny management belongs to the Core, not the Module Manager.
Object.assign(IT,{
  tiinyCoreTitle:'Tiiny · gestione Core',
  tiinyCoreHelp:'Modelli scaricati sul dispositivo, modelli caricati in NPU e stato della coda.',
  tiinyCoreLoaded:'In NPU',tiinyCoreDownloaded:'Scaricato',tiinyCoreUnknown:'Stato sconosciuto',
  tiinyCoreLoad:'Carica in NPU',tiinyCoreUnload:'Rimuovi dalla NPU',
  tiinyCoreLimited:'Inventario esteso del Gateway non disponibile: il catalogo /v1/models potrebbe essere parziale. Controlla /gateway/v1/models e le credenziali.',
  tiinyCoreGeneric:'Provider generico: funzioni specifiche Tiiny non disponibili.',
  tiinyCoreFailure:'Catalogo Tiiny non disponibile. Controlla la connessione al servizio configurato.',
  tiinyCoreCatalogUnavailable:'Non è stato possibile recuperare l’inventario modelli dal Gateway. Controlla /gateway/v1/models e /v1/models.',
  tiinyCoreQueue:'Coda inferenze: {waiting} in attesa, {active} in esecuzione.',
  tiinyCoreLoading:'Richiesta inviata. Aggiorna per verificare lo stato reale.',
  tiinyCoreNoModels:'Nessun modello installato rilevato.',
  tiinyCoreGatewayComplete:'Inventario esteso Gateway disponibile.',
  tiinyCoreConfirmUnload:'Rimuovere {model} dalla NPU? Il file scaricato rimarrà sul dispositivo.'
});
Object.assign(EN,{
  tiinyCoreTitle:'Tiiny · Core management',
  tiinyCoreHelp:'Models downloaded on the device, NPU resident models and queue status.',
  tiinyCoreLoaded:'NPU resident',tiinyCoreDownloaded:'Downloaded',tiinyCoreUnknown:'Unknown status',
  tiinyCoreLoad:'Load in NPU',tiinyCoreUnload:'Unload from NPU',
  tiinyCoreLimited:'Extended Gateway inventory unavailable: /v1/models may provide only a partial catalog. Check /gateway/v1/models access and credentials.',
  tiinyCoreGeneric:'Generic provider: Tiiny-specific controls are unavailable.',
  tiinyCoreFailure:'Tiiny inventory unavailable. Check configured service connectivity.',
  tiinyCoreCatalogUnavailable:'Could not retrieve the model inventory from Gateway. Check /gateway/v1/models and /v1/models.',
  tiinyCoreQueue:'Inference queue: {waiting} pending, {active} running.',
  tiinyCoreLoading:'Request submitted. Refresh to verify actual state.',
  tiinyCoreNoModels:'No installed models found.',
  tiinyCoreGatewayComplete:'Extended Gateway inventory available.',
  tiinyCoreConfirmUnload:'Unload {model} from NPU? The downloaded model file will remain on device.'
});
let tiinyCoreGeneration=0;
window.pharosLoadTiinyCore=async function(){
  const panel=document.getElementById('tiiny-core-panel');
  const inventory=document.getElementById('tiiny-core-inventory');
  const note=document.getElementById('tiiny-core-note');
  const feedback=document.getElementById('tiiny-core-feedback');
  const mode=appConfig?.ai?.provider || '';
  panel.hidden=!['tiiny_direct','tiiny_gateway'].includes(mode);
  if(panel.hidden)return;
  const generation=++tiinyCoreGeneration;
  inventory.replaceChildren();feedback.textContent='';note.textContent='…';
  try{
    const [catalog,queue]=await Promise.all([api('ai/catalog'),api('ai/queue')]);
    if(generation!==tiinyCoreGeneration)return;
    const q=t('tiinyCoreQueue').replace('{waiting}',String(queue.waiting)).replace('{active}',queue.active?'1':'0');
    const info=catalog.complete
      ? (mode==='tiiny_gateway'?t('tiinyCoreGatewayComplete'):'')
      : t(catalog.reason==='gateway_models_unavailable'?'tiinyCoreCatalogUnavailable':'tiinyCoreLimited');
    note.textContent=(queue.enabled ? q+' ' : '') + info + (catalog.residency_verified===false?' '+t('tiinyCoreUnknown'):'');
    if(!catalog.models?.length){inventory.textContent=t('tiinyCoreNoModels');return;}
    for(const item of catalog.models){
      const row=document.createElement('div');row.className='tiiny-core-model';
      const info=document.createElement('div');info.className='tiiny-core-model-info';
      const name=document.createElement('strong');name.textContent=item.id;
      const meta=document.createElement('small');
      const residency=item.loaded===true?t('tiinyCoreLoaded'):item.loaded===false?t('tiinyCoreDownloaded'):t('tiinyCoreUnknown');
      const parts=[residency,item.kind,item.npu_usage!=null?String(item.npu_usage)+'% NPU':''];
      meta.textContent=parts.filter(Boolean).join(' · ');info.append(name,meta);
      if(item.description){const desc=document.createElement('small');desc.textContent=item.description;info.append(desc);}
      row.append(info);
      if(mode==='tiiny_direct'&&item.downloaded===true&&item.loaded!==null){
        const btn=document.createElement('button');btn.type='button';btn.className='outline-btn';
        const action=item.loaded?'stop':'launch';btn.textContent=t(item.loaded?'tiinyCoreUnload':'tiinyCoreLoad');
        btn.addEventListener('click',async()=>{
          if(action==='stop'&&!confirm(t('tiinyCoreConfirmUnload').replace('{model}',item.id)))return;
          btn.disabled=true;
          try{await api('ai/models/'+action,{method:'POST',body:JSON.stringify({model:item.id})});
            feedback.textContent=t('tiinyCoreLoading');await window.pharosLoadTiinyCore();}
          catch(error){feedback.textContent=error.message;btn.disabled=false;}
        });
        row.append(btn);
      }
      inventory.append(row);
    }
  }catch(error){if(generation===tiinyCoreGeneration){note.textContent=t('tiinyCoreFailure');feedback.textContent=error.message;}}
};
document.getElementById('tiiny-core-refresh').addEventListener('click',window.pharosLoadTiinyCore);
