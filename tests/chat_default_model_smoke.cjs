"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const app=fs.readFileSync(path.join(__dirname,"../src/pharos/web/app.js"),"utf8");
const settings=fs.readFileSync(path.join(__dirname,"../src/pharos/web/settings.js"),"utf8");
const begin=app.indexOf("const chatModelResidency = new Map();");
const end=app.indexOf("function groupChatSources(",begin);
assert(begin>=0&&end>begin,"Chat model selection functions available");
assert(!settings.includes("if(section==='ai'&&key==='default_model')continue"),
  "Default chat model must be visible in settings");
assert(settings.includes("void loadSettingsModels()"),"Settings dropdown must be populated");
assert(settings.includes("appConfig.ai.default_model=values.ai.default_model"),
  "Saving default must update live Chat configuration");
assert(settings.includes("catalog.models||[]"),
  "Default picker should include installed Tiiny inventory, not just NPU-loaded models");
class OptionFake{
  constructor(label,value){this.textContent=label;this.value=value;this.dataset={};}
}
class SelectFake{
  constructor(){this.options=[];this._value="";this.disabled=false;this.dataset={};}
  replaceChildren(...options){this.options=[...options];this._value=options[0]?.value||"";}
  add(option,index){
    if(Number.isInteger(index))this.options.splice(index,0,option);
    else this.options.push(option);
    if(this.options.length===1)this._value=option.value;
  }
  set value(value){this._value=this.options.some(option=>option.value===value)?value:"";}
  get value(){return this._value;}
}
const nodes=new Map();
nodes.set("model-select",new SelectFake());
for(const id of ["chat-npu-action","chat-thinking-label","chat-thinking",
  "chat-feedback","chat-model-legend","send-button"]){
  nodes.set(id,{value:"auto",textContent:"",hidden:true,disabled:false,dataset:{}});
}
const config={ai:{default_model:"tiiny/qwen-chat"}};
let catalog={mode:"tiiny_direct",complete:true,models:[
  {id:"tiiny/first-chat",kind:"chat",loaded:true},
  {id:"tiiny/qwen-chat",kind:"chat",loaded:false}
]};
const context={
  el:id=>nodes.get(id),Option:OptionFake,t:k=>k,appConfig:config,busy:false,
  console,api:async endpoint=>endpoint==="ai/catalog"?catalog:
    endpoint==="models"?{models:catalog.models.map(x=>x.id)}:{}
};
vm.createContext(context);
vm.runInContext(app.slice(begin,end),context);
(async()=>{
  context.showConfiguredChatModelImmediately();
  assert.equal(nodes.get("model-select").value,"tiiny/qwen-chat",
    "Configured Chat model must be usable before slow Tiiny discovery");
  assert.equal(nodes.get("send-button").disabled,false,
    "Configured Chat model must enable Send immediately");
  await context.loadModels();
  assert.equal(nodes.get("model-select").value,"tiiny/qwen-chat",
    "Configured default must win over first loaded model on startup");
  assert(vm.runInContext("chatCatalogLastRefresh",context) > 0,
    "Chat inventory should store the last refresh for stale invalidation");
  assert.equal(nodes.get("send-button").disabled,false);

  config.ai.default_model="tiiny/not-installed";
  await context.loadModels(true);
  assert.equal(nodes.get("model-select").value,"",
    "Missing configured default must never silently select first available model");
  assert.equal(nodes.get("send-button").disabled,true);
  assert(nodes.get("chat-feedback").textContent.includes("chatDefaultUnavailable"));

  config.ai.default_model="tiiny/qwen-chat";
  await context.loadModels(true);
  nodes.get("model-select").value="tiiny/first-chat";
  context.updateChatModelLabels();
  await context.loadModels();
  assert.equal(nodes.get("model-select").value,"tiiny/first-chat",
    "Manual Chat choice persists across refresh within same session");

  config.ai.default_model="";
  await context.loadModels(true);
  assert.equal(nodes.get("model-select").value,"");
  assert.equal(nodes.get("send-button").disabled,true);
  nodes.get("model-select").value="tiiny/first-chat";
  context.updateChatModelLabels();
  assert.equal(nodes.get("send-button").disabled,false,
    "Send must become enabled when a model is chosen manually");
  console.log("Chat default model and settings UI regression: PASS");
})().catch(e=>{console.error(e);process.exitCode=1;});
