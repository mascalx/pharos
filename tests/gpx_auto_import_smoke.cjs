"use strict";
// Verify one-step GPX import and that an imported file receives its own name.
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
class Node{
 constructor(){this.children=[];this.events={};this.hidden=false;this.files=[];this.value="";this.disabled=false;this.textContent="";}
 click(){this.clicked=true;}addEventListener(k,f){this.events[k]=f;}setAttribute(){}replaceChildren(...c){this.children=c;}append(...c){this.children.push(...c);}
}
class FormDataMock{
 constructor(){this.fields={};}
 append(k,v){this.fields[k]=v;}
}
const nodes=new Map(),document={
 documentElement:{lang:"it"},
 getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},
 createElement(){return new Node();}
};
let uploads=0,named="";
const api=async(endpoint,opt)=>{
 if(endpoint==="maps/gpx"&&opt?.method==="POST"){
   uploads++;named=opt.body.fields.name;return {};
 }
 if(endpoint==="maps/gpx")return {items:[]};
 throw Error("unexpected API "+endpoint);
};
const globals={document,api,FormData:FormDataMock,console,t:key=>key,window:{confirm:()=>true}};
vm.createContext(globals);
vm.runInContext(fs.readFileSync(path.join(__dirname,
 "../src/pharos/web/maps_gpx.js"),"utf8"),globals);
(async()=>{
 const field=nodes.get("maps-gpx-file");
 const chooser=nodes.get("maps-gpx-choose");
 chooser.events.click();assert(field.clicked,"Localized chooser opens native file dialog");
 field.files=[{name:"Escursione nella Valle.GPX",size:140}];
 await field.events.change();
 // The change listener intentionally returns immediately; wait for
 // the actual asynchronous import and inventory refresh to settle.
 for(let i=0;i<12&&field.disabled;i++)await Promise.resolve();
 assert.equal(uploads,1,"Selecting a file should upload exactly once");
 assert.equal(named,"Escursione nella Valle","Use filename without extension as track name");
 assert.equal(chooser.disabled,false,"Localized button reusable");
 assert.equal(field.disabled,false,"File picker must be reusable after upload");
 assert.equal(field.value,"");
 const form=nodes.get("maps-gpx-import-form");
 assert(form.events.submit,"Keyboard-accessible form fallback retained");
 const html=fs.readFileSync(path.join(__dirname,"../src/pharos/web/index.html"),"utf8");
 assert(!html.includes('id="maps-gpx-import"'),"Manual import button should be absent");
 assert(!html.includes('id="maps-gpx-name"'),"Manual title field should be absent");
 const css=fs.readFileSync(path.join(__dirname,"../src/pharos/web/layout-dev2.css"),"utf8");
 assert(css.includes(".maps-gpx-actions button:last-child{margin-left:auto}"));
 console.log("GPX automatic import smoke: PASS");
})().catch(e=>{console.error(e);process.exitCode=1;});
