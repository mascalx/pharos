'use strict';
const IT = {};

const EN = {};


let lang = 'it';
let appConfig = null;
let statusData = null;
let chatHistory = [];
let busy = false;
const el = id => document.getElementById(id);


const localeCache = new Map();
let activeMessages = {};
let localeNames = new Map([['it','Italiano'],['en','English']]);
const t = key => activeMessages[key] ?? EN[key] ?? IT[key] ?? key;
window.pharosLanguageLabel = code => localeNames.get(code) || code;
async function loadLanguageBundle(code,force=false){
  if(!force&&localeCache.has(code))return localeCache.get(code);
  const data=await api('locales/'+encodeURIComponent(code));
  if(!data||!data.messages||typeof data.messages!=='object')throw Error('invalid_locale_bundle');
  localeCache.set(code,data.messages);
  localeNames.set(code,data.name);
  if(code==='en')Object.assign(EN,data.messages);
  if(code==='it')Object.assign(IT,data.messages);
  return data.messages;
}
window.pharosUseLanguage=async function(code,force=false){
  try{
    await loadLanguageBundle(code,force);
    setLanguage(code);
  }catch(error){
    // Custom locale may be unavailable when NFS is temporarily offline.
    // A language pack failure must not prevent Chat, Kiwix or the dashboard.
    console.warn('Custom translation unavailable; using English',error.message);
    setLanguage('en');
  }
};
window.pharosLoadLocaleCatalog=async function(){
  const result=await api('locales');
  localeNames=new Map((result.items||[]).map(item=>[item.code,item.name]));
  return result.items||[];
};


function setLanguage(value) {
  lang = localeCache.has(value) ? value : 'en';
  activeMessages=localeCache.get(lang)||EN;
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(node => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-i18n-aria]').forEach(node => { node.setAttribute('aria-label', t(node.dataset.i18nAria)); node.title = t(node.dataset.i18nAria); });
  const active = document.querySelector('.view.active');
  el('page-title').textContent = t(active?.id?.replace('view-','') || 'home');
  renderStatus(); renderChat(); updateChatModelLabels(); if (window.pharosRefreshSettingsLabels) window.pharosRefreshSettingsLabels(); if(window.pharosRenderUpdateStatus)window.pharosRenderUpdateStatus();
  if(window.pharosRefreshJobTranslations)window.pharosRefreshJobTranslations();
  if(window.pharosRefreshMapsLabels)window.pharosRefreshMapsLabels();
  if(window.pharosWaypointRefreshLanguage)window.pharosWaypointRefreshLanguage();
  if(window.pharosGpxRefreshLanguage)window.pharosGpxRefreshLanguage();
}
function apiErrorDetail(detail) {
  if (typeof detail === 'string' && detail) return detail;
  // FastAPI validation errors are arrays of {loc,msg,type}; avoid [object Object].
  if (Array.isArray(detail)) return detail.slice(0,3).map(item => {
    if (!item || typeof item !== 'object') return String(item);
    const loc=Array.isArray(item.loc) ? item.loc.filter(x=>x!=='body').join('.') : '';
    return (loc ? loc + ': ' : '') + String(item.msg || item.type || 'invalid_request');
  }).join('; ');
  if (detail && typeof detail === 'object')
    return String(detail.message || detail.error || detail.code || 'invalid_request');
  return '';
}
async function api(path, options = {}) {
  const response = await fetch('/api/' + path, {credentials:'same-origin', cache:'no-store', ...options,
    headers:{...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...(options.method && options.method !== 'GET' ? {'X-Pharos-Request':'1'} : {}), ...options.headers}});
  if (!response.ok) {
    let reason='http_'+response.status;
    try {const data=await response.json();const detail=apiErrorDetail(data.detail);if(detail)reason+=': '+detail;}
    catch (_) { /* non JSON: retain status */ }
    throw new Error(reason.slice(0,400));
  }
  return response.json();
}
function showView(view) {
  // Atlas is a separate interactive overlay; leave it cleanly when using
  // the persistent PHAROS logo or notification navigation.
  if(window.pharosCloseMap)window.pharosCloseMap();
  if(window.pharosCloseKiwix)window.pharosCloseKiwix();
  if(window.pharosCloseKBDocument)window.pharosCloseKBDocument();
  const allowed = ['home','chat','knowledge','kiwix','diagnostics','settings','notifications','translations','maps'];
  const chosen = allowed.includes(view) ? view : 'home';
  const selected = chosen;
  document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === 'view-' + selected));
  el('page-title').textContent = t(selected);
  document.body.classList.toggle('showing-home', selected === 'home');
  document.title = 'PHAROS · ' + t(selected);
  if (window.location.hash !== '#' + selected) window.location.hash = selected;
  window.scrollTo(0,0);
  if ((selected === 'knowledge' || selected === 'chat') && window.pharosLoadKnowledge) window.pharosLoadKnowledge();
  // Refresh stale model choices only when opening Chat, without delaying
  // navigation or wiping out a deliberate selection while in use.
  if(selected==='chat' && chatCatalogLastRefresh > 0 &&
     Date.now()-chatCatalogLastRefresh > 90000)
    void loadModels().catch(error=>console.warn('Chat refresh unavailable',error));
  if (selected === 'settings' && window.pharosLoadSettings) window.pharosLoadSettings();
  if (selected === 'translations' && window.pharosLoadTranslationEditor) window.pharosLoadTranslationEditor();
  if (selected === 'kiwix' && window.pharosLoadKiwix)window.pharosLoadKiwix();
  if (selected === 'maps' && window.pharosLoadMaps)window.pharosLoadMaps();
  if (['knowledge','kiwix','maps','notifications'].includes(selected) && window.pharosLoadJobs)window.pharosLoadJobs();
  if (selected === 'diagnostics') {refreshStatus();if(window.pharosLoadUpdates)window.pharosLoadUpdates(false,true);}
}
function showState(id, text, cls) { const node=el(id); node.textContent=text; node.classList.remove('ok','bad','warn'); if (cls) node.classList.add(cls); }
function renderStatus() {
  if (!statusData) return;
  const goodStorage = statusData.storage?.available;
  showState('health-storage',t(goodStorage?'available':'unavailable'),goodStorage?'ok':'bad');
  const ai=statusData.ai || 'unavailable';
  const aiKey=ai === 'available' ? 'available' : ai === 'reachable' ? 'aiReachable' :
    ai === 'disabled' ? 'disabled' : ai === 'unknown' ? 'aiUnverified' : 'unavailable';
  showState('health-ai',t(aiKey),['available','reachable'].includes(aiKey)?'ok':
    aiKey === 'unavailable'?'bad':'warn');
  showState('health-db',t(statusData.database === 'available'?'available':'unavailable'),statusData.database === 'available'?'ok':'bad');
  const storageNote = goodStorage ? '' : t('storageFailNote');
  const gatewayNote = statusData.ai_reason === 'gateway_auth_failed' ? t('authError') :
    statusData.ai_reason === 'ai_endpoint_reachable' ? t('aiReachableNote') : '';
  el('health-note').textContent = [storageNote, gatewayNote].filter(Boolean).join(' ');
}
async function refreshStatus() {
  try { statusData = await api('status'); renderStatus(); }
  catch (error) { el('health-note').textContent=t('refreshFail'); console.warn('Status check failed',error.message); }
}
function renderConfig() {
  if (!appConfig) return;
  const apiKeyStatus=el('config-api-key');
  if(apiKeyStatus)apiKeyStatus.textContent=t(appConfig.ai.api_key_configured ? 'configured' : 'notConfigured');
  // Storage paths are displayed by the settings editor.





  el('sidebar-version').textContent=appConfig.version;
}
// Native select options use Unicode superscripts: HTML <sup> does not work in <option>.
const chatModelResidency = new Map();
let chatInventoryMode='';
function modelOptionLabel(id, isCurrent, loaded) {
  return id + (isCurrent ? ' *' : '') + (loaded === true ? ' ᴺᴾᵁ' : '');
}
function isChatCandidate(item) {
  const kind = String(item.kind || '').toLowerCase();
  const caps = Array.isArray(item.capabilities) ? item.capabilities.map(x => String(x).toLowerCase()) : [];
  // Only exclude known embedding-/rerank-only classes, not models with unknown type.
  if (caps.some(x => /chat|text.generation|completion/.test(x))) return true;
  if (/(embedding|rerank|ocr|speech|image generation|text.to.image|music)/.test(kind)) return false;
  if (caps.some(x => /embed|rerank/.test(x))) return false;
  return !/(?:embedding|embed-text|bge-|nomic-embed|rerank)/i.test(item.id);
}
function updateChatThinking() {
  const model=el('model-select').value.split('/').pop();
  // OpenAI-compatible providers do not expose reliable capability metadata.
  // Offer an explicit opt-in to every chat model; unsupported upstreams fail
  // clearly instead of silently dropping the selection.
  const supported=!!model && !/(embedding|embed-text|rerank|^bge-|^nomic-embed|asr|whisper|tts|image|ocr|music)/i.test(model);
  el('chat-thinking-label').hidden=!supported;el('chat-thinking').hidden=!supported;
  el('chat-thinking').title=t('chatThinkingBackendNote');
  if(!supported)el('chat-thinking').value='auto';
}
function updateChatModelLabels() {
  const select = el('model-select');
  if (!select) return;
  for (const option of select.options) {
    if (!option.value) continue;
    option.textContent = modelOptionLabel(option.value, option.value === select.value,
                                        chatModelResidency.get(option.value));
  }
  const button=el('chat-npu-action');
  const residency=chatModelResidency.get(select.value);
  button.hidden=chatInventoryMode!=='tiiny_direct' || typeof residency!=='boolean' || !select.value;
  if(!button.hidden){button.textContent=t(residency?'npuUnload':'npuLoad');}
  updateChatThinking();
  // No message may be sent before an explicit or configured model is selected.
  el('send-button').disabled=busy || !select.value;
}
let chatCatalogRequest=0;
let chatCatalogLastRefresh=0;
function showConfiguredChatModelImmediately(){
  const selected=appConfig?.ai?.default_model;
  if(!selected)return;
  const select=el('model-select');
  select.replaceChildren(new Option(selected,selected));
  select.disabled=false;
  select.value=selected;
  // The model was explicitly configured by the administrator. Loading the
  // catalog is still needed to validate residency and update the options,
  // but must not block first use of Chat.
  el('chat-feedback').textContent='';
  updateChatModelLabels();
}
async function loadModels(preferDefault=false,refresh=false) {
  const sequence=++chatCatalogRequest;
  const select = el('model-select');
  let models=[];
  let mode='';
  const nextResidency=new Map();
  // Tiiny management inventory includes downloaded models not yet in NPU.
  // All provider calls use the Core endpoint: the Gateway is never bypassed.
  try {
    const catalog=await api('ai/catalog'+(refresh?'?refresh=true':''));
    mode=catalog.mode||'';
    for(const item of catalog.models||[]){
      if(!item || typeof item.id!=='string' || !item.id)continue;
      nextResidency.set(item.id,item.loaded===true);
      if(catalog.complete && isChatCandidate(item))models.push(item.id);
    }
  }catch(error){ console.warn('AI inventory unavailable',error.message); }
  if(!models.length){
    try { const result=await api('models'); models=Array.isArray(result.models)?result.models:[]; }
    catch(error){ el('chat-feedback').textContent=t(error.message==='gateway_auth_failed'?'authError':'gatewayError'); }
  }
  if(sequence!==chatCatalogRequest)return;
  chatCatalogLastRefresh=Date.now();
  chatInventoryMode=mode;
  chatModelResidency.clear();
  for(const [id,loaded] of nextResidency)chatModelResidency.set(id,loaded);
  models=[...new Set(models.filter(x=>typeof x==='string' && x))];
  const defaultModel=appConfig?.ai?.default_model;
  const liveChoice=select.value;
  select.replaceChildren();
  if(!models.length){
    const option=new Option(t('noModels'),'');option.dataset.i18n='noModels';select.add(option);select.disabled=true;
  }else{
    models.forEach(model=>select.add(new Option(model,model)));select.disabled=false;
    // On first page load, use the configured default, not the first item
    // returned by Tiiny. During a manual refresh keep the current selection.
    // An unavailable default must be explicit, not silently substituted.
    select.add(new Option(t('chatChooseModel'),''),0);
    select.value=(!preferDefault && models.includes(liveChoice))?liveChoice:
      models.includes(defaultModel)?defaultModel:'';
    if(defaultModel && !models.includes(defaultModel)){
      el('chat-feedback').textContent=t('chatDefaultUnavailable').replace('{model}',defaultModel);
    }else if(select.value){
      el('chat-feedback').textContent='';
    }
    updateChatModelLabels();
  }
  const legend=el('chat-model-legend');
  legend.dataset.i18n=['tiiny_direct','tiiny_gateway'].includes(mode)?'modelLegend':'modelLegendGeneric';
  legend.textContent=t(legend.dataset.i18n);
  el('send-button').disabled=busy || !select.value;
  updateChatThinking();
}
function groupChatSources(sources){
  const docs=new Map();
  for(const source of sources){
    if(!source || typeof source.kb_id!=='string' || !source.kb_id ||
       typeof source.document_id!=='string' || !source.document_id)continue;
    const article=source.source_type==='zim' && typeof source.url==='string' &&
      /^\/kiwix\/content\/[A-Za-z0-9_.+\-]+\/.+/.test(source.url) ? source.url : '';
    const key=JSON.stringify([source.kb_id,source.document_id,article]);
    if(!docs.has(key))docs.set(key,{kb_id:source.kb_id,document_id:source.document_id,
      filename:article?`${source.title || source.filename} · ${source.filename}`:
        (source.filename || source.document_id),url:article,pages:new Set()});
    const page=Number(source.page);
    if(Number.isSafeInteger(page) && page>0)docs.get(key).pages.add(page);
  }
  return [...docs.values()].map(item=>({...item,pages:[...item.pages].sort((a,b)=>a-b)}));
}
function renderChat() {
  const log=el('chat-log'); log.replaceChildren();
  if (!chatHistory.length) {
    const wrap=document.createElement('div'); wrap.className='chat-empty';
    const glyph=document.createElement('div'); glyph.className='empty-icon'; glyph.textContent='✦';
    const title=document.createElement('strong'); title.textContent=t('chatEmptyTitle');
    const para=document.createElement('p'); para.textContent=t('chatEmptyBody');
    wrap.append(glyph,title,para); log.append(wrap);
  } else {
    for (const message of chatHistory) {
      const container=document.createElement('div');container.className='message '+message.role;
      const role=document.createElement('span');role.className='message-role';role.textContent=t(message.role==='user'?'you':'assistant');
      const formatted=message.role==='assistant' && typeof window.pharosRenderMarkdown==='function';
      const body=document.createElement(formatted?'div':'span');body.className='message-text';
      if(formatted){
        body.classList.add('message-markdown');
        try{window.pharosRenderMarkdown(body,message.content);}
        catch(error){body.textContent=message.content;console.warn('Markdown rendering failed');}
      }else body.textContent=message.content;
      container.append(role,body);
      if (message.role==='assistant' && message.citations_missing) {
        container.append(Object.assign(document.createElement('small'), {
          className:'kb-muted', textContent:t('kbUncitedWarning')
        }));
      }
      if (message.role==='assistant' && Array.isArray(message.sources) && message.sources.length) {
        const sources=document.createElement('div');sources.className='chat-citations';
        const heading=document.createElement('strong');heading.textContent=t('kbSources');sources.append(heading);
        for (const source of groupChatSources(message.sources)) {
          const row=document.createElement('div');row.className='chat-citation-document';
          const link=document.createElement('a');
          link.href=source.url || `/api/kb/${encodeURIComponent(source.kb_id)}/documents/${encodeURIComponent(source.document_id)}/file`;
          if(source.url){
            // ZIM references use the same sandboxed reader as the Kiwix
            // library. Keep the ordinary link as a fallback.
            link.addEventListener('click',event=>{
              if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;
              if(window.pharosOpenKiwix?.(source.url))event.preventDefault();
            });
          }else{
            // Ordinary KB documents use the same internal reader from Chat.
            link.addEventListener('click',event=>{
              if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;
              if(window.pharosOpenKBDocument){
                event.preventDefault();
                void window.pharosOpenKBDocument(link.href,source.filename);
              }
            });
            link.target='_blank';link.rel='noopener noreferrer';
          }
          link.textContent=source.filename;
          link.title=t('kbSourceOpen');row.append(link);
          if(source.pages.length){
            const pages=document.createElement('span');pages.textContent=' ['+source.pages.join(', ')+']';
            row.append(pages);
          }
          sources.append(row);
        }
        container.append(sources);
      }
      if(message.role==='assistant' && (message.geo_query || (Array.isArray(message.geo_results)&&message.geo_results.length))){
        const results=document.createElement('div');results.className='chat-geo-results';
        if(message.geo_query){
          const interpreted=document.createElement('small');interpreted.className='chat-geo-query';
          interpreted.textContent=t('chatGeoInterpreted')+': '+message.geo_query;
          results.append(interpreted);
        }
        for(const item of (message.geo_results||[])){
          const row=document.createElement('div');row.className='chat-geo-result';
          const info=document.createElement('div');info.className='chat-geo-info';
          const title=document.createElement('strong');title.textContent=String(item.name||'');
          const desc=document.createElement('small');
          const key={place:'mapsSearchPlace',address:'mapsSearchAddress',
                     street:'mapsSearchStreet',poi:'mapsSearchPoi'}[item.category]||'mapsSearchPoi';
          desc.textContent=t(key)+(item.detail?' · '+item.detail:'')+
            (item.locality_hint?' · '+t('mapsSearchNearby').replace('{name}',item.locality_hint):'');
          info.append(title,desc);
          if(item.approximate_street&&item.requested_number){
            const note=document.createElement('small');note.className='chat-geo-caution';
            note.textContent=t('mapsSearchApproximateNumber')
              .replace('{number}',String(item.requested_number));
            info.append(note);
          }
          const button=document.createElement('button');button.type='button';
          button.className='outline-btn';button.textContent=t('mapsSearchShow');
          button.addEventListener('click',async()=>{
            button.disabled=true;
            try{
              const ok=await window.pharosOpenMapForPoint?.(Number(item.latitude),Number(item.longitude));
              if(!ok)el('chat-feedback').textContent=t('mapsSearchNoMap');
            }catch(error){el('chat-feedback').textContent=t('mapsSearchOpenFailed')+': '+error.message;}
            finally{button.disabled=false;}
          });
          row.append(info,button);results.append(row);
        }
        container.append(results);
      }
      if(message.role==='assistant' && message.geo_diagnostics && Object.keys(message.geo_diagnostics).length){
        const report=document.createElement('button');report.type='button';report.className='outline-btn';
        report.textContent=t('chatGeoExportReport');
        report.addEventListener('click',()=>{
          const blob=new Blob([JSON.stringify({diagnostics:message.geo_diagnostics,geo_status:message.geo_status,geo_query:message.geo_query,geo_results:message.geo_results},null,2)],{type:'application/json'});
          const url=URL.createObjectURL(blob);const link=document.createElement('a');
          link.href=url;link.download='pharos-geo-report-'+Date.now()+'.json';
          document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        });container.append(report);
      }
      if(message.role==='assistant' && message.geo_status==='needs_reindex'){
        const maps=document.createElement('button');
        maps.type='button';maps.className='outline-btn';
        maps.textContent=t('chatGeoOpenMaps');
        maps.addEventListener('click',()=>window.pharosOpenView?.('maps'));
        container.append(maps);
      }
      log.append(container);
    }
  }
  log.scrollTop=log.scrollHeight;
}
async function submitChat(event) {
  event.preventDefault();
  const input=el('chat-input'); const message=input.value.trim(); const model=el('model-select').value;
  if (!message || !model || busy) return;
  const kb_ids = window.selectedChatKB ? window.selectedChatKB() : [];
  const geo_enabled=el('chat-geo-enabled').checked;
  if(geo_enabled&&kb_ids.length){
    el('chat-feedback').textContent=t('chatGeoKBConflict');
    return;
  }
  busy=true; el('send-button').disabled=true; el('clear-chat').disabled=true; el('chat-feedback').textContent=t('waiting');
  chatHistory.push({role:'user',content:message});
  while (chatHistory.length>19) chatHistory.splice(0,2);
  input.value=''; renderChat();
  try {
    const thinkingChoice=el('chat-thinking').hidden?'auto':el('chat-thinking').value;
    const answer=await api('chat',{method:'POST',body:JSON.stringify({
      model, ...(thinkingChoice==='auto'?{}:{thinking:thinkingChoice==='on'}), messages:chatHistory.filter(item=>typeof item.content==='string' && item.content.trim())
        .map(({role,content})=>({role,content})), kb_ids, geo_enabled
    })});
    if (!answer.insufficient_evidence && (typeof answer.answer!=='string' || !answer.answer.trim()))
      throw new Error('rag_answer_empty');
    chatHistory.push({role:'assistant',content:answer.insufficient_evidence ? t('chatKBInsufficient') : answer.answer,
                      sources: answer.sources || [],citations_missing:Boolean(answer.citations_missing),
                      geo_results: Array.isArray(answer.geo_results)?answer.geo_results:[],
                      geo_query: typeof answer.geo_query==='string'?answer.geo_query:'',
                      geo_status: typeof answer.geo_status==='string'?answer.geo_status:'',
                      geo_diagnostics: answer.geo_diagnostics||{}});
    el('chat-feedback').textContent='';
  } catch (error) {
    chatHistory.pop(); input.value=message;
    const reason=String(error.message || 'network_error');
    el('chat-feedback').textContent=t('chatError') + ': ' + (
      reason.includes('gateway_auth_failed') ? t('authError') :
      reason.includes('gateway_unavailable') ? t('gatewayError') :
      reason.includes('rag_answer_empty') || reason.includes('gateway_empty_completion') ? t('chatEmptyAnswerError') :
      reason.includes('thinking_control_') ? t('chatThinkingUnsupported') : reason);
  } finally { busy=false; el('clear-chat').disabled=false; el('send-button').disabled=!el('model-select').value; renderChat(); }
}
async function initialize() {
  try {
    appConfig=await api('config');
    // All deferred scripts have registered their UI handlers at this point.
    // Load the single JSON translation source before rendering any views.
    await Promise.all([loadLanguageBundle('en'),loadLanguageBundle('it')]);
    await window.pharosLoadLocaleCatalog();
    await window.pharosUseLanguage(appConfig.language);
    renderConfig();
    if(window.pharosSetKnowledgeModuleEnabled)window.pharosSetKnowledgeModuleEnabled(Boolean(appConfig.modules?.knowledge_enabled));
    showConfiguredChatModelImmediately();
    if (window.pharosLoadHome) await window.pharosLoadHome();
    const view=window.location.hash.slice(1);showView(view);
    // Slow Tiiny inventory is an eventual background refresh, never a
    // prerequisite for navigating the UI or sending via configured default.
    void loadModels().catch(error=>console.warn('Chat catalog refresh failed',error));
    void refreshStatus();
  } catch (error) { console.error('PHAROS initialization failed',error); el('health-note').textContent=t('refreshFail'); }
}
el('pharos-home').addEventListener('click',()=>showView('home'));
document.querySelectorAll('[data-view]').forEach(node=>node.addEventListener('click',()=>{if(!document.body.classList.contains('home-editing'))showView(node.dataset.view);}));
window.pharosOpenView=showView;
document.querySelectorAll('[data-go]').forEach(node=>node.addEventListener('click',()=>showView(node.dataset.go==='notifications' && node.classList.contains('has-update') ? 'diagnostics' : node.dataset.go)));
document.querySelectorAll('[data-go-refresh]').forEach(node=>node.addEventListener('click',()=>{refreshStatus();if(window.pharosCheckModules)window.pharosCheckModules();}));
window.addEventListener('hashchange',()=>{
  const chosen=window.location.hash.slice(1);
  if(!document.getElementById('view-'+chosen)?.classList.contains('active'))showView(chosen);
});
// Do not forward the click Event as loadModels(preferDefault): it is truthy.
el('refresh-models').addEventListener('click',()=>loadModels(false,true));
el('model-select').addEventListener('change',updateChatModelLabels);
el('chat-npu-action').addEventListener('click',async()=>{
  const model=el('model-select').value;
  const loaded=chatModelResidency.get(model);
  if(chatInventoryMode!=='tiiny_direct'||typeof loaded!=='boolean'||!model)return;
  if(loaded&&!confirm(t('npuConfirmUnload')))return;
  const b=el('chat-npu-action');b.disabled=true;
  try{await api('ai/models/'+(loaded?'stop':'launch'),{method:'POST',body:JSON.stringify({model})});await loadModels(false,true);}
  catch(error){el('chat-feedback').textContent=error.message;}
  finally{b.disabled=false;}
});


el('chat-geo-enabled').addEventListener('change',()=>{el('chat-feedback').textContent='';});
el('clear-chat').addEventListener('click',()=>{chatHistory=[];renderChat();el('chat-feedback').textContent='';});
el('chat-form').addEventListener('submit',submitChat);
el('chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();el('chat-form').requestSubmit();}});


document.addEventListener('DOMContentLoaded', initialize, {once:true});













