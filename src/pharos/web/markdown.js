/* PHAROS safe local Markdown subset. Construct DOM nodes, never parse HTML. */
(function(global){
  "use strict";
  const text=(parent,value)=>parent.append(document.createTextNode(value));
  function node(parent,tag,value){
    const n=document.createElement(tag);
    if(value!==undefined)n.textContent=value;
    parent.append(n);return n;
  }
  function safeLink(value){
    if(!/^https?:\/\//i.test(value)||/[\u0000-\u0020\u007f\\]/.test(value))return null;
    try{
      const url=new URL(value);
      return (url.protocol==="https:"||url.protocol==="http:")&&!url.username&&!url.password?url.href:null;
    }catch(_){return null;}
  }
  function inline(parent,source,depth=0){
    if(depth>=6||source.length>8192){text(parent,source);return;}
    // Code, links, emphasis and escapes. Images are deliberately literal.
    const pattern=/\\([\\\x60*_[\]~])|(\x60+)([^\n]*?)\2|(!?)\[([^\]\n]+)\]\(([^)\n]+)\)|(\*\*|__)(?=\S)(.+?)(?<=\S)\7|(\*|_)(?=\S)(.+?)(?<=\S)\9/g;
    let pos=0,m;
    while((m=pattern.exec(source))!==null){
      text(parent,source.slice(pos,m.index));
      if(m[1])text(parent,m[1]);
      else if(m[2])node(parent,"code",m[3]);
      else if(m[4]!==undefined){
        const destination=m[6];
        const checked=!m[4]&&safeLink(destination);
        if(checked){const a=node(parent,"a",m[5]);a.href=checked;a.target="_blank";a.rel="noopener noreferrer";}
        else text(parent,m[0]);
      }else if(m[7])inline(node(parent,"strong"),m[8],depth+1);
      else if(m[9])inline(node(parent,"em"),m[10],depth+1);
      else text(parent,m[0]);
      pos=pattern.lastIndex;
    }
    text(parent,source.slice(pos));
  }
  const cells=line=>line.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(v=>v.trim());
  const separator=line=>line.includes("|")&&cells(line).every(v=>/^:?-{3,}:?$/.test(v));
  const heading=line=>/^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$/.exec(line);
  const list=line=>/^ {0,3}([-+*]|\d{1,9}[.)])[ \t]+(.+)$/.exec(line);
  const fence=line=>/^ {0,3}(\x60{3,}|~{3,})(.*)$/.exec(line);
  const rule=line=>/^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line);
  function render(parent,value){
    const source=String(value??"");
    parent.replaceChildren();
    // Bound parser work; preserve the complete original text on fallback.
    if(source.length>100000){parent.classList.remove("message-markdown");parent.textContent=source;return;}
    const lines=source.replace(/\r\n?/g,"\n").split("\n");
    let i=0;
    const tableAt=j=>j+1<lines.length&&lines[j].length<=8192&&lines[j+1].length<=8192&&lines[j].includes("|")&&separator(lines[j+1])&&cells(lines[j]).length<=40&&cells(lines[j]).length===cells(lines[j+1]).length;
    const blockAt=j=>heading(lines[j])||list(lines[j])||fence(lines[j])||rule(lines[j])||/^\s*>/.test(lines[j])||tableAt(j);
    while(i<lines.length){
      const line=lines[i];
      if(!line.trim()){i++;continue;}
      const f=fence(line);
      if(f){
        const code=[];i++;
        while(i<lines.length){
          const closing=fence(lines[i]);
          if(closing&&closing[1][0]===f[1][0]&&closing[1].length>=f[1].length&&!closing[2].trim()){i++;break;}
          code.push(lines[i++]);
        }
        node(node(parent,"pre"),"code",code.join("\n"));continue;
      }
      const h=heading(line);
      if(h){inline(node(parent,"h"+Math.min(6,Number(h[1].length)+2)),h[2]);i++;continue;}
      if(rule(line)){node(parent,"hr");i++;continue;}
      if(tableAt(i)){
        const wrap=node(parent,"div");wrap.className="markdown-table";
        const table=node(wrap,"table"),head=node(node(table,"thead"),"tr");
        const header=cells(line);for(const v of header)inline(node(head,"th"),v);
        i+=2;const body=node(table,"tbody");
        while(i<lines.length&&lines[i].trim()&&lines[i].includes("|")&&!fence(lines[i])){
          const row=node(body,"tr"),values=cells(lines[i++]);
          for(let k=0;k<header.length;k++)inline(node(row,"td"),values[k]??"");
        }
        continue;
      }
      const item=list(line);
      if(item){
        const ordered=/^\d/.test(item[1]),out=node(parent,ordered?"ol":"ul");
        if(ordered)out.start=parseInt(item[1],10);
        while(i<lines.length){
          const entry=list(lines[i]);
          if(!entry||/^\d/.test(entry[1])!==ordered)break;
          inline(node(out,"li"),entry[2]);i++;
        }
        continue;
      }
      if(/^\s*>/.test(line)){
        const out=node(parent,"blockquote");
        while(i<lines.length&&/^\s*>/.test(lines[i])){
          inline(node(out,"p"),lines[i++].replace(/^\s*>\s?/,""));
        }
        continue;
      }
      const para=node(parent,"p");
      inline(para,line);i++;
      while(i<lines.length&&lines[i].trim()&&!blockAt(i)){
        node(para,"br");inline(para,lines[i++]);
      }
    }
  }
  global.pharosRenderMarkdown=render;
})(window);
