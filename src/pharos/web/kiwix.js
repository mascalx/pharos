"use strict";
// Local library and official online OPDS catalog are independent. An empty
// library does not start kiwix-serve; online discovery still works.


let kiwixRequestGeneration=0,kiwixStart=0,kiwixHasMore=false,kiwixBrowserURL='';
let kiwixKnownBooks=new Set();
let kiwixCatalogGeneration=0,kiwixCatalogStart=0,kiwixCatalogMore=false;
let kiwixCatalogBusy=false,kiwixActiveDownload='';
let kiwixCatalogFiltersInitialized=false;
let kiwixLinksByFilename=new Map();
let kiwixLinkChooser=null;
async function kiwixRefreshLinks(){
  try{
    const result=await api('kiwix/kb-links');
    const links=new Map();
    for(const entry of result.items||[]){
      if(!links.has(entry.filename))links.set(entry.filename,[]);
      links.get(entry.filename).push({id:entry.kb_id,name:entry.kb_name});
    }
    kiwixLinksByFilename=links;
  }catch(error){kiwixLinksByFilename=new Map();}
  document.querySelectorAll('.kiwix-linked-kbs[data-zim-filename]').forEach(node=>{
    const names=(kiwixLinksByFilename.get(node.dataset.zimFilename)||[]).map(k=>k.name);
    node.textContent=names.length?t('kiwixLinkedKBs')+' '+names.join(', '):t('kiwixLinkedKBNone');
  });
}
function closeKiwixLinkChooser(){
  if(!kiwixLinkChooser)return;
  const {chooser,button}=kiwixLinkChooser;
  chooser.remove();button.disabled=false;kiwixLinkChooser=null;
}

function kiwixEditionDetails(filename){
  const name=String(filename||'').toLowerCase().replace(/\.zim$/,'');
  const parts=name.split('_');
  const edition=parts.includes('mini')?'kiwixEditionMini':
    parts.includes('nopic')?'kiwixEditionNopic':
    parts.includes('maxi')?'kiwixEditionMaxi':'kiwixEditionOther';
  const topic=parts.includes('medicine')?'kiwixEditionMedicine':
    parts.includes('all')?'kiwixEditionComplete':null;
  return [t(edition),...(topic?[t(topic)]:[])].join(' · ');
}
function kiwixFilenameNote(card,filename){
  if(!filename)return;
  const note=document.createElement('small');note.className='kiwix-filename';
  note.textContent=filename;card.append(note);
}
async function kiwixDeleteZim(book,button){
  if(!book.filename)return;
  if(!window.confirm(t('kiwixDeleteConfirm').replace('{name}',book.filename)))return;
  button.disabled=true;
  const feedback=el('kiwix-feedback');
  try{
    const result=await api('extensions/kiwix/zims/'+encodeURIComponent(book.filename),{method:'DELETE'});
    await loadKiwixPage(true);await loadKiwixCatalog(true);
    feedback.textContent=t('kiwixDeleteSuccess')+(result.restart_error?' · '+result.restart_error:'');
  }catch(error){
    button.disabled=false;
    feedback.textContent=String(error.message).includes('kiwix_download_in_progress')?
      t('kiwixDeleteBusy'):t('kiwixDeleteFailed')+' ('+error.message+')';
  }
}
async function kiwixLinkZim(book,button){
  if(!book.filename)return;
  if(kiwixLinkChooser){
    // The same click closes its chooser. A different archive replaces it.
    const same=kiwixLinkChooser.button===button;
    if(kiwixLinkChooser.busy)return;
    closeKiwixLinkChooser();
    if(same)return;
  }
  button.disabled=true;
  const feedback=el('kiwix-feedback');
  const chooser=document.createElement('div');chooser.className='kiwix-kb-link-chooser';
  kiwixLinkChooser={chooser,button,busy:true};
  try{
    const data=await api('kb');
    if(kiwixLinkChooser?.chooser!==chooser)return;
    const linked=new Set((kiwixLinksByFilename.get(book.filename)||[]).map(k=>k.id));
    const bases=(data.items||[]).filter(k=>!linked.has(k.id));
    if(!bases.length){
      feedback.textContent=(data.items||[]).length?t('kiwixLinkKBAlready'):t('kiwixLinkKBMissing');
      closeKiwixLinkChooser();return;
    }
    const select=document.createElement('select');select.setAttribute('aria-label',t('kiwixLinkKBSelect'));
    for(const kb of bases){const option=document.createElement('option');option.value=kb.id;
      option.textContent=kb.name;select.append(option);}
    const confirm=document.createElement('button');confirm.type='button';confirm.className='outline-btn';
    confirm.textContent=t('kiwixLinkKBConfirm');
    const cancel=document.createElement('button');cancel.type='button';cancel.className='outline-btn';
    cancel.textContent=t('kiwixLinkKBCancel');
    cancel.addEventListener('click',()=>closeKiwixLinkChooser());
    confirm.addEventListener('click',async()=>{
      if(kiwixLinkChooser?.chooser!==chooser||kiwixLinkChooser.busy)return;
      kiwixLinkChooser.busy=true;confirm.disabled=true;cancel.disabled=true;
      try{
        await api('kb/'+encodeURIComponent(select.value)+'/zim-sources',{
          method:'POST',body:JSON.stringify({filename:book.filename})});
        feedback.textContent=t('kiwixLinkKBSuccess');
        await kiwixRefreshLinks();
        if(window.pharosLoadKnowledge)el('refresh-kb')?.click();
        closeKiwixLinkChooser();
      }catch(error){feedback.textContent=error.message;
        confirm.disabled=false;cancel.disabled=false;
        if(kiwixLinkChooser?.chooser===chooser)kiwixLinkChooser.busy=false;
      }
    });
    chooser.append(select,confirm,cancel);button.after(chooser);
    kiwixLinkChooser.busy=false;
  }catch(error){feedback.textContent=error.message;closeKiwixLinkChooser();}
}
// ZIM articles are loaded through PHAROS' fixed /kiwix proxy. The iframe is
// sandboxed WITHOUT allow-same-origin, so archived HTML cannot access PHAROS.
function kiwixSafeLocalURL(value){
  try{
    const url=new URL(value,window.location.href);
    if(url.origin!==window.location.origin||
       !(url.pathname==="/kiwix"||url.pathname.startsWith("/kiwix/")))
      return null;
    return url.pathname+url.search+url.hash;
  }catch(error){return null;}
}
function kiwixCloseReader(){
  const panel=el("kiwix-viewer");
  if(panel.hidden)return;
  panel.hidden=true;
  el("kiwix-viewer-frame").src="about:blank";
  document.body.classList.remove("kiwix-viewer-active");
}
function kiwixOpenReader(url){
  const safe=kiwixSafeLocalURL(url);
  if(!safe)return false;
  window.pharosCloseMap?.();
  el("kiwix-viewer-frame").src=safe;
  el("kiwix-viewer").hidden=false;
  document.body.classList.add("kiwix-viewer-active");
  el("kiwix-viewer-close").focus();
  return true;
}
window.pharosCloseKiwix=kiwixCloseReader;
window.pharosOpenKiwix=kiwixOpenReader;
el("kiwix-viewer-close").addEventListener("click",kiwixCloseReader);
document.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&!el("kiwix-viewer").hidden)kiwixCloseReader();
});
el("kiwix-search-form").addEventListener("submit",event=>{
  event.preventDefault();
  const form=el("kiwix-search-form");
  const url=new URL(form.action,window.location.href);
  url.search=new URLSearchParams(new FormData(form)).toString();
  if(!kiwixOpenReader(url.href))
    el("kiwix-feedback").textContent=t("kiwixViewerInvalidURL");
});

function kiwixBookCard(book){
  const card=document.createElement('article');card.className='kiwix-book';
  // Use the archive title itself as the large, accessible mobile link.
  // Keep destructive and KB management controls outside that link.
  const title=document.createElement(book.url?'a':'strong');
  title.className='kiwix-book-title';
  title.textContent=book.title;
  if(book.url){
    title.href=book.url;
    title.addEventListener('click',event=>{
      if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;
      if(kiwixOpenReader(book.url))event.preventDefault();
    });
  }
  card.append(title);
  if(book.language){const lang=document.createElement('small');lang.textContent=book.language;card.append(lang);}
  if(book.description){const desc=document.createElement('p');desc.textContent=book.description;card.append(desc);}
  if(book.filename){const edition=document.createElement('small');edition.textContent=kiwixEditionDetails(book.filename);card.append(edition);}
  kiwixFilenameNote(card,book.filename);
  if(book.filename){
    const linked=document.createElement('small');linked.className='kiwix-linked-kbs';
    linked.dataset.zimFilename=book.filename;
    const names=(kiwixLinksByFilename.get(book.filename)||[]).map(k=>k.name);
    linked.textContent=names.length?t('kiwixLinkedKBs')+' '+names.join(', '):t('kiwixLinkedKBNone');
    card.append(linked);
  }
  const controls=document.createElement('div');controls.className='kiwix-book-actions';
  if(book.filename){const linkkb=document.createElement('button');linkkb.type='button';
    linkkb.className='outline-btn';linkkb.textContent=t('kiwixLinkKB');
    linkkb.addEventListener('click',()=>kiwixLinkZim(book,linkkb));controls.append(linkkb);}
  if(book.filename){const remove=document.createElement('button');remove.type='button';
    remove.className='outline-btn kiwix-delete-btn';remove.textContent=t('kiwixDelete');
    remove.addEventListener('click',()=>kiwixDeleteZim(book,remove));controls.append(remove);}
  card.append(controls);
  return card;
}
async function kiwixStatus(){
  const status=el('kiwix-server-status');
  try{
    const result=await api('extensions/kiwix/status');
    status.textContent=!result.installed?t('kiwixRuntimeMissing'):
      result.waiting_for_zim?t('kiwixWaitingZim'):
      result.running?t('kiwixRunning'):
      result.error?t('kiwixNotConfigured')+' ('+result.error+')':t('kiwixNotConfigured');
    return result;
  }catch(error){status.textContent=t('kiwixNotConfigured')+' ('+error.message+')';return null;}
}
async function loadKiwixPage(reset=true){
  const generation=++kiwixRequestGeneration;
  const list=el('kiwix-book-list'),feedback=el('kiwix-feedback');
  const more=el('kiwix-more');more.disabled=true;
  if(reset){closeKiwixLinkChooser();kiwixStart=0;kiwixKnownBooks=new Set();list.replaceChildren();}
  feedback.textContent='';
  try{
    const status=await kiwixStatus();
    if(!status?.installed){
      if(generation===kiwixRequestGeneration){feedback.textContent=t('kiwixEmpty');more.hidden=true;el('kiwix-search-form').hidden=true;}
      return;
    }
    if(status.waiting_for_zim){
      if(generation===kiwixRequestGeneration){feedback.textContent=t('kiwixEmpty');more.hidden=true;el('kiwix-search-form').hidden=true;}
      return;
    }
    await kiwixRefreshLinks();
    if(generation!==kiwixRequestGeneration)return;
    const result=await api('extensions/kiwix/books?start='+kiwixStart+'&count=30');
    if(generation!==kiwixRequestGeneration)return;
    const books=result.items||[];kiwixBrowserURL=result.browser_url;
    const form=el('kiwix-search-form');form.action=kiwixBrowserURL.replace(/\/$/,'')+'/search';
    const chooser=el('kiwix-book-select');if(reset)chooser.replaceChildren();
    const fragment=document.createDocumentFragment();
    for(const book of books){fragment.append(kiwixBookCard(book));
      if(book.name&&!kiwixKnownBooks.has(book.name)){
        chooser.add(new Option(book.title,book.name));kiwixKnownBooks.add(book.name);
      }
    }
    list.append(fragment);form.hidden=!chooser.options.length;
    kiwixStart+=books.length;kiwixHasMore=Boolean(result.more);
    more.hidden=!kiwixHasMore;
    if(!kiwixStart)feedback.textContent=t('kiwixEmpty');
  }catch(error){if(generation===kiwixRequestGeneration){feedback.textContent=t('kiwixNotConfigured')+' ('+error.message+')';more.hidden=true;el('kiwix-search-form').hidden=true;}}
  finally{more.disabled=false;}
}
function kiwixSize(bytes){
  if(!Number.isFinite(bytes)||bytes<=0)return t('kiwixSizeUnknown');
  const units=['B','KB','MB','GB','TB'];let size=bytes,unit=0;
  while(size>=1024&&unit<units.length-1){size/=1024;unit++;}
  return (unit<2?Math.round(size):size.toFixed(size<10?1:0))+' '+units[unit];
}
function kiwixError(error){
  const code=String(error.message||'');
  return code.includes('zim_already_queued')?t('kiwixDownloadBusy'):
    code.includes('zim_already_exists')?t('kiwixDownloadExists'):code;
}
async function kiwixBeginDownload(item,button){
  if(!item.url||item.installed)return;
  if(item.size>=1024**3 && !window.confirm(t('kiwixDownloadConfirm').replace('{size}',kiwixSize(item.size))))return;
  const feedback=el('kiwix-download-feedback');
  button.disabled=true;
  try{
    const result=await api('jobs/kiwix/zim',{method:'POST',body:JSON.stringify({url:item.url})});
    kiwixActiveDownload=result.id;
    button.textContent=t('kiwixQueued');feedback.textContent=t('kiwixDownloadStarted');
    if(window.pharosLoadJobs)await window.pharosLoadJobs();
  }catch(error){button.disabled=false;feedback.textContent=kiwixError(error);}
}
function kiwixCatalogCard(item){
  const card=document.createElement('article');card.className='kiwix-book kiwix-catalog-book';
  const title=document.createElement('strong');title.textContent=item.title;card.append(title);
  const meta=document.createElement('small');meta.className='kiwix-catalog-meta';
  meta.textContent=[item.language,item.category,kiwixSize(item.size),item.updated?.slice(0,10)].filter(Boolean).join(' · ');
  card.append(meta);
  if(item.description){const desc=document.createElement('p');desc.textContent=item.description;card.append(desc);}
  if(item.filename){const edition=document.createElement('small');edition.className='kiwix-edition';
    edition.textContent=kiwixEditionDetails(item.filename);card.append(edition);}
  kiwixFilenameNote(card,item.filename);
  const button=document.createElement('button');button.type='button';button.className='outline-btn';
  button.textContent=item.installed?t('kiwixInstalled'):t('kiwixDownload');
  button.disabled=item.installed;button.addEventListener('click',()=>kiwixBeginDownload(item,button));
  card.append(button);return card;
}
async function loadKiwixCatalog(reset=true){
  // Generation guard means old searches cannot append to a newer filter query.
  const generation=++kiwixCatalogGeneration;
  const feedback=el('kiwix-catalog-feedback'),list=el('kiwix-catalog-list'),more=el('kiwix-catalog-more');
  if(reset){kiwixCatalogStart=0;list.replaceChildren();}
  if(kiwixCatalogBusy&&!reset)return;
  kiwixCatalogBusy=true;more.disabled=true;feedback.textContent=t('kiwixCatalogLoading');
  const params=new URLSearchParams({start:String(kiwixCatalogStart),count:'24'});
  const query=el('kiwix-catalog-query').value.trim();
  const lang=el('kiwix-catalog-language').value,category=el('kiwix-catalog-category').value;
  if(query)params.set('q',query);
  if(lang)params.set('lang',lang);
  if(category)params.set('category',category);
  try{
    const result=await api('extensions/kiwix/catalog?'+params.toString());
    if(generation!==kiwixCatalogGeneration)return;
    const items=result.items||[];const fragment=document.createDocumentFragment();
    for(const item of items)fragment.append(kiwixCatalogCard(item));
    list.append(fragment);kiwixCatalogStart+=24;kiwixCatalogMore=Boolean(result.more);
    more.hidden=!kiwixCatalogMore;
    feedback.textContent=result.cached?t('kiwixCatalogCached'):(!list.children.length?t('kiwixCatalogEmpty'):'');
  }catch(error){
    if(generation===kiwixCatalogGeneration){more.hidden=true;
      feedback.textContent=t('kiwixCatalogUnavailable')+' ('+error.message+')';}
  }finally{if(generation===kiwixCatalogGeneration){kiwixCatalogBusy=false;more.disabled=false;}}
}
window.pharosLoadKiwix=()=>{
  if(!kiwixCatalogFiltersInitialized){
    el('kiwix-catalog-language').value=lang==='it'?'ita':'eng';
    kiwixCatalogFiltersInitialized=true;
  }
  void loadKiwixPage(true);void loadKiwixCatalog(true);
};
el('kiwix-refresh').addEventListener('click',()=>window.pharosLoadKiwix());
el('kiwix-more').addEventListener('click',()=>loadKiwixPage(false));
el('kiwix-catalog-form').addEventListener('submit',event=>{event.preventDefault();void loadKiwixCatalog(true);});
el('kiwix-catalog-more').addEventListener('click',()=>loadKiwixCatalog(false));
el('kiwix-download-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const field=el('kiwix-download-url'),button=el('kiwix-download-start');
  const feedback=el('kiwix-download-feedback');button.disabled=true;
  try{
    await api('jobs/kiwix/zim',{method:'POST',body:JSON.stringify({url:field.value.trim()})});
    field.value='';feedback.textContent=t('kiwixDownloadStarted');
    if(window.pharosLoadJobs)await window.pharosLoadJobs();
  }catch(error){feedback.textContent=kiwixError(error);}
  finally{button.disabled=false;}
});
