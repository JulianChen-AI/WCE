// Stored ZIP keeps media bytes intact and works from file:// without a server.
const enc = new TextEncoder();
function* archiveMessages(entries){for(const m of entries){yield m;if(m.forward)yield* archiveMessages(m.forward.messages);}}
const crcTable = Array.from({length:256}, (_,n)=>{for(let k=0;k<8;k++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
function crc32(b){let c=0xffffffff;for(const v of b)c=crcTable[(c^v)&255]^(c>>>8);return (c^0xffffffff)>>>0;}
const breathe=()=>new Promise(resolve=>setTimeout(resolve,0));
async function makeZip(files,onProgress=()=>{}){
 const chunks=[],central=[];let offset=0,centralSize=0;
 const total=Array.from(files.values()).reduce((n,b)=>n+b.length,0);let done=0;
 for(const [name,bytes] of files){
  let checksum=0xffffffff;
  for(let start=0;start<bytes.length;start+=1048576){
   for(const value of bytes.subarray(start,start+1048576))checksum=crcTable[(checksum^value)&255]^(checksum>>>8);
   done+=Math.min(1048576,bytes.length-start);onProgress(done,Math.max(total,1));await breathe();
  }
  const n=enc.encode(name),crc=(checksum^0xffffffff)>>>0,h=new Uint8Array(30),v=new DataView(h.buffer);
  v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint16(12,33,true);v.setUint32(14,crc,true);v.setUint32(18,bytes.length,true);v.setUint32(22,bytes.length,true);v.setUint16(26,n.length,true);
  chunks.push(h,n,bytes);
  const c=new Uint8Array(46),d=new DataView(c.buffer);d.setUint32(0,0x02014b50,true);d.setUint16(4,20,true);d.setUint16(6,20,true);d.setUint16(8,0x800,true);d.setUint16(14,33,true);d.setUint32(16,crc,true);d.setUint32(20,bytes.length,true);d.setUint32(24,bytes.length,true);d.setUint16(28,n.length,true);d.setUint32(42,offset,true);
  central.push(c,n);centralSize+=c.length+n.length;offset+=h.length+n.length+bytes.length;
 }
 const end=new Uint8Array(22),v=new DataView(end.buffer);v.setUint32(0,0x06054b50,true);v.setUint16(8,files.size,true);v.setUint16(10,files.size,true);v.setUint32(12,centralSize,true);v.setUint32(16,offset,true);
 return new Blob([...chunks,...central,end],{type:'application/zip'});
}
let assetsReady;
function loadExportAssets(){return assetsReady ||=new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='export-assets.js';s.onload=resolve;s.onerror=()=>{assetsReady=null;s.remove();reject(new Error('无法读取媒体导出资源，请保留完整档案目录'));};document.body.append(s);});}
document.getElementById('export-filtered').onclick=async()=>{
 const button=document.getElementById('export-filtered'),status=document.getElementById('export-status');
 const selected=structuredClone(filtered);if(!selected.length){status.textContent='没有符合条件的消息';return;}
 button.disabled=true;
 let meter=document.getElementById('export-progress');
 if(!meter){meter=document.createElement('progress');meter.id='export-progress';meter.max=100;meter.style.cssText='width:100%;height:14px';meter.setAttribute('aria-label','打包进度');status.after(meter);}
 meter.hidden=false;meter.removeAttribute('value');
 const started=performance.now();let phase='读取媒体资源',percent=0,stageStarted=started,stageFraction=0;
 const duration=s=>s<60?Math.max(1,Math.ceil(s))+' 秒':Math.ceil(s/60)+' 分钟';
 function show(){const elapsed=(performance.now()-started)/1000,stageElapsed=(performance.now()-stageStarted)/1000;
  const eta=stageFraction>0&&stageFraction<1&&stageElapsed>=1?duration(stageElapsed*(1-stageFraction)/stageFraction):'估算中';
  status.textContent=phase+' · '+Math.floor(percent)+'% · 已用 '+duration(elapsed)+' · 本阶段预计剩余 '+eta;
 }
 function update(label,value,fraction){if(phase!==label){phase=label;stageStarted=performance.now();}percent=value;stageFraction=fraction;meter.value=value;show();}
 show();const ticker=setInterval(show,500);await breathe();
 try{
  await loadExportAssets();
  update('准备消息',5,0);await breathe();
  const criteria={members:selectedValues('member'),start:$('date').value,end:$('date-end').value,types:selectedValues('type'),keyword:$('search').value,availability:$('availability')?.value||'',image_size:$('export-size').value};
  const counts={};for(const m of selected)counts[m.type]=(counts[m.type]||0)+1;
  const data={meta:{...meta,count:selected.length,counts,start:selected[0].time,end:selected.at(-1).time,export_filters:criteria,scope:'筛选导出'},messages:selected};
  const files=new Map(),paths=new Set();
  for(const m of archiveMessages(selected))for(const key of ['src','thumb','original']){const path=m.media?.[key];if(safePath(path))paths.add(path);}
  let mediaDone=0;
  for(const path of paths){update('处理媒体',5+70*mediaDone/paths.size,mediaDone/paths.size);await breathe();const b64=window.exportAssets[path];if(!b64)throw new Error('媒体导出资源缺失：'+path);const raw=atob(b64);let bytes=Uint8Array.from(raw,c=>c.charCodeAt(0)),target=path;
   if(criteria.image_size!=='original'&&Array.from(archiveMessages(selected)).some(m=>m.type==='image'&&m.media?.src===path)){
    const bitmap=await createImageBitmap(new Blob([bytes]));const edge=criteria.image_size==='light'?2048:1600,quality=criteria.image_size==='light'?0.85:0.8;
    const ratio=Math.min(1,edge/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));if(!blob)throw new Error('图片压缩失败');
    if(blob.size<bytes.length){bytes=new Uint8Array(await blob.arrayBuffer());target=path.replace(/\.[^.]+$/,'-compressed.jpg');for(const m of archiveMessages(selected))if(m.type==='image'&&m.media?.src===path){m.media.src=target;m.media.compressed=true;}}
   }
   files.set(target,bytes);mediaDone++;}
  update('生成聊天文件',75,0);await breathe();
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+esc(meta.peer)+'</title><style>body{max-width:1000px;margin:30px auto;font:16px sans-serif;padding:20px}article{border-bottom:1px solid #ddd;padding:16px 0;white-space:pre-wrap;overflow-wrap:anywhere}img,video{max-width:100%;max-height:700px}blockquote{color:#68766c}</style><h1>'+esc(meta.peer)+'</h1><p>筛选导出 · '+selected.length+' 条 · 北京时间</p>';
  const lines=[];
  for(const m of selected){
   const d=m.media||{},src=safePath(d.src);html+='<article><b>'+esc(m.sender)+'</b> · '+esc(m.time)+'<p>'+esc(m.text)+'</p>';
   lines.push('['+m.time+'] '+m.sender+' ['+m.type+'] '+m.text);
   if(src){if(['image','emoji'].includes(m.type))html+='<img src="'+esc(src)+'">';else if(['video','voice'].includes(m.type))html+='<'+(m.type==='video'?'video':'audio')+' controls src="'+esc(src)+'"></'+(m.type==='video'?'video':'audio')+'>';else html+='<a href="'+esc(src)+'" download>'+esc(d.filename||'附件')+'</a>';lines.push('媒体：'+src);}
   if(d.quote_text){html+='<blockquote>'+esc(d.quote_from)+'：'+esc(d.quote_text)+'</blockquote>';lines.push('引用：'+(d.quote_from||'')+'：'+d.quote_text);}
   if(m.forward){for(const inner of archiveMessages(m.forward.messages)){const path=safePath(inner.media?.src);if(inner.type==='image'&&path){html+='<p><img src="'+esc(path)+'" alt="转发图片"></p>';lines.push('图片：'+path);}if(inner.type==='file'&&path){html+='<p><a target="_blank" rel="noopener noreferrer" href="'+esc(path)+'">打开 '+esc(inner.media.filename||'附件')+'</a> · <a href="'+esc(path)+'" download="'+esc(inner.media.filename||'附件')+'">下载</a></p>';lines.push('附件：'+path);}}}
   if(m.transcription?.text){html+='<p>'+esc(m.transcription.text)+'</p>';lines.push('语音转写：'+m.transcription.text);}
   if(m.availability==='missing'){html+='<p>'+esc(m.missing_reason)+'</p>';lines.push('缺失：'+m.missing_reason);}
   html+='</article>';lines.push('');
  }
  files.set('index.html',enc.encode(html+'</html>'));files.set('chat.json',enc.encode(JSON.stringify(data,null,2)));files.set('聊天记录.txt',enc.encode(lines.join('\n')));files.set('筛选条件.json',enc.encode(JSON.stringify(criteria,null,2)));
  update('封装 ZIP',80,0);await breathe();
  const blob=await makeZip(files,(done,total)=>update('封装 ZIP',80+19*done/total,done/total)),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=meta.peer+'-筛选结果.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);clearInterval(ticker);meter.value=100;status.textContent='打包完成 100% · '+selected.length+' 条消息 · 用时 '+duration((performance.now()-started)/1000)+' · 已交给浏览器下载';
 }catch(e){clearInterval(ticker);meter.hidden=true;status.textContent='导出失败：'+e.message;}finally{clearInterval(ticker);button.disabled=false;}
};
