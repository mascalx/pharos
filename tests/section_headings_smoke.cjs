const fs=require('node:fs'),path=require('node:path');
function verify(html,css,app,it,en){
 const check=(v,msg)=>{if(!v)throw Error(msg)};
 const expected={home:'homeWelcome',maps:'mapsHeading',chat:'chatHeading',knowledge:'knowledgeTitle',kiwix:'kiwixBooks',diagnostics:'healthTitle',notifications:'notificationsHeading',settings:'configHeading',translations:'translationsTitle'};
 const views=[...html.matchAll(/<section class="view(?: active)?" id="view-([^"]+)">([\s\S]*?)(?=<section class="view(?: active)?" id="view-|$)/g)];
 check(views.length===Object.keys(expected).length,'view count');
 const languages=[JSON.parse(it).messages,JSON.parse(en).messages];
 for(const [,id,body] of views){
   if(id==='home'){
     check(/^\s*<div class="home-intro"><h2><span data-i18n="homeWelcome">[^<]+<\/span><\/h2><\/div>/.test(body),'original Home welcome');
     check(languages.every(m=>m.homeWelcome),'localized Home welcome');
     continue;
   }
   const heading=body.match(/^\s*<div class="section-heading">\s*<div>\s*<h2 data-i18n="([^"]+)">[^<]+<\/h2>\s*<\/div>/);
   check(heading&&heading[1]===expected[id],id+' heading structure');
   check(languages.every(m=>m[id]&&m[heading[1]]&&m[id]!==m[heading[1]]),id+' translated distinct titles');
 }
 check((html.match(/id="page-title"/g)||[]).length===1,'one page title');
 check(!html.includes('data-i18n="mapsSub"'),'no maps description in header');
 check(!html.includes('id="config-language"')&&!app.includes("el('config-language')"),'no duplicate language or dangling DOM access');
 check(/\.showing-home\s+\.page-heading\s*\{[^}]*display\s*:\s*none/.test(css),'home page title hidden');
 check(/#view-home \.home-intro h2\{justify-content:center;text-align:center\}/.test(css),'original centered welcome styling');
 check(!/#view-chat\s+\.section-heading[^{]*\{[^}]*display\s*:\s*none/.test(css),'chat heading visible');
 check(css.includes('.shell .view>.section-heading h2'),'shared subtitle styling');
 for(const id of ['clear-chat','maps-refresh','refresh-kb','kiwix-refresh','jobs-refresh','translations-back','settings-form'])check(html.includes('id="'+id+'"'),id+' preserved');
 new Function(app);
}
const root=path.join(__dirname,'../src/pharos/web');
verify(...['index.html','layout-dev2.css','app.js','locales/it.json','locales/en.json'].map(p=>fs.readFileSync(path.join(root,p),'utf8')));
console.log('Section headings smoke passed');
