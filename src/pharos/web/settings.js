'use strict';


let settingsSnapshot=null;
let settingsModelRequest=0;
let settingsEmbeddingModelRequest=0;
let settingsLoadGeneration=0;
const sectionNames={general:'settingsFieldGeneral',server:'settingsFieldServer',security:'settingsFieldSecurity',storage:'settingsFieldStorage',ai:'settingsFieldAI',rag:'settingsFieldRAG',kiwix:'settingsFieldKiwix'};

function addNonSelectableOption(select,value,reason){
  const option=new Option(reason+' · '+value,value);
  option.disabled=true;
  select.add(option);
  select.value=value;
  select.dataset.skip='1';
}
function settingField(section,key,value){
  const wrapper=document.createElement('div');wrapper.className='settings-field';
  // Size layout cells by their actual controls. Long numeric labels may use
  // more width than their deliberately compact input boxes.
  const kind=typeof value==='boolean'?'toggle'
    :typeof value==='number'?'number'
    :((section==='ai'&&key==='default_model')||(section==='rag'&&key==='embedding_model'))?'model'
    :section==='ai'&&['provider','npu_policy'].includes(key)?'select'
    :section==='general'&&key==='language'?'short'
    :/(?:url|_dir|_file|^host$|^marker_id$)/.test(key)?'path':'text';
  wrapper.classList.add('settings-field--'+kind);
  const label=document.createElement('label');label.textContent=t('settingsLabel_'+key);
  const dropdown=(section==='general'&&key==='language')||(section==='ai'&&['provider','npu_policy','default_model'].includes(key))||(section==='rag'&&key==='embedding_model');
  const control=document.createElement(dropdown?'select':'input');
  control.id='setting-'+section+'-'+key;label.htmlFor=control.id;
  control.dataset.section=section;control.dataset.key=key;
  if(section==='general'&&key==='language'){
    const options=settingsSnapshot.language_options||[];
    for(const item of options)control.add(new Option(item.name,item.code));
    if(options.some(item=>item.code===value))control.value=value;
    else addNonSelectableOption(control,value,t('translationsLoadError'));
  }else if(section==='ai'&&key==='provider'){
    const options=settingsSnapshot.provider_options||[];
    for(const item of options)control.add(new Option(item.label,item.id));
    if(options.some(item=>item.id===value))control.value=value;
    else addNonSelectableOption(control,value,t('settingsProviderNotAvailable'));
    if(!options.length)control.disabled=true;
  }else if(section==='ai'&&key==='npu_policy'){
    for(const item of [['manual','Manual'],['protected','Automatico protetto / Protected'],['managed','Automatico gestito / Managed (no auto-eviction yet)']]) control.add(new Option(item[1],item[0]));
    control.value=value;
  }else if((section==='ai'&&key==='default_model')||(section==='rag'&&key==='embedding_model')){
    control.add(new Option(t('settingsModelRefreshing'),''));
    control.disabled=true;control.dataset.skip='1';
  }else if(typeof value==='boolean'){
    control.type='checkbox';control.checked=value;
  }else{
    control.type=typeof value==='number'?'number':'text';control.value=String(value);
    if(typeof value==='number')control.step='1';
  }
  if(section==='storage'&&key==='marker_id'){
    control.readOnly=true;control.title='Volume identity is changed using the CLI only';
  }
  wrapper.append(label,control);
  if(section==='ai'&&key==='default_model'){
    const refresh=document.createElement('button');refresh.type='button';refresh.className='outline-btn settings-model-refresh';
    refresh.textContent=t('settingsModelsRefresh');refresh.addEventListener('click',()=>loadSettingsModels(true));
    wrapper.append(refresh);
    const help=document.createElement('small');help.className='settings-model-hint';
    help.textContent=t('settingsDefaultModelHint');wrapper.append(help);
  }
  if(section==='rag'&&key==='embedding_model'){
    const active=Boolean(settingsSnapshot.module_runtime_active?.knowledge);
    if(active){
      const help=document.createElement('small');help.className='settings-model-hint';
      help.textContent=t('settingsKBModelActiveHint');wrapper.append(help);
      const go=document.createElement('button');go.type='button';
      go.className='outline-btn settings-model-refresh';
      go.textContent=t('settingsKBOpen');
      go.addEventListener('click',()=>showView('knowledge'));wrapper.append(go);
    }else{
      const refresh=document.createElement('button');refresh.type='button';
      refresh.className='outline-btn settings-model-refresh';
      refresh.textContent=t('settingsModelsRefresh');
      refresh.addEventListener('click',()=>loadSettingsEmbeddingModels(true));
      wrapper.append(refresh);
      const help=document.createElement('small');help.className='settings-model-hint';
      help.textContent=t('settingsKBModelHint');wrapper.append(help);
    }
  }
  return wrapper;
}
// Credential editors are part of their proper fieldsets. Their write-only endpoints
// stay independent of TOML settings; we never nest an HTML form in #settings-form.
function createCredentialEditor(kind){
  const isAI=kind==='ai';
  const wrap=document.createElement('div');wrap.className='settings-credential-editor';
  const status=document.createElement('div');status.className='config-row';
  const statusLabel=document.createElement('span');statusLabel.textContent=t('apiKeyStatus');
  const statusValue=document.createElement('strong');statusValue.id=isAI?'config-api-key':'config-api-key';
  statusValue.textContent=t(settingsSnapshot.api_key_configured?'configured':'notConfigured');
  status.append(statusLabel,statusValue);
  const label=document.createElement('label');label.htmlFor=isAI?'settings-api-key':'settings-api-key';
  label.textContent=t('settingsNewAPI');
  const row=document.createElement('div');row.className='settings-inline';
  const field=document.createElement('input');field.type='password';field.id=label.htmlFor;
  field.autocomplete='new-password';field.maxLength=2048;
  
  const save=document.createElement('button');save.type='button';save.className='outline-btn';save.textContent=t('settingsReplace');
  const feedback=document.createElement('p');feedback.className='inline-feedback';feedback.id=isAI?'settings-api-feedback':'settings-secret-feedback';
  feedback.setAttribute('role','status');
  const submit=async()=>{
    const routeEdited=el('setting-ai-provider')?.value!==settingsSnapshot.values.ai.provider || el('setting-ai-base_url')?.value!==settingsSnapshot.values.ai.base_url;
    if(routeEdited){feedback.textContent=t('settingsKeyWithRoute');return;}
    if(!field.value){
      feedback.textContent=isAI?t('settingsAPIRequired'):t('settingsPasswordTooShort');return;
    }
    save.disabled=true;feedback.textContent='…';
    try{
      await api('settings/api-key',{method:'PUT',body:JSON.stringify({api_key:field.value})});
      field.value='';
      feedback.textContent=t('settingsSecretSaved');
      settingsSnapshot.api_key_configured=true;statusValue.textContent=t('configured');
    }catch(error){feedback.textContent=error.message;}
    finally{save.disabled=false;}
  };
  save.addEventListener('click',submit);
  field.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();submit();}});
  row.append(field,save);wrap.append(status,label,row,feedback);
  return wrap;
}
function renderSettings(){
  if(!settingsSnapshot)return;
  const fields=el('settings-fields');fields.replaceChildren();
  for(const [section,properties] of Object.entries(settingsSnapshot.values)){
    const fieldset=document.createElement('fieldset');const legend=document.createElement('legend');
    legend.textContent=t(sectionNames[section]||section);fieldset.append(legend);
    const grid=document.createElement('div');grid.className='settings-form-grid';
    for(const [key,value] of Object.entries(properties)){
      if(section==='ai'&&key==='management_url')continue; // Deprecated: gateway must never contact TiinyOS directly.
      grid.append(settingField(section,key,value));
    }
    fieldset.append(grid);
    if(section==='ai')fieldset.append(createCredentialEditor(section));
    fields.append(fieldset);
  }
}
async function loadSettingsModels(refresh=false){
  const select=el('setting-ai-default_model');
  if(!select||!settingsSnapshot)return;
  const request=++settingsModelRequest;
  const original=settingsSnapshot.values.ai.default_model;
  const earlier=select.dataset.skip==='1'?'':select.value;
  select.replaceChildren(new Option(t('settingsModelRefreshing'),''));select.disabled=true;select.dataset.skip='1';
  let names=[];
  let residency=new Map();
  // The Tiiny catalog contains installed models, including those not yet
  // loaded in NPU. /api/models alone may list only currently loaded models.
  try{
    const catalog=await api('ai/catalog'+(refresh?'?refresh=true':''));
    for(const item of catalog.models||[]){
      if(!item || typeof item.id!=='string' || !item.id)continue;
      if(typeof isChatCandidate==='function' && !isChatCandidate(item))continue;
      residency.set(item.id,item.loaded===true);
      names.push(item.id);
    }
  }catch(error){console.warn('AI catalog unavailable',error.message);}
  if(!names.length){
    try{
      const result=await api('models');
      names=(result.models||[]).filter(item=>typeof item==='string'&&item);
    }catch(error){console.warn('Chat model inventory unavailable',error.message);}
  }
  names=[...new Set(names)];
  if(request!==settingsModelRequest || el('setting-ai-default_model')!==select)return;
  select.replaceChildren();
  if(!names.length){
    addNonSelectableOption(select,original||'',t('settingsNoModels'));select.disabled=true;
    return;
  }
  select.add(new Option(t('settingsModelChoose'),''));
  for(const name of names){
    const suffix=residency.has(name)?residency.get(name)?' ᴺᴾᵁ':'': '';
    select.add(new Option(name+suffix,name));
  }
  const desired=names.includes(earlier)?earlier:original;
  if(desired&&!names.includes(desired))addNonSelectableOption(select,desired,t('settingsModelNotAvailable'));
  else{
    select.value=desired&&names.includes(desired)?desired:'';
    select.dataset.skip=select.value?'0':'1';
  }
  select.disabled=false;
  select.addEventListener('change',()=>{select.dataset.skip='0';},{once:false});
}
async function loadSettingsEmbeddingModels(refresh=false){
  const select=el('setting-rag-embedding_model');
  if(!select||!settingsSnapshot)return;
  const request=++settingsEmbeddingModelRequest;
  const active=Boolean(settingsSnapshot.module_runtime_active?.knowledge);
  const configured=settingsSnapshot.values.rag.embedding_model;
  if(active){
    // Changing an active embedding model requires KB confirmation and shadow
    // reindexing. Never submit an unverified change from ordinary Settings.
    select.replaceChildren(new Option(configured||t('settingsKBModelNotSet'),configured||''));
    select.value=configured||'';select.disabled=true;select.dataset.skip='1';
    return;
  }
  const earlier=select.dataset.skip==='1'?'':select.value;
  select.replaceChildren(new Option(t('settingsModelRefreshing'),''));select.disabled=true;
  select.dataset.skip='1';
  let candidates=[];
  try{
    const data=await api('embedding-models'+(refresh?'?refresh=true':''));
    candidates=(data.items||[]).map(item=>item&&item.id).filter(id=>typeof id==='string'&&id);
    if(!candidates.length&&Array.isArray(data.models))
      candidates=data.models.filter(id=>typeof id==='string'&&id);
  }catch(error){console.warn('Embedding catalog unavailable',error.message);}
  if(request!==settingsEmbeddingModelRequest || el('setting-rag-embedding_model')!==select)return;
  candidates=[...new Set(candidates)];
  select.replaceChildren();
  if(!candidates.length){
    addNonSelectableOption(select,configured||'',t('settingsNoModels'));
    select.disabled=true;return;
  }
  select.add(new Option(t('settingsModelChoose'),''));
  for(const name of candidates)select.add(new Option(name,name));
  const desired=candidates.includes(earlier)?earlier:configured;
  if(desired&&!candidates.includes(desired))
    addNonSelectableOption(select,desired,t('settingsModelNotAvailable'));
  else{
    select.value=desired&&candidates.includes(desired)?desired:'';
    select.dataset.skip=select.value?'0':'1';
  }
  select.disabled=false;
  select.addEventListener('change',()=>{select.dataset.skip='0';});
}

async function loadSettings(preserveNotice=false){
  const generation=++settingsLoadGeneration;
  const feedback=el('settings-feedback');
  if(!settingsSnapshot)feedback.textContent=t('settingsLoading');
  // GET only: never retry writes to the TOML or credential endpoints.
  for(let attempt=0;attempt<4;attempt++){
    if(generation!==settingsLoadGeneration)return;
    try{
      const result=await api('settings');
      if(generation!==settingsLoadGeneration)return;
      settingsSnapshot=result;renderSettings();
      // The field is now visible; asynchronously load an inventory suitable
      // for both installed Tiiny models and generic OpenAI-compatible providers.
      void loadSettingsModels();
      void loadSettingsEmbeddingModels();
      if(!preserveNotice){
        feedback.textContent=result.restart_required
          ? t('settingsPendingRestart')+(result.restart_fields||[]).join(', ') : '';
      }
      return;
    }catch(error){
      if(generation!==settingsLoadGeneration)return;
      // Network errors from fetch(), not HTTP errors (422/403/etc).
      const transient=error instanceof TypeError;
      if(!transient||attempt===3){
        feedback.textContent=transient?t('settingsLoadUnavailable'):error.message;
        return;
      }
      feedback.textContent=t('settingsRetrying');
      await new Promise(resolve=>setTimeout(resolve,[350,700,1300][attempt]));
    }
  }
}
window.pharosLoadSettings=loadSettings;
el('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!settingsSnapshot)return;
  const values=structuredClone(settingsSnapshot.values);
  for(const control of el('settings-fields').querySelectorAll('[data-section]')){
    const section=control.dataset.section,key=control.dataset.key;
    if(control.readOnly || control.disabled || control.dataset.skip==='1')continue;
    const original=values[section][key];
    values[section][key]=typeof original==='boolean'?control.checked:typeof original==='number'?Number(control.value):control.value;
  }
  const feedback=el('settings-feedback');feedback.textContent='…';
  try{
    const routeChanged=values.ai.provider!==settingsSnapshot.values.ai.provider || values.ai.base_url!==settingsSnapshot.values.ai.base_url;
    const key=el('settings-api-key')?.value||'';
    if(routeChanged&&!key&&settingsSnapshot.api_key_configured&&!window.confirm(t('settingsRouteClearsKey'))){feedback.textContent='';return;}
    const body={values,revision:settingsSnapshot.revision};
    if(key||routeChanged)body.api_key=key;
    const result=await api('settings',{method:'PUT',body:JSON.stringify(body)});
    if(result.language){
      appConfig.language=result.language;
      await window.pharosLoadLocaleCatalog();
      await window.pharosUseLanguage(result.language,true);
      renderConfig();
      // Settings controls and legends are generated dynamically, so updating
      // data-i18n nodes alone does not translate the open Settings view.
      // The save has completed: rebuild from the saved snapshot, then reload
      // authoritative server values below without navigating away.
      renderSettings();
    }
    feedback.textContent=result.restart_required
      ? t('settingsSaved')+(result.restart_fields||[]).join(', ')
      : t('settingsSavedNoRestart');
    if(appConfig?.ai){
      appConfig.ai.default_model=values.ai.default_model;
      // After saving, apply the configured model immediately in Chat.
      if(typeof loadModels==='function')await loadModels(true);
    }
    await loadSettings(true);
  }catch(error){feedback.textContent=error.message;}
});
