'use strict';



let kbCache=[];
let kbLoaded=false;
let kbModuleEnabled=false;
window.pharosSetKnowledgeModuleEnabled = enabled => {
  if (kbModuleEnabled !== enabled) { kbLoaded=false; kbCache=[]; ++kbLoadGeneration; }
  kbModuleEnabled=enabled; renderChatKB();updateEmbeddingButtons();
};
let kbLoadGeneration=0;
let embeddingListLoading=false;
// UI-only tracking of in-flight uploads. The real embedding/indexing counts
// come from SQLite; the elapsed time never pretends to be a percentage.
const activeKBUploads=new Map();
// Acknowledgements belong to the KB which received the file, not to the
// separate "New Knowledge Base" form. Persist across card re-renders.
const kbUploadNotices=new Map();
function setKBUploadNotice(kbId, message='', kind='', jobId='') {
  if(message) kbUploadNotices.set(kbId,{message,kind,jobId});
  else kbUploadNotices.delete(kbId);
  const target=el('kb-upload-feedback-'+kbId);
  if(target){
    target.textContent=message;
    target.className='inline-feedback'+(kind?' '+kind:'');
    target.hidden=!message;
  }
}
// A terminal event replaces its transient acknowledgement in Notifications.
// Until then the message survives automatic refresh of the corresponding KB.
window.pharosSyncKBUploadNotices=jobs=>{
  const byId=new Map(jobs.map(job=>[job.id,job]));
  for(const [kbId,notice] of kbUploadNotices){
    if(!notice.jobId)continue; // The upload request may still be in flight.
    const job=byId.get(notice.jobId);
    if(job&&['completed','failed','cancelled','interrupted'].includes(job.status))
      setKBUploadNotice(kbId);
  }
};
function kbIngestProgressText(doc, seconds, filename) {
  const elapsed=`${Math.floor(seconds/60)}m ${String(seconds%60).padStart(2,'0')}s`;
  if (!doc || doc.status!=='indexing') return `${t('kbIngestPreparing')} ${filename} · ${elapsed}`;
  const stage=doc.progress_stage==='indexing' ? t('kbIngestVectors') : t('kbIngestEmbedding');
  const count=Number.isInteger(doc.progress_done) && Number.isInteger(doc.progress_total) && doc.progress_total>0
    ? ` ${doc.progress_done}/${doc.progress_total}` : '';
  return `${stage}${count} · ${elapsed}`;
}
function syncKBUploadView(kb_id) {
  const state=activeKBUploads.get(kb_id);if(!state || !state.progressNode)return;
  state.fileField.disabled=true;state.button.disabled=true;
  state.progressNode.hidden=false;
  state.progressNode.textContent=kbIngestProgressText(state.doc,Math.floor((Date.now()-state.started)/1000),state.filename);
}
async function pollKBIngestState(state) {
  if(state.polling || !activeKBUploads.has(state.kb_id))return;
  state.polling=true;
  try{
    const response=await api('kb/'+encodeURIComponent(state.kb_id)+'/documents');
    if(!activeKBUploads.has(state.kb_id))return;
    const candidates=(response.documents||[]).filter(d=>d.filename===state.filename && d.status==='indexing');
    if(candidates.length===1)state.doc=candidates[0];
    syncKBUploadView(state.kb_id);
  } catch(_) { /* Never replace an active upload with a transient poll error. */ }
  finally {state.polling=false;}
}
async function uploadKBFile(kb_id, fileField, progressNode, button) {
  if(activeKBUploads.has(kb_id) || !fileField.files?.length)return;
  const selected=fileField.files[0];
  const state={kb_id,filename:selected.name,started:Date.now(),doc:null,polling:false,
    progressNode,button,fileField};
  activeKBUploads.set(kb_id,state);
  syncKBUploadView(kb_id);
  setKBUploadNotice(kb_id,t('kbIngestPreparing')+' '+selected.name);
  const body=new FormData();body.append('file',selected,selected.name);
  try {
    const job=await api('kb/'+encodeURIComponent(kb_id)+'/documents',{method:'POST',body});
    setKBUploadNotice(kb_id,t('kbIngestQueued')+' · '+selected.name,'ok',job.id);
    if(window.pharosLoadJobs)await window.pharosLoadJobs();
  }catch(e){
    setKBUploadNotice(kb_id,t('kbError')+': '+(e.message==='http_413'?t('kbUploadTooBig'):e.message),'bad');
  }finally {
    activeKBUploads.delete(kb_id);
    // Restore current controls even if refresh fails or the card was re-rendered.
    state.fileField.value='';state.fileField.disabled=false;state.button.disabled=false;
    state.progressNode.hidden=true;
    kbLoaded=false;await loadKnowledge();
  }
}

const make = (tag, className='', text='') => {
  const node=document.createElement(tag);
  if (className) node.className=className;
  if (text) node.textContent=text;
  return node;
};
function actionButton(label, handler, secondary=true) {
  const button=make('button',secondary?'outline-btn':'primary-btn',label);
  button.type='button';button.addEventListener('click',handler);return button;
}
function selectedChatKB() {
  return [...document.querySelectorAll('#chat-kb-list input:checked')].map(box=>box.value);
}
window.selectedChatKB = selectedChatKB;
function renderChatKB() {
  const host=el('chat-kb-list');const selected=new Set(selectedChatKB());host.replaceChildren();
  const usable=kbCache.filter(kb=>kb.indexed_documents>0 || kb.zim_sources_count>0);
  if (!kbModuleEnabled || !usable.length) {
    host.append(make('span','kb-muted',t('chatKBNone')));return;
  }
  for (const kb of usable) {
    const label=make('label','chat-kb-choice');
    const cb=document.createElement('input');cb.type='checkbox';cb.value=kb.id;cb.checked=selected.has(kb.id);
    cb.addEventListener('change',()=>{
      if (selectedChatKB().length>5){cb.checked=false;el('chat-feedback').textContent=t('chatKBMax');}
      else el('chat-feedback').textContent='';
    });
    label.append(cb,make('span','',kb.name));host.append(label);
  }
}
function feedback(text='',kind='') {
  const target=el('kb-feedback');target.textContent=text;target.className='inline-feedback '+kind;
}
async function operate(operation) {
  feedback(t('kbBusy'));
  try { await operation();kbLoaded=false;await loadKnowledge();feedback(t('kbDone'),'ok'); }
  catch(e){feedback(t('kbError')+': '+(e.message==='http_413'?t('kbUploadTooBig'):e.message),'bad');}
}
async function loadKnowledge() {
  if (!kbModuleEnabled || kbLoaded) return;
  const generation=++kbLoadGeneration;
  try {
    const data=await api('kb');
    const unique=[...new Map((data.items||[]).map(kb=>[kb.id,kb])).values()];
    const dataWithDocs=await Promise.all(unique.map(async kb=>{
      try {
        const [documents,links,lexical]=await Promise.all([
          api('kb/'+encodeURIComponent(kb.id)+'/documents'),
          api('kb/'+encodeURIComponent(kb.id)+'/zim-sources'),
          api('kb/'+encodeURIComponent(kb.id)+'/lexical').catch(()=>({state:'unavailable'}))]);
        return {kb,docs:documents.documents,zims:links.items||[],lexical:lexical.state};
      }catch(e){return {kb,docs:[],zims:[],error:e.message};}
    }));
    // Only the most recent refresh may render. Prevents duplicate cards from races.
    if (generation!==kbLoadGeneration)return;
    kbCache=unique;kbLoaded=true;renderChatKB();
    const manager=el('kb-manager');manager.replaceChildren();
    el('kb-create-form').hidden=!data.enabled;
    if(!data.enabled || !kbCache.length){
      manager.append(make('p','kb-muted',t(data.enabled?'kbNoItems':'kbDisabled')));return;
    }
    for(const {kb,docs,zims,error,lexical} of dataWithDocs)renderManagerCard(manager,kb,docs,error,zims,lexical);
  } catch(e) {if(generation===kbLoadGeneration){kbLoaded=false;feedback(t('kbError')+': '+e.message,'bad');}}
}
window.pharosLoadKnowledge=loadKnowledge;
function renderManagerCard(host,kb,docs,error,zims=[],lexical='unavailable') {
  const card=make('article','panel kb-manage-card');
  const header=make('div','kb-card-head');
  const details=make('div','kb-title-details');
  details.append(make('h3','',kb.name),make('p','',kb.description||''),
    make('small','kb-muted',`${kb.indexed_documents} ${t('kbIndexedCount')} / ${kb.total_documents} ${t('kbTotalCount')}`));
  const tools=make('div','kb-buttons');
  const editor=make('form','kb-inline-editor');editor.hidden=true;
  const name=document.createElement('input');name.value=kb.name;name.required=true;name.maxLength=100;
  name.setAttribute('aria-label',t('kbName'));
  const desc=document.createElement('input');desc.value=kb.description;desc.maxLength=500;
  desc.setAttribute('aria-label',t('kbDescription'));
  const save=make('button','primary-btn',t('kbSave'));save.type='submit';
  editor.append(name,desc,save,actionButton(t('kbCancel'),()=>{editor.hidden=true;}));
  editor.addEventListener('submit',e=>{
    e.preventDefault();operate(()=>api('kb/'+encodeURIComponent(kb.id),{
      method:'PATCH',body:JSON.stringify({name:name.value.trim(),description:desc.value.trim()})}));
  });
  tools.append(actionButton(t('kbEdit'),()=>{editor.hidden=!editor.hidden;}));
  const deleteKB=actionButton(t('kbDelete'),()=>{
    if (window.confirm(t('kbRemoveKBConfirm')+'\n'+kb.name+' ('+kb.total_documents+' '+t('kbDocuments')+')'))
      operate(()=>api('kb/'+encodeURIComponent(kb.id),{method:'DELETE'}));
  });
  deleteKB.classList.add('destructive-action');
  tools.classList.add('separated-actions');tools.append(deleteKB);
  header.append(details,tools);card.append(header,editor);
  // FTS5 is a derived, opt-in index, never a replacement for the vector KB.
  const lexicalArea=make('div','kb-lexical-actions');
  const lexicalStates={ready:'kbLexicalReady',stale:'kbLexicalStale',
    missing:'kbLexicalMissing',unavailable:'kbLexicalUnavailable'};
  const lexicalStatus=make('small','kb-muted',t(lexicalStates[lexical]||'kbLexicalUnavailable'));
  const lexicalBuild=actionButton(t(lexical==='ready'?'kbLexicalRebuild':'kbLexicalBuild'),async()=>{
    if(!window.confirm(t('kbLexicalConfirm')+'\n'+kb.name))return;
    lexicalBuild.disabled=true;
    try{
      await api('kb/'+encodeURIComponent(kb.id)+'/lexical/build',{method:'POST'});
      lexicalStatus.textContent=t('kbLexicalQueued');
      if(window.pharosLoadJobs)await window.pharosLoadJobs();
    }catch(e){lexicalStatus.textContent=t('kbError')+': '+e.message;}
    finally{lexicalBuild.disabled=false;}
  });
  lexicalBuild.disabled=kb.indexed_documents===0||lexical==='unavailable';
  lexicalArea.append(lexicalStatus,lexicalBuild);
  card.append(lexicalArea);
  card.append(make('h4','kb-docs-title',t('kbDocuments')));
  const docList=make('div','kb-doc-list');
  if (error) docList.append(make('p','kb-muted',t('kbError')+': '+error));
  else if (!docs.length) docList.append(make('p','kb-muted',t('kbNoDocs')));
  else for (const doc of docs) {
    const row=make('div','kb-document-row');
    const title=make('div','kb-document-info');
    const a=make('a','kb-file-link',doc.filename);
    a.href=`/api/kb/${encodeURIComponent(kb.id)}/documents/${encodeURIComponent(doc.id)}/file`;
    a.addEventListener('click',event=>{
      if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;
      if(window.pharosOpenKBDocument){
        event.preventDefault();
        void window.pharosOpenKBDocument(a.href,doc.filename);
      }
    });
    a.target='_blank';a.rel='noopener noreferrer';
    const progress=doc.status==='indexing' ? ' · '+kbIngestProgressText(doc,0,doc.filename) : '';
    const problem=doc.status==='failed' && doc.error ? ' · '+doc.error : '';
    title.append(a,make('small','kb-muted',`${doc.status} · ${doc.chunks||0} chunks${progress}${problem}`));
    const deleteDoc=actionButton(t('kbDelete'),()=>{
      if(window.confirm(t('kbRemoveDocConfirm')+'\n'+doc.filename))
        operate(()=>api(`kb/${encodeURIComponent(kb.id)}/documents/${encodeURIComponent(doc.id)}`,{method:'DELETE'}));
    });
    deleteDoc.classList.add('destructive-action');
    row.append(title,deleteDoc);docList.append(row);
  }
  // Kiwix is hosted by PHAROS: never require a local browser file picker
  // or an NFS/SMB mount on the user's desktop or phone.
  const addZimButton=actionButton(t('kbZimAdd'), async()=>{
    addZimButton.disabled=true;
    try{
      const connected=new Set(zims.map(z=>z.filename));
      const available=[];
      let start=0;
      for(let page=0;page<30;page++){
        const result=await api('extensions/kiwix/books?start='+start+'&count=100');
        const items=result.items||[];
        for(const item of items){
          if(item.filename && !connected.has(item.filename) &&
             !available.some(b=>b.filename===item.filename)){
            available.push(item);
          }
        }
        start+=items.length;
        if(!result.more || !items.length)break;
      }
      const old=card.querySelector('.kb-zim-chooser');
      if(old)old.remove();
      if(!available.length){feedback(t('kbZimNone'));return;}
      const chooser=make('div','kb-zim-chooser kb-upload-form');
      const select=document.createElement('select');
      select.setAttribute('aria-label',t('kbZimChoose'));
      for(const item of available){
        select.add(new Option(item.title && item.title!==item.filename ?
          item.title+' ('+item.filename+')' : item.filename,item.filename));
      }
      const confirm=actionButton(t('kbZimAddConfirm'),async()=>{
        confirm.disabled=true;
        await operate(()=>api('kb/'+encodeURIComponent(kb.id)+'/zim-sources',{
          method:'POST',body:JSON.stringify({filename:select.value})}));
      });
      chooser.append(select,confirm);
      addZimButton.after(chooser);
    }catch(e){feedback(t('kbZimLoadFail')+' '+e.message,'bad');}
    finally{addZimButton.disabled=false;}
  });
  const zimSection=make('section','kb-source-section');
  zimSection.append(make('h4','kb-docs-title',t('kbZimSources')),addZimButton,
    make('p','kb-muted',t('kbZimHint')));
  if(zims.length){
    const linked=make('div','kb-doc-list');
    for(const book of zims){
      const row=make('div','kb-document-row');
      const text=make('div','kb-document-info');
      text.append(make('strong','',book.filename));
      if(!book.available)text.append(make('small','kb-muted',t('kbZimMissing')));
      const remove=actionButton(t('kbZimUnlink'),()=>{
        if(window.confirm(t('kbZimUnlinkConfirm')+'\n'+book.filename))
          operate(()=>api(`kb/${encodeURIComponent(kb.id)}/zim-sources/${encodeURIComponent(book.filename)}`,{
            method:'DELETE'}));
      });
      remove.classList.add('destructive-action');
      row.append(text,remove);linked.append(row);
    }
    zimSection.append(linked);
  }
  const upload=make('div','kb-upload-form kb-auto-upload');
  const file=document.createElement('input');file.type='file';file.hidden=true;file.accept='.pdf,.txt,.md';
  file.setAttribute('aria-label',t('kbSelectFile'));
  const button=actionButton(t('kbUpload'),()=>file.click(),false);
  const progressNode=make('p','kb-ingest-progress');
  progressNode.hidden=true;progressNode.setAttribute('role','status');
  progressNode.setAttribute('aria-live','polite');
  upload.append(file,button,progressNode);
  file.addEventListener('change',()=>{
    if(file.files?.length)void uploadKBFile(kb.id,file,progressNode,button);
  });
  const uploadNotice=make('p','inline-feedback');
  uploadNotice.id='kb-upload-feedback-'+kb.id;
  uploadNotice.setAttribute('role','status');
  const notice=kbUploadNotices.get(kb.id);
  uploadNotice.hidden=!notice;
  if(notice){
    uploadNotice.textContent=notice.message;
    if(notice.kind)uploadNotice.classList.add(notice.kind);
  }
  card.append(upload,uploadNotice,docList,zimSection);host.append(card);
  if(activeKBUploads.has(kb.id)){
    const state=activeKBUploads.get(kb.id);
    Object.assign(state,{progressNode,fileField:file,button});syncKBUploadView(kb.id);
  }
}
el('refresh-kb').addEventListener('click',()=>{kbLoaded=false;loadKnowledge();});
el('chat-refresh-kb').addEventListener('click',()=>{kbLoaded=false;loadKnowledge();});
el('kb-create-form').addEventListener('submit',e=>{
  e.preventDefault();
  const name=el('kb-new-name').value.trim(),description=el('kb-new-desc').value.trim();
  if(!name)return;
  operate(async()=>{
    await api('kb',{method:'POST',body:JSON.stringify({name,description})});
    el('kb-new-name').value='';el('kb-new-desc').value='';
  });
});


let embeddingModelsPromise=null;
let embeddingLoadSequence=0;
let embeddingCatalogLastRefresh=0;
let embeddingApplyBusy=false;
let embeddingReindexActive=false;
const kbEmbeddingItems=new Map();
let kbInventoryMode='';
function updateEmbeddingButtons(){
 const select=el('kb-embedding-model');
 const activate=el('kb-apply-embedding');
 activate.textContent=t(kbModuleEnabled?'kbChangeModel':'kbEnableRAG');
 const unavailable=select.disabled || !select.value;
 el('kb-apply-embedding').disabled=unavailable || embeddingApplyBusy || embeddingReindexActive;
 const action=el('kb-npu-action');const item=kbEmbeddingItems.get(select.value);
 action.hidden=kbInventoryMode!=='tiiny_direct' || !item || typeof item.loaded!=='boolean';
 if(!action.hidden){action.textContent=t(item.loaded?'npuUnload':'npuLoad');action.disabled=embeddingApplyBusy || embeddingReindexActive;}
}
function embeddingCatalogText(catalog){
 if(catalog.catalog_complete)return ''; // No permanent explanatory notices on healthy services.
 if(catalog.mode==='tiiny_gateway' && catalog.catalog_reason==='gateway_models_unavailable')
  return t(catalog.catalog_error_code==='gateway_auth_failed'?'kbModelCatalogAuth':'kbModelCatalogUnavailable');
 if(catalog.mode==='tiiny_gateway' && catalog.catalog_reason==='gateway_catalog_not_exposed')
  return t('kbModelCatalogGatewayLimited');
 return t('kbModelCatalogLimited');
}
function embeddingItemLabel(item,current){
  return modelOptionLabel(item.id, item.id===current, item.loaded===true);
}
async function loadEmbeddingModels(refresh=false){
 if(embeddingModelsPromise && !refresh)return embeddingModelsPromise;
 const sequence=++embeddingLoadSequence;
 embeddingModelsPromise=(async()=>{
  const select=el('kb-embedding-model'),feedback=el('kb-embedding-feedback');
  const note=el('kb-embedding-catalog-note');
  const previous=refresh?select.value:'';
  select.replaceChildren();select.disabled=true;
  feedback.textContent=t('kbModelListing');note.textContent='';
  kbEmbeddingItems.clear();updateEmbeddingButtons();
  // Metadata and the embedding inventory are independent. Fetch together
  // and reuse the Core's short-lived shared Tiiny cache; force refresh only
  // when the user explicitly requests it.
  const [config,catalog]=await Promise.all([
    api('settings'),api('embedding-models'+(refresh?'?refresh=true':''))
  ]);
  const current=config.values.rag.embedding_model;
  kbInventoryMode=catalog.mode||'';
  if(sequence!==embeddingLoadSequence)return;
  embeddingCatalogLastRefresh=Date.now();
  const entries=Array.isArray(catalog.items)?catalog.items:
   (catalog.models||[]).map(model=>({id:model,loaded:null,downloaded:null,candidate_basis:'name'}));
  const unique=new Set();
  for(const item of entries){
   if(!item || typeof item.id!=='string' || !item.id || unique.has(item.id))continue;
   unique.add(item.id);kbEmbeddingItems.set(item.id,item);
   select.add(new Option(embeddingItemLabel(item,current),item.id));
  }
  note.textContent=embeddingCatalogText(catalog);
  const legend=el('kb-model-legend');
  legend.dataset.i18n=['tiiny_direct','tiiny_gateway'].includes(catalog.mode)?'kbModelLegend':'kbModelLegendGeneric';
  legend.textContent=t(legend.dataset.i18n);
  if(!select.options.length){
   select.add(new Option(t('kbModelNoneFound'),''));select.disabled=true;
   feedback.textContent=t('kbModelNoneFound');
  }else{
   select.disabled=false;
   select.value=unique.has(previous)?previous:unique.has(current)?current:select.options[0].value;
   feedback.textContent='';
  }
  updateEmbeddingButtons();
 })().catch(err=>{
  if(sequence===embeddingLoadSequence){
   el('kb-embedding-feedback').textContent=err.message;
   embeddingModelsPromise=null;
   updateEmbeddingButtons();
  }
 });
 return embeddingModelsPromise;
}
async function refreshReindex(){
 try{
  const result=await api('rag/reindex');const job=result.job;
  const panel=el('kb-reindex-progress');
  panel.hidden=!job;
  if(!job)return;
  const stateText=job.state==='running'?t('kbReindexRunning'):job.state==='complete'?t('kbReindexComplete'):job.state==='interrupted'?t('kbReindexInterrupted'):t('kbReindexFailed');
  el('kb-reindex-state').textContent=stateText;
  el('kb-reindex-bar').value=job.total?100*job.completed/job.total:job.state==='complete'?100:0;
  el('kb-reindex-detail').textContent=`${job.completed} / ${job.total} ${job.error||''}`;
  embeddingReindexActive=result.active;updateEmbeddingButtons();
  if(job.state==='complete'&&!window.pharosReindexCompleteHandled){
    window.pharosReindexCompleteHandled=true;
    await loadSettings();kbLoaded=false;loadKnowledge();loadEmbeddingModels(true);
  }
 }catch(error){el('kb-embedding-feedback').textContent=error.message;}
}
el('kb-refresh-embedding').addEventListener('click',()=>loadEmbeddingModels(true));
el('kb-npu-action').addEventListener('click',async()=>{
 const id=el('kb-embedding-model').value,item=kbEmbeddingItems.get(id);
 if(kbInventoryMode!=='tiiny_direct'||!item||typeof item.loaded!=='boolean')return;
 if(item.loaded&&!confirm(t('npuConfirmUnload')))return;
 const btn=el('kb-npu-action');btn.disabled=true;
 try{await api('ai/models/'+(item.loaded?'stop':'launch'),{method:'POST',body:JSON.stringify({model:id})});await loadEmbeddingModels(true);}
 catch(error){el('kb-embedding-feedback').textContent=error.message;}
 finally{btn.disabled=false;}
});
el('kb-embedding-model').addEventListener('change',()=>{
 el('kb-embedding-feedback').textContent='';
 updateEmbeddingButtons();
});
el('kb-apply-embedding').addEventListener('click',async()=>{
 if(embeddingApplyBusy || embeddingReindexActive)return;
 const model=el('kb-embedding-model').value;const feedback=el('kb-embedding-feedback');
 if(!model)return;
 try{
  const settings=await api('settings');
  if(kbModuleEnabled && model===settings.values.rag.embedding_model && !kbCache.some(kb=>kb.indexed_documents>0 && kb.embedding_model!==model)){
   feedback.textContent=t('kbModelSelected');return;
  }
  // On a fresh installation there are no active indexes to reindex.
  const firstActivation=!kbModuleEnabled;
  if(!window.confirm(t(firstActivation?'kbEnableConfirm':'kbModelConfirm')))return;
  embeddingApplyBusy=true;updateEmbeddingButtons();
  feedback.textContent=t('kbModelVerifying');
  if(firstActivation){
    await api('rag/activate',{method:'POST',body:JSON.stringify({model,revision:settings.revision})});
    window.pharosSetKnowledgeModuleEnabled(true);
    kbLoaded=false;await loadKnowledge();await loadSettings();
    feedback.textContent=t('kbEnabled');
  }else{
    await api('rag/reindex',{method:'POST',body:JSON.stringify({model,revision:settings.revision})});
    window.pharosReindexCompleteHandled=false;
    feedback.textContent='';await refreshReindex();
  }
 }catch(error){feedback.textContent=error.message;}
 finally{embeddingApplyBusy=false;updateEmbeddingButtons();}
});
window.pharosLoadEmbeddingModels=loadEmbeddingModels;
const previousLoadKnowledge=window.pharosLoadKnowledge;
window.pharosLoadKnowledge=async()=>{
  // KB inventory discovery runs alongside document management requests.
  // Neither should block the other when Tiiny performs slow model probing.
  if(window.location.hash==='#knowledge'){
    if(!embeddingModelsPromise)void loadEmbeddingModels();
    else if(embeddingCatalogLastRefresh &&
            Date.now()-embeddingCatalogLastRefresh > 90000)
      void loadEmbeddingModels(true);
    void refreshReindex();
  }
  await previousLoadKnowledge();
};
setInterval(()=>{if(window.location.hash==='#knowledge')refreshReindex();},2500);






