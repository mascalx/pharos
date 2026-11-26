"use strict";
// Dependency-free smoke for active KB job notifications and per-KB upload status.
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const assert=require("node:assert/strict");
const root=path.join(__dirname,"../src/pharos/web");
const jobsJS=fs.readFileSync(path.join(root,"jobs.js"),"utf8");
const kbJS=fs.readFileSync(path.join(root,"knowledge.js"),"utf8");

class FakeElement {
  constructor(tag="div"){
    this.tagName=tag;
    this.children=[];
    this.textContent="";
    this.className="";
    this.hidden=true;
    this.disabled=false;
    this.value=0;
    this.events={};
    this.classes=new Set();
    this.classList={
      add:cls=>this.classes.add(cls),
      remove:cls=>this.classes.delete(cls),
      toggle:(cls,enabled)=>enabled?this.classes.add(cls):this.classes.delete(cls),
      contains:cls=>this.classes.has(cls),
    };
  }
  append(...elements){
    for(const element of elements){
      if(element.tagName==="#fragment")this.children.push(...element.children);
      else this.children.push(element);
    }
  }
  replaceChildren(...elements){this.children=[];this.append(...elements);}
  addEventListener(type,callback){this.events[type]=callback;}
  setAttribute(){}
}
const fakeNodes=()=> {
  const nodes=new Map();
  const el=id=>{
    if(!nodes.has(id))nodes.set(id,new FakeElement());
    return nodes.get(id);
  };
  return {nodes,el};
};

async function testSilentJobs(){
  const {el}=fakeNodes();
  const document={
    createElement:tag=>new FakeElement(tag),
    createDocumentFragment:()=>new FakeElement("#fragment"),
  };
  let jobs=[
    {id:"ing1",kind:"ingest-document",status:"running",label:"Manual.pdf",
      detail:"embedding: 280/446",progress:62,read_at:0,target_view:"knowledge"},
    {id:"re1",kind:"reindex-rag",status:"running",label:"Knowledge",
      detail:"reindexing",progress:35,read_at:0,target_view:"knowledge"},
    {id:"zim1",kind:"download-zim",status:"running",label:"Atlas.zim",
      detail:"downloading",progress:25,read_at:0,target_view:"kiwix"},
    {id:"map1",kind:"download-map",status:"queued",label:"Atlas.pmtiles",
      detail:"queued",progress:0,read_at:0,target_view:"maps"},
  ];
  const calls=[],navigation=[],sync=[];
  const api=async(route)=>{
    calls.push(route);
    if(route==="jobs")return {items:jobs};
    throw Error("Unexpected API call: "+route);
  };
  const window={pharosUpdateAvailable:false,
    pharosSyncKBUploadNotices:items=>sync.push(items.length)};
  const context=vm.createContext({document,window,el,api,t:key=>key,
    showView:where=>navigation.push(where),console,setInterval:()=>{}});
  vm.runInContext(jobsJS,context,{filename:"jobs.js"});
  await new Promise(resolve=>setImmediate(resolve));
  await window.pharosLoadJobs();

  let cards=el("job-list").children;
  assert.equal(cards.length,4,"All active RAG and download jobs must be listed");
  assert(cards.every(card=>card.className.includes("job-notification-active")));
  assert.equal(el("notification-indicator").hidden,true,
    "Activity-only jobs must remain silent in the notification indicator");
  cards[0].events.click();
  assert.equal(navigation.at(-1),"knowledge");
  cards[2].events.click();
  assert.equal(navigation.at(-1),"kiwix");
  cards[3].events.click();
  assert.equal(navigation.at(-1),"maps");
  assert.equal(calls.filter(url=>url.includes("/read")).length,0,
    "Active jobs cannot be acknowledged as completed notifications");
  assert(sync.length>0,"KB upload acknowledgements should follow the job snapshot");

  jobs=[{...jobs[0],status:"completed",progress:100}];
  await window.pharosLoadJobs();
  cards=el("job-list").children;
  assert.equal(cards.length,1,"Completed job should be a single unread notification");
  assert.equal(el("notification-indicator").hidden,false,
    "Unread terminal jobs should light the indicator");
}

async function testUploadNotice(){
  const {el}=fakeNodes();
  const window={pharosLoadJobs:async()=>{}};
  const requests=[];
  const api=async(path,options)=>{
    requests.push(path);
    assert.equal(options.method,"POST");
    return {id:"job42",status:"queued"};
  };
  class FormDataFake {append(){}}
  let reloads=0;
  // Only ingest functions are evaluated; other KB UI controls are unrelated.
  const untilRenderer=kbJS.slice(0,kbJS.indexOf("const make = "));
  assert(untilRenderer.includes("window.pharosSyncKBUploadNotices"));
  const context=vm.createContext({
    window,el,api,t:key=>key,FormData:FormDataFake,
    loadKnowledge:async()=>{reloads++;},
  });
  vm.runInContext(untilRenderer,context,{filename:"knowledge.js"});
  const input={files:[{name:"Manual.pdf"}],disabled:false};
  const progress={hidden:true,textContent:""};
  const button={disabled:false};
  await vm.runInContext("uploadKBFile('kb42',input,progress,button)",
    Object.assign(context,{input,progress,button}));
  assert.deepEqual(requests,["kb/kb42/documents"]);
  assert.match(el("kb-upload-feedback-kb42").textContent,/Manual.pdf/);
  assert.equal(el("kb-feedback").textContent,"",
    "Upload must never place its message in the new-KB form");
  assert.equal(reloads,1);
  window.pharosSyncKBUploadNotices([{id:"job42",status:"running"}]);
  assert(el("kb-upload-feedback-kb42").textContent.length>0);
  window.pharosSyncKBUploadNotices([{id:"job42",status:"completed"}]);
  assert.equal(el("kb-upload-feedback-kb42").textContent,"");
  assert.equal(el("kb-upload-feedback-kb42").hidden,true);
  assert(kbJS.includes("card.append(upload,uploadNotice,docList,zimSection)"),
    "The per-KB status must render within the KB card");
}
(async()=>{
  await testSilentJobs();
  await testUploadNotice();
  console.log("KB job notifications and per-KB upload status smoke: PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
