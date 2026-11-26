const fs=require('node:fs'),path=require('node:path');
async function exercise(source) {
  const nodes=new Map();
  function el(id){
    if(!nodes.has(id)){
      const classes=new Set();
      nodes.set(id,{hidden:false,disabled:false,textContent:'',href:'',
        classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},contains:name=>classes.has(name)},
        addEventListener(name,handler){this[name]=handler;}});
    }
    return nodes.get(id);
  }
  const check=(condition,message)=>{if(!condition)throw Error(message);};
  let state={state:'available',available:true,version:'0.7.0.dev27',remote_version:'0.7.0.dev28',
    installed_sha:'a'.repeat(40),remote_sha:'b'.repeat(40),last_checked:1,
    compare_url:'https://github.com/mascalx/pharos/compare/'+'a'.repeat(40)+'...main'};
  let status={status:'idle'},failure='background_jobs_active',posts=0,reloads=0;
  const window={location:{reload(){reloads++;}}}; // no confirm(): calling it is a regression
  const t=key=>key==='updatesError_background_jobs_active'?'Jobs active':key;
  async function api(path,options){
    if(path==='updates/installation')return status;
    if(path.startsWith('updates?')||path==='updates'||path==='updates/check')return state;
    if(path==='updates/start'){
      posts++;
      check(JSON.parse(options.body).target_sha===state.remote_sha,'target SHA');
      check(el('pharos-updates-panel').classList.contains('is-updating'),'progress during request');
      if(failure)throw Error('http_409: '+failure);
      return status={status:'queued',phase:'launching',target_sha:state.remote_sha};
    }
    throw Error(path);
  }
  new Function('window','el','api','t','lang','setInterval',source)(window,el,api,t,'en',()=>{});
  await window.pharosLoadUpdates();
  await window.pharosLoadInstallation();
  check(el('updates-installed').textContent===state.version,'installed version');
  check(el('updates-remote').textContent===state.remote_version,'remote version');
  check(el('updates-status').hidden,'ordinary status hidden');
  check(!el('updates-compare').hidden,'compare visible');
  await el('updates-install').click();
  await window.pharosLoadInstallation();
  check(posts===1,'direct request without confirmation');
  check(el('updates-install-status').textContent==='Jobs active','localized block');
  check(!el('updates-install').hidden,'retry available');
  failure='';
  await el('updates-install').click();
  await window.pharosLoadInstallation();
  check(posts===2,'second request');
  check(el('pharos-updates-panel').classList.contains('is-updating'),'progress mode');
  check(el('updates-install-status').textContent==='updatesInstallRunning','only progress label');
  status={status:'completed'};
  await window.pharosLoadInstallation();
  await window.pharosLoadInstallation();
  check(reloads===1,'reload once');
  check(el('updates-install-status').hidden,'no completed message');
  state={...state,state:'current',available:false,remote_version:state.version};
  await window.pharosLoadUpdates();
  check(el('updates-install').hidden&&el('updates-compare').hidden,'current version controls');
}
exercise(fs.readFileSync(path.join(__dirname,'../src/pharos/web/updates.js'),'utf8')).then(()=>console.log('Updates UI smoke passed')).catch(error=>{console.error(error);process.exitCode=1;});
