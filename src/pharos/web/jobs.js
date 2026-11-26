"use strict";
// The jobs database is the single durable source for operational progress and
// completion events. All running/queued background operations appear as
// silent navigational cards; only terminal events can be acknowledged.


const jobStateLabel={queued:'jobsPending',running:'jobsRunning',completed:'jobsDone',
  failed:'jobsFailed',cancelled:'jobsCancelled',interrupted:'jobsInterrupted'};
const jobKindLabel={'ingest-document':'jobsIngest','reindex-rag':'jobsReindex','build-lexical':'jobsLexical','download-zim':'jobsZim','download-map':'jobsMap','index-map':'jobsMapIndex'};
const jobTerminal=new Set(['completed','failed','cancelled','interrupted']);
const jobActive=new Set(['queued','running']);
let jobsRefreshing=false;
let lastNotificationSignature='';
let lastKnowledgeProgressSignature='';
let lastKiwixProgressSignature='';
let lastKnowledgeReloadSignature='';
const acknowledgedJobIds=new Set();
let visibleUnreadJobs=[];
let visibleActiveJobs=[];
let jobsRefreshPending=false;

function setNotificationIndicator(unread){
  const indicator=el('notification-indicator');
  if(!indicator)return;
  indicator.classList.remove('has-activity');
  indicator.classList.toggle('has-unread',unread.length>0);
  indicator.classList.toggle('has-error',unread.some(job=>job.status==='failed'));
  indicator.classList.toggle('has-update',!unread.length&&Boolean(window.pharosUpdateAvailable));
  const lit=unread.length>0||Boolean(window.pharosUpdateAvailable);
  indicator.hidden=!lit;
  indicator.title=unread.length?`${unread.length} · ${t('notifications')}`:
    window.pharosUpdateAvailable?t('updatesAvailable'):t('notifications');
}

window.pharosRefreshNotificationIndicator=()=>setNotificationIndicator(visibleUnreadJobs);

function jobHeader(job){return t(jobKindLabel[job.kind]||'jobsZim')+' · '+job.label;}
function jobState(job){
  let detail=['download-map','index-map'].includes(job.kind)?t(job.detail):job.detail;
  if(job.kind==='build-lexical'){
    if(job.detail.startsWith('lexical_scanning: '))
      detail=t('jobsLexicalScanning')+' · '+job.detail.slice('lexical_scanning: '.length);
    else if(job.detail==='lexical_scanning')detail=t('jobsLexicalScanning');
    else if(job.detail==='resuming_lexical')detail=t('jobsLexicalResuming');
    else if(job.detail==='lexical_ready')detail=t('jobsLexicalReady');
    else if(job.detail==='waiting_for_reindex')detail=t('jobsLexicalWaiting');
  }
  return t(jobStateLabel[job.status]||'jobsFailed')+(job.detail?' · '+detail:'');
}
function jobBytes(job){
  if(['maps_search_importing','maps_search_building_fts',
      'maps_search_building_rtree','maps_search_building_tags',
       'maps_search_verifying'].includes(job.detail)){
    const done=Number(job.bytes_done||0).toLocaleString();
    const total=Number(job.bytes_total||0);
    return total>0?t('mapsSearchIndexingProgress')
      .replace('{done}',done).replace('{total}',total.toLocaleString()):
      t('mapsSearchIndexingCount').replace('{count}',done);
  }
  if(!['download-zim','download-map','index-map'].includes(job.kind)||
     !job.bytes_done||job.detail==='maps_search_indexing')return '';
  const mb=n=>(n/(1024*1024)).toFixed(1)+' MiB';
  return `${mb(job.bytes_done)}${job.bytes_total?' / '+mb(job.bytes_total):''}`;
}
function moduleProgress(containerId,jobs,headingKey){
  const container=el(containerId);
  if(!container)return;
  if(!jobs.length){container.hidden=true;container.replaceChildren();return;}
  const root=document.createDocumentFragment();
  const heading=document.createElement('h4');heading.textContent=t(headingKey);root.append(heading);
  for(const job of jobs){
    const row=document.createElement('div');row.className='module-job-row';
    const info=document.createElement('div');info.className='job-details';
    const name=document.createElement('strong');name.textContent=jobHeader(job);
    const state=document.createElement('small');state.textContent=jobState(job);
    const bar=document.createElement('progress');bar.max=100;bar.value=Math.max(0,Math.min(100,job.progress||0));
    bar.setAttribute('aria-label',jobHeader(job));
    info.append(name,state,bar);
    const bytes=jobBytes(job);
    if(bytes){const summary=document.createElement('small');summary.textContent=bytes;info.append(summary);}
    row.append(info);
    if(['ingest-document','build-lexical','download-zim','download-map','index-map'].includes(job.kind)){
      const cancel=document.createElement('button');cancel.type='button';cancel.className='outline-btn';
      cancel.textContent=t('jobsCancel');
      cancel.addEventListener('click',async()=>{
        cancel.disabled=true;
        try{await api('jobs/'+encodeURIComponent(job.id)+'/cancel',{method:'POST'});await window.pharosLoadJobs();}
        catch(error){state.textContent=error.message;}
        finally{cancel.disabled=false;}
      });
      row.append(cancel);
    }
    root.append(row);
  }
  container.replaceChildren(root);container.hidden=false;
}
async function openJobNotification(job,button){
  if(button.disabled)return;
  button.disabled=true;
  try{
    // Archive on the server first. If this fails, keep the message visible.
    await api('jobs/'+encodeURIComponent(job.id)+'/read',{method:'POST'});
    // Update the bar immediately, without waiting for the next 5-second poll.
    // A concurrent in-flight GET cannot resurrect this acknowledged message.
    acknowledgedJobIds.add(job.id);
    visibleUnreadJobs=visibleUnreadJobs.filter(item=>item.id!==job.id);
    renderNotifications(visibleActiveJobs,visibleUnreadJobs);
    setNotificationIndicator(visibleUnreadJobs);
    lastNotificationSignature='';
    if(job.target_view==='knowledge'){
      // Refresh KB cards without deleting/reindexing any document.
      const refresh=el('refresh-kb');if(refresh)refresh.click();
      showView('knowledge');
    }else if(job.target_view==='kiwix')showView('kiwix');
    else if(job.target_view==='maps')showView('maps');
    else showView('notifications');
    await window.pharosLoadJobs();
  }catch(error){
    button.disabled=false;
    const panel=el('jobs-feedback');
    if(panel)panel.textContent=t('jobsReadError')+': '+error.message;
  }
}
function renderNotifications(activeJobs,notifications){
  const container=el('job-list');if(!container)return;
  const fragment=document.createDocumentFragment();
  if(!activeJobs.length&&!notifications.length){const note=document.createElement('p');note.textContent=t('jobsEmpty');fragment.append(note);}
  for(const job of activeJobs){
    const button=document.createElement('button');button.type='button';
    button.className='job-notification job-notification-active';
    const destinationKey=job.target_view==='knowledge'?'jobsOpenKnowledge':
      job.target_view==='maps'?'jobsOpenMaps':'jobsOpenKiwix';
    button.title=t(destinationKey);
    const title=document.createElement('strong');title.textContent=jobHeader(job);
    const description=document.createElement('small');description.textContent=jobState(job);
    const destination=document.createElement('small');
    destination.textContent=t(job.target_view==='knowledge'?'jobsOpenKnowledge':
      job.target_view==='maps'?'jobsOpenMaps':'jobsActiveDownloadNotification');
    const progress=document.createElement('progress');progress.max=100;
    if(!['download-map','index-map'].includes(job.kind))progress.value=Math.max(0,Math.min(100,Number(job.progress)||0));
    // PMTiles CLI currently reports bytes written but no trustworthy total.
    // Leave this progress element indeterminate instead of inventing a percent.
    progress.setAttribute('aria-label',jobHeader(job));
    button.append(title,description,progress);
    const bytes=jobBytes(job);
    if(bytes){const summary=document.createElement('small');summary.textContent=bytes;button.append(summary);}
    button.append(destination);
    // Navigating does NOT mark active jobs as read or cancel them. Their
    // cards remain until a durable terminal state replaces them.
    button.addEventListener('click',()=>showView(job.target_view==='knowledge'?'knowledge':
      job.target_view==='maps'?'maps':'kiwix'));
    fragment.append(button);
  }
  for(const job of notifications){
    const button=document.createElement('button');button.type='button';
    button.className='job-notification'+(job.status==='failed'?' job-notification-failed':'');
    button.title=t('jobsOpenNotification');
    const title=document.createElement('strong');title.textContent=jobHeader(job);
    const description=document.createElement('small');description.textContent=jobState(job);
    const destination=document.createElement('small');destination.textContent=t(job.target_view==='knowledge'?'jobsOpenKnowledge':job.target_view==='maps'?'jobsOpenMaps':'jobsOpenKiwix');
    button.append(title,description,destination);
    button.addEventListener('click',()=>openJobNotification(job,button));
    fragment.append(button);
  }
  container.replaceChildren(fragment);
}
// Re-render labels after a live language switch even if the job data and
// progress values have not changed since the preceding poll.
window.pharosRefreshJobTranslations=()=>{
  lastNotificationSignature='';
  lastKnowledgeProgressSignature='';
  lastKiwixProgressSignature='';
  void window.pharosLoadJobs();
};
window.pharosLoadJobs=async function(){
  if(jobsRefreshing){jobsRefreshPending=true;return;}
  jobsRefreshing=true;
  try{
    const result=await api('jobs');
    const jobs=Array.isArray(result.items)?result.items:[];
    const active=jobs.filter(job=>jobActive.has(job.status));
    // Reindex progress already has its own dedicated KB panel (#kb-reindex-progress).
    // Do not duplicate it in the ingest job list.
    const knowledgeActive=active.filter(job=>job.kind==='ingest-document'||job.kind==='reindex-rag'||job.kind==='build-lexical');
    const knowledge=knowledgeActive.filter(job=>job.kind==='ingest-document'||job.kind==='build-lexical');
    const kiwix=active.filter(job=>job.kind==='download-zim').sort((a,b)=>
      (a.status==='running'?0:1)-(b.status==='running'?0:1) ||
      (Number(a.queue_sequence)||0)-(Number(b.queue_sequence)||0));
    const maps=active.filter(job=>['download-map','index-map'].includes(job.kind)).sort((a,b)=>
      (a.status==='running'?0:1)-(b.status==='running'?0:1)||
      (Number(a.queue_sequence)||0)-(Number(b.queue_sequence)||0));
    const downloads=[...kiwix,...maps];
    const unread=jobs.filter(job=>jobTerminal.has(job.status)&&!job.read_at&&!acknowledgedJobIds.has(job.id));
    visibleUnreadJobs=unread;
    const activeNotifications=[...knowledgeActive,...downloads];
    visibleActiveJobs=activeNotifications;
    if(window.pharosSyncKBUploadNotices)window.pharosSyncKBUploadNotices(jobs);
    // An in-progress job never lights the header bar.
    setNotificationIndicator(unread);
    const notificationSignature=JSON.stringify([
      ...activeNotifications.map(job=>[job.id,job.kind,job.status,job.detail,job.progress,job.bytes_done,job.bytes_total]),
      ...unread.map(job=>[job.id,job.status,job.detail,job.updated_at])]);
    if(notificationSignature!==lastNotificationSignature){
      renderNotifications(activeNotifications,unread);lastNotificationSignature=notificationSignature;
    }
    const knowledgeSignature=JSON.stringify(knowledge.map(job=>[job.id,job.status,job.progress,job.detail,job.bytes_done,job.bytes_total]));
    if(knowledgeSignature!==lastKnowledgeProgressSignature){
      moduleProgress('kb-active-jobs',knowledge,'jobsActiveKnowledge');
      lastKnowledgeProgressSignature=knowledgeSignature;
    }
    const kiwixSignature=JSON.stringify(kiwix.map(job=>[job.id,job.status,job.progress,job.detail,job.bytes_done,job.bytes_total]));
    if(kiwixSignature!==lastKiwixProgressSignature){
      moduleProgress('kiwix-active-jobs',kiwix,'jobsActiveKiwix');
      lastKiwixProgressSignature=kiwixSignature;
    }
    const banner=el('chat-background-hint');
    if(banner){banner.hidden=!knowledgeActive.length;banner.textContent=knowledgeActive.length?t('jobsBackgroundChat'):'';}
    // Keep indexed-document counts and the per-document progress in sync.
    if(knowledgeSignature!==lastKnowledgeReloadSignature && el('view-knowledge').classList.contains('active')){
      el('refresh-kb').click();
    }
    lastKnowledgeReloadSignature=knowledgeSignature;
  }catch(error){console.warn('Jobs unavailable:',error.message);}
  finally{
    jobsRefreshing=false;
    if(jobsRefreshPending){jobsRefreshPending=false;void window.pharosLoadJobs();}
  }
};
el('jobs-refresh').addEventListener('click',()=>{
  lastNotificationSignature='';lastKnowledgeProgressSignature='';lastKiwixProgressSignature='';
  window.pharosLoadJobs();
});
setInterval(()=>window.pharosLoadJobs(),5000);
window.pharosLoadJobs();
