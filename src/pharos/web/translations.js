"use strict";
// Data-only translation editor. Never evaluates files or injects translation HTML.
let pharosLocaleList=[];
let pharosLocaleReference={};
let pharosLocaleEditor=null;
let pharosLocaleDirty=false;
let pharosLocaleGeneration=0;
const localeNode=id=>document.getElementById(id);
const localeFeedback=message=>{localeNode('translations-feedback').textContent=message||''};

function localeUnsavedOK(){
  return !pharosLocaleDirty||window.confirm(t('translationsUnsaved'));
}
function localeCount(){
  const draft=pharosLocaleEditor;
  const total=Object.keys(pharosLocaleReference).length;
  const done=Object.values(draft?.messages||{}).filter(v=>typeof v==='string'&&v.trim()).length;
  localeNode('translations-count').textContent=
    t('translationsCount').replace('{done}',String(done)).replace('{total}',String(total));
}
function localeRenderEntries(){
  const container=localeNode('translations-entries');
  const draft=pharosLocaleEditor;
  container.replaceChildren();
  if(!draft)return;
  const search=localeNode('translations-search').value.toLocaleLowerCase();
  const onlyMissing=localeNode('translations-missing').checked;
  const fragment=document.createDocumentFragment();
  for(const [key,english] of Object.entries(pharosLocaleReference)){
    const translated=draft.messages[key]||'';
    if(onlyMissing&&translated.trim())continue;
    if(search&&!(key+' '+english+' '+translated).toLocaleLowerCase().includes(search))continue;
    const row=document.createElement('div');row.className='translation-entry';
    const info=document.createElement('div');info.className='translation-entry-reference';
    const code=document.createElement('code');code.textContent=key;
    const original=document.createElement('div');original.textContent=english;
    info.append(code,original);
    const label=document.createElement('label');label.className='translation-entry-field';
    const title=document.createElement('span');title.textContent=t('translationsTranslated');
    const value=document.createElement('textarea');value.rows=2;
    value.value=translated;value.maxLength=4000;value.disabled=Boolean(draft.builtin);
    value.setAttribute('aria-label',t('translationsTranslated')+' · '+key);
    value.addEventListener('input',()=>{
      if(!pharosLocaleEditor||pharosLocaleEditor!==draft)return;
      if(value.value)draft.messages[key]=value.value;
      else delete draft.messages[key];
      pharosLocaleDirty=true;localeCount();
    });
    label.append(title,value);row.append(info,label);fragment.append(row);
  }
  container.append(fragment);
}
function localeRender(){
  const draft=pharosLocaleEditor;
  localeNode('translations-editor').hidden=!draft;
  if(!draft){localeNode('translations-entries').replaceChildren();return;}
  localeNode('translations-name').value=draft.name;
  localeNode('translations-name').disabled=Boolean(draft.builtin);
  localeNode('translations-code-view').textContent=draft.code;
  localeNode('translations-save').hidden=Boolean(draft.builtin);
  localeNode('translations-delete').hidden=Boolean(draft.builtin)||!draft.revision;
  localeNode('translations-readonly').hidden=!draft.builtin;
  localeNode('translations-list').value=pharosLocaleList.some(x=>x.code===draft.code)?draft.code:'';
  localeCount();
  localeRenderEntries();
}
async function localeReloadList(){
  pharosLocaleList=await window.pharosLoadLocaleCatalog();
  const select=localeNode('translations-list');
  const previous=select.value;
  select.replaceChildren();
  for(const item of pharosLocaleList){
    const label=item.name+' ('+item.code+')'+(item.builtin?' · '+t('translationsReadOnly').split(':')[0]:'');
    select.append(new Option(label,item.code));
  }
  if(pharosLocaleList.some(i=>i.code===previous))select.value=previous;
}
async function localeOpen(code){
  const generation=++pharosLocaleGeneration;
  try{
    const result=await api('locales/'+encodeURIComponent(code)+'/edit');
    if(generation!==pharosLocaleGeneration)return;
    pharosLocaleEditor={code:result.code,name:result.name,
      builtin:result.builtin,revision:result.revision,messages:{...result.messages}};
    pharosLocaleReference=result.reference;
    pharosLocaleDirty=false;
    localeNode('translations-create-panel').hidden=true;
    localeFeedback('');
    localeRender();
  }catch(error){localeFeedback(t('translationsLoadError')+' '+error.message);}
}
window.pharosLoadTranslationEditor=async function(){
  try{
    const selected=pharosLocaleEditor?.code||appConfig?.language||'en';
    await localeReloadList();
    if(!Object.keys(pharosLocaleReference).length){
      const baseline=await api('locales/en/edit');
      pharosLocaleReference=baseline.reference;
    }
    if(pharosLocaleEditor&&!pharosLocaleEditor.builtin&&!pharosLocaleEditor.revision){
      localeRender();
    }else{
      const chosen=pharosLocaleList.some(i=>i.code===selected)?selected:'en';
      await localeOpen(chosen);
    }
  }catch(error){localeFeedback(t('translationsLoadError')+' '+error.message);}
};
localeNode('translations-list').addEventListener('change',async event=>{
  const requested=event.target.value;
  if(!localeUnsavedOK()){
    event.target.value=pharosLocaleEditor?.code||'en';
    return;
  }
  await localeOpen(requested);
});
localeNode('translations-new').addEventListener('click',()=>{
  if(!localeUnsavedOK())return;
  localeNode('translations-create-panel').hidden=false;
  localeFeedback('');
  localeNode('translations-new-code').focus();
});
localeNode('translations-create').addEventListener('click',()=>{
  const code=localeNode('translations-new-code').value.trim();
  const name=localeNode('translations-new-name').value.trim();
  if(!/^[a-z]{2}(?:-[A-Z]{2})?$/.test(code)||!name||name.length>60){
    localeFeedback(t('translationsMissingName'));return;
  }
  if(pharosLocaleList.some(i=>i.code===code)){
    localeFeedback(t('translationsCodeAlready'));return;
  }
  pharosLocaleEditor={code,name,revision:null,builtin:false,messages:{}};
  pharosLocaleDirty=true;
  localeNode('translations-create-panel').hidden=true;
  localeFeedback('');
  localeRender();
});
localeNode('translations-name').addEventListener('input',event=>{
  if(!pharosLocaleEditor||pharosLocaleEditor.builtin)return;
  pharosLocaleEditor.name=event.target.value;pharosLocaleDirty=true;
});
localeNode('translations-search').addEventListener('input',localeRenderEntries);
localeNode('translations-missing').addEventListener('change',localeRenderEntries);
localeNode('translations-save').addEventListener('click',async()=>{
  const draft=pharosLocaleEditor;if(!draft||draft.builtin)return;
  const button=localeNode('translations-save');button.disabled=true;
  try{
    const result=await api('locales/'+encodeURIComponent(draft.code),{
      method:'PUT',body:JSON.stringify({
        code:draft.code,name:draft.name,messages:draft.messages,revision:draft.revision
      })
    });
    draft.revision=result.revision;
    pharosLocaleDirty=false;
    await localeReloadList();
    if(appConfig?.language===draft.code)await window.pharosUseLanguage(draft.code,true);
    localeFeedback(t('translationsSaved'));
    localeRender();
  }catch(error){localeFeedback(t('translationsSaveError')+' '+error.message);}
  finally{button.disabled=false;}
});
localeNode('translations-delete').addEventListener('click',async()=>{
  const draft=pharosLocaleEditor;
  if(!draft||draft.builtin||!draft.revision)return;
  if(!window.confirm(t('translationsConfirmDelete').replace('{name}',draft.name)))return;
  try{
    await api('locales/'+encodeURIComponent(draft.code)+'?revision='+encodeURIComponent(draft.revision),
              {method:'DELETE'});
    pharosLocaleDirty=false;pharosLocaleEditor=null;
    await localeReloadList();await localeOpen('en');
    localeFeedback(t('translationsDeleted'));
  }catch(error){localeFeedback(t('translationsSaveError')+' '+error.message);}
});
localeNode('translations-back').addEventListener('click',()=>{
  if(localeUnsavedOK())window.pharosOpenView('home');
});
