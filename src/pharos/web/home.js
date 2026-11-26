"use strict";
// Home preferences change presentation only. Features stay installed and routable.
const HOME_DEFAULT=['chat','knowledge','kiwix','settings','diagnostics','notifications','translations','maps'];
let homeSnapshot={order:[...HOME_DEFAULT],hidden:[]};
let homeDraft=null;
const homeGrid=document.getElementById('home-grid');
const homeFeedback=document.getElementById('home-edit-feedback');
const homeTiles=new Map([...homeGrid.querySelectorAll('[data-view]')].map(n=>[n.dataset.view,n]));
function renderHome(){
  const draft=homeDraft||homeSnapshot;
  document.body.classList.toggle('home-editing',Boolean(homeDraft));
  for(const id of draft.order){
    const tile=homeTiles.get(id);if(!tile)continue;
    const hidden=draft.hidden.includes(id);
    tile.hidden=homeDraft?false:hidden;
    tile.classList.toggle('home-tile-dimmed',!!homeDraft&&hidden);
    tile.draggable=!!homeDraft;
    tile.querySelector('.home-edit-controls')?.remove();
    if(homeDraft){
      const controls=document.createElement('div');controls.className='home-edit-controls';
      for(const [action,label] of [['up','homeUp'],['down','homeDown'],['toggle',hidden?'homeShow':'homeHide']]){
        const btn=document.createElement('button');btn.type='button';btn.className='outline-btn';
        btn.textContent=action==='up'?'↑':action==='down'?'↓':t(label);btn.title=t(label);
        btn.addEventListener('click',e=>{e.stopPropagation();
          if(action==='toggle')homeDraft.hidden=hidden?homeDraft.hidden.filter(v=>v!==id):[...homeDraft.hidden,id];
          else {const i=homeDraft.order.indexOf(id),j=i+(action==='up'?-1:1);
            if(j>=0&&j<homeDraft.order.length){[homeDraft.order[i],homeDraft.order[j]]=[homeDraft.order[j],homeDraft.order[i]];}}
          renderHome();});controls.append(btn);
      }
      tile.append(controls);
    }
    homeGrid.append(tile);
  }
  for(const name of ['edit','save','cancel','reset'])document.getElementById('home-'+name).hidden=(name==='edit'?!!homeDraft:!homeDraft);
}
window.pharosLoadHome=async function(){
  try{const data=await api('home');homeSnapshot={order:data.order,hidden:data.hidden};renderHome();}
  catch(e){console.warn('Home preferences unavailable',e.message);renderHome();}
};
document.getElementById('home-edit').addEventListener('click',()=>{homeDraft=structuredClone(homeSnapshot);homeFeedback.textContent='';renderHome();});
document.getElementById('home-cancel').addEventListener('click',()=>{homeDraft=null;homeFeedback.textContent='';renderHome();});
document.getElementById('home-reset').addEventListener('click',()=>{homeDraft={order:[...HOME_DEFAULT],hidden:[]};renderHome();});
document.getElementById('home-save').addEventListener('click',async()=>{
  try{const saved=await api('home',{method:'PUT',body:JSON.stringify(homeDraft)});
    homeSnapshot=saved;homeDraft=null;homeFeedback.textContent=t('homeSaved');renderHome();}
  catch(e){homeFeedback.textContent=t('homeSaveError')+': '+e.message;}
});
let moving='';
homeGrid.addEventListener('dragstart',e=>{if(!homeDraft)return;
  const tile=e.target.closest('.home-tile');if(!tile)return;
  moving=tile.dataset.view;e.dataTransfer?.setData('text/plain',moving);
  if(e.dataTransfer)e.dataTransfer.effectAllowed='move';
});
homeGrid.addEventListener('dragover',e=>{if(homeDraft&&e.target.closest('.home-tile'))e.preventDefault();});
homeGrid.addEventListener('drop',e=>{if(!homeDraft||!moving)return;
  const tile=e.target.closest('.home-tile');if(!tile)return;
  e.preventDefault();const destination=tile.dataset.view;
  const order=homeDraft.order.filter(x=>x!==moving);
  order.splice(order.indexOf(destination),0,moving);homeDraft.order=order;moving='';renderHome();
});

