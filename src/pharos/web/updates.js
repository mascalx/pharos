"use strict";
// Update discovery and server-gated Ubuntu update via detached worker.
// The browser never executes commands or supplies a remote URL.




let installSnapshot=null;
let installingRequest=false;
let installStartError='';
let updateObservedActive=false;
let updateReloadTriggered=false;
let installationGeneration=0;
const activeUpdateStages=new Set(['queued','checking','backup','applying','verifying','rollback']);
function updateErrorText(error){
  const reason=String(error||'').replace(/^http_\d+:\s*/, '');
  const key='updatesError_'+reason;
  const translated=t(key);
  return translated===key?(reason?t('updatesInstallFailed')+' ('+reason+')':t('updatesInstallFailed')):translated;
}
function renderInstallStatus(){
  const button=el('updates-install');
  const note=el('updates-install-status');
  if(!button||!note)return;
  const state=installSnapshot?.status||'idle';
  const active=activeUpdateStages.has(state)||installingRequest;
  button.hidden=!lastUpdateSnapshot?.available||active||Boolean(lastUpdateSnapshot?.checking);
  button.disabled=active;
  el('pharos-updates-panel').classList.toggle('is-updating',active);
  el('updates-check').disabled=active||updatesBusy;
  note.classList.toggle('update-running',active);
  if(active)note.textContent=t('updatesInstallRunning');
  else if(installStartError)note.textContent=updateErrorText(installStartError);
  else if(state==='rolled_back')note.textContent=t('updatesInstallRolledBack');
  else if(state==='rollback_failed')note.textContent=t('updatesInstallRollbackFailed');
  else if(state==='interrupted')note.textContent=t('updatesInstallInterrupted');
  else if(state==='failed')note.textContent=updateErrorText(installSnapshot.error);
  else note.textContent='';
  note.hidden=!note.textContent;
  window.pharosUpdateAvailable=Boolean(lastUpdateSnapshot?.available)&&!active&&!lastUpdateSnapshot?.checking;
  if(window.pharosRefreshNotificationIndicator)window.pharosRefreshNotificationIndicator();
}
window.pharosLoadInstallation=async function(){
  const generation=installationGeneration;
  try{
    const status=await api('updates/installation');
    if(generation!==installationGeneration||installingRequest)return;
    installSnapshot=status;
    if(activeUpdateStages.has(status.status)){
      updateObservedActive=true;
      installStartError='';
    }
    renderInstallStatus();
    // Only reload after this browser has actually witnessed an update in
    // progress. A persisted "completed" record must not cause reload loops.
    // The worker marks completion only after the installer and health checks.
    if(status.status==='completed'&&updateObservedActive&&!updateReloadTriggered&&!installingRequest){
      updateReloadTriggered=true;
      window.location.reload();
      return;
    }
  }catch(_){
    if(installSnapshot&&activeUpdateStages.has(installSnapshot.status)){
      renderInstallStatus();
    }
  }
};
let lastUpdateSnapshot=null;
let updatesBusy=false;
window.pharosUpdateAvailable=false;
function renderUpdates(){
  const state=lastUpdateSnapshot||{};
  const status=el('updates-status');
  if(!status)return;
  const key=(updatesBusy||state.checking)?'updatesChecking':({
    available:'updatesAvailable', current:'updatesCurrent',
    unavailable:'updatesUnavailable', unsupported:'updatesUnsupported',
    unchecked:'updatesWaiting'
  }[state.state]||'updatesWaiting');
  status.textContent=t(key);
  status.hidden=!(updatesBusy||state.checking||!['available','current'].includes(state.state));
  status.classList.toggle('ok',state.state==='current');
  status.classList.toggle('warn',state.state==='available');
  status.classList.toggle('bad',state.state==='unavailable');
  el('updates-installed').textContent=state.version||'—';
  el('updates-remote').textContent=state.remote_version||(state.state==='current'?state.version:'')||'—';
  const time=state.last_checked;
  el('updates-last-checked').textContent=time?
    t('updatesCheckedAt')+' '+new Date(time*1000).toLocaleString(lang==='en'?'en-GB':'it-IT'):t('updatesCheckedAt')+' —';
  const link=el('updates-compare');
  // Only open the official GitHub compare route, never an arbitrary server URL.
  const url=state.compare_url||'';
  link.hidden=!(state.available&&/^https:\/\/github\.com\/mascalx\/pharos\/compare\/[0-9a-f]{40}\.\.\.main$/.test(url));
  if(!link.hidden)link.href=url;
  renderInstallStatus();
}
window.pharosRenderUpdateStatus=renderUpdates;
window.pharosLoadUpdates=async function(manual=false,refresh=false){
  if(updatesBusy)return;
  updatesBusy=manual;
  if(manual){el('updates-check').disabled=true;renderUpdates();}
  try{lastUpdateSnapshot=await api(manual?'updates/check':'updates'+(refresh?'?refresh=1':''),manual?{method:'POST'}:{});}
  catch(error){
    lastUpdateSnapshot={...lastUpdateSnapshot,state:'unavailable',available:false,checking:false,remote_version:''};
  }finally{
    updatesBusy=false;
    if(manual)el('updates-check').disabled=false;
    renderUpdates();
  }
};
el('updates-check').addEventListener('click',()=>void window.pharosLoadUpdates(true));
el('updates-install').addEventListener('click',async()=>{
  const sha=lastUpdateSnapshot?.remote_sha;
  if(!lastUpdateSnapshot?.available||!sha||!(/^[0-9a-f]{40}$/).test(sha)||installingRequest||updatesBusy||lastUpdateSnapshot.checking||activeUpdateStages.has(installSnapshot?.status))return;
  installationGeneration++;
  installStartError='';installingRequest=true;renderInstallStatus();
  try{
    installSnapshot=await api('updates/start',{
      method:'POST',body:JSON.stringify({target_sha:sha})});
    updateObservedActive=true;
    renderInstallStatus();
  }catch(error){
    installStartError=error.message;
    if(error.message.includes('update_revision_changed'))void window.pharosLoadUpdates(false,true);
  }finally{
    installationGeneration++;
    installingRequest=false;renderInstallStatus();
    void window.pharosLoadInstallation();
  }
});
// Startup and Diagnostics request fresh evidence without blocking the page.
// Short polling is active only while a background Git probe is still running.
window.pharosLoadUpdates(false,true);
setInterval(()=>{
  if(lastUpdateSnapshot?.checking)void window.pharosLoadUpdates(false);
},2500);
setInterval(()=>window.pharosLoadUpdates(false,true),60000);

window.pharosLoadInstallation();
setInterval(()=>window.pharosLoadInstallation(),5000);
