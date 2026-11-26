"use strict";
// First-party Knowledge Base document viewer. Do not execute uploaded HTML;
// render text with textContent, and leave PDF to the browser's built-in viewer.
let pharosKBViewerTicket=0;
const pharosKBViewerEl=id=>document.getElementById(id);
function pharosKBDocURL(value){
  try{
    const url=new URL(value,window.location.href);
    if(url.origin!==window.location.origin ||
      !/^\/api\/kb\/[^/?#]+\/documents\/[^/?#]+\/file$/.test(url.pathname) ||
      url.search || url.hash)return null;
    return url.pathname;
  }catch(error){return null;}
}
function pharosCloseKBDocument(){
  pharosKBViewerTicket++;
  pharosKBViewerEl("kb-document-viewer").hidden=true;
  pharosKBViewerEl("kb-document-viewer-pdf").src="about:blank";
  pharosKBViewerEl("kb-document-viewer-pdf").hidden=true;
  pharosKBViewerEl("kb-document-viewer-text").textContent="";
  pharosKBViewerEl("kb-document-viewer-text").hidden=true;
  pharosKBViewerEl("kb-document-viewer-status").hidden=true;
  document.body.classList.remove("kb-document-viewer-active");
}
async function pharosOpenKBDocument(value,filename){
  const url=pharosKBDocURL(value);
  if(!url)return false;
  window.pharosCloseMap?.();
  window.pharosCloseKiwix?.();
  pharosCloseKBDocument();
  const ticket=pharosKBViewerTicket;
  const title=String(filename||"").slice(0,220);
  const panel=pharosKBViewerEl("kb-document-viewer");
  const message=pharosKBViewerEl("kb-document-viewer-status");
  const pdf=pharosKBViewerEl("kb-document-viewer-pdf");
  const text=pharosKBViewerEl("kb-document-viewer-text");
  const download=pharosKBViewerEl("kb-document-viewer-download");
  pharosKBViewerEl("kb-document-viewer-title").textContent=title;
  download.href=url;
  download.setAttribute("download",title||"documento");
  panel.hidden=false;
  document.body.classList.add("kb-document-viewer-active");
  pharosKBViewerEl("kb-document-viewer-close").focus();
  if(/\.pdf$/i.test(title)){
    pdf.hidden=false;
    pdf.src=url;
    return true;
  }
  if(/\.(?:txt|md|markdown|csv|json|log)$/i.test(title)){
    message.textContent=(typeof t==="function"?t("kbViewerLoading"):"Caricamento…");
    message.hidden=false;
    try{
      const response=await fetch(url,{cache:"no-store",credentials:"same-origin"});
      if(!response.ok)throw Error("HTTP "+response.status);
      const raw=await response.text();
      if(ticket!==pharosKBViewerTicket)return true;
      text.textContent=raw;
      text.hidden=false;
      message.hidden=true;
    }catch(error){
      if(ticket!==pharosKBViewerTicket)return true;
      message.textContent=(typeof t==="function"?t("kbViewerFailed"):"Impossibile aprire il documento.");
    }
    return true;
  }
  message.textContent=(typeof t==="function"?t("kbViewerUnsupported"):
    "Questo formato non è visualizzabile direttamente. Usa Scarica.");
  message.hidden=false;
  return true;
}
window.pharosOpenKBDocument=pharosOpenKBDocument;
window.pharosCloseKBDocument=pharosCloseKBDocument;
pharosKBViewerEl("kb-document-viewer-close").addEventListener("click",pharosCloseKBDocument);
document.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&!pharosKBViewerEl("kb-document-viewer").hidden)
    pharosCloseKBDocument();
});
