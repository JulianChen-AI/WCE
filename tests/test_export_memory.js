const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.argv[2],out=process.argv[3];
async function check(name){
 const payload=fs.readFileSync(path.join(out,'media/file/'+name+'.bin'));
 const loaded=[];let zipBlob;
 class El{constructor(tag){this.tag=tag;this.style={};}after(){}removeAttribute(){}setAttribute(){}remove(){}click(){}}
 const controls={};for(const id of ['export-filtered','export-status','export-progress','date','date-end','search','availability','export-size'])controls[id]=new El(id);
 for(const id of ['date','date-end','search','availability'])controls[id].value='';controls['export-size'].value='original';
 const window={};
 const context=vm.createContext({TextEncoder,Blob,Uint8Array,DataView,performance,structuredClone,atob,window,filtered:[{type:'forward',sender:'fixture',time:'2026-11-01 12:00:00',text:'forward',media:{},forward:{messages:[{type:'file',text:'fixture',media:{src:'media/file/'+name+'.bin',filename:name+'.bin'}}]}}],meta:{peer:'fixture'},selectedValues:()=>[],$:id=>controls[id],safePath:p=>/^media\/[a-z_]+\/[a-zA-Z0-9_.-]+$/.test(p||'')?p:null,document:{getElementById:id=>controls[id],createElement:tag=>new El(tag),body:{append:s=>{loaded.push(s.src);vm.runInContext(fs.readFileSync(path.join(out,s.src),'utf8'),context);s.onload();}}},URL:{createObjectURL:b=>{zipBlob=b;return 'blob:fixture';},revokeObjectURL(){}},setInterval:()=>1,clearInterval(){},setTimeout:f=>f()});
 vm.runInContext(fs.readFileSync(path.join(root,'scripts/multimodal/filtered-export.js'),'utf8'),context);
 await controls['export-filtered'].onclick();assert(zipBlob,controls['export-status'].textContent);
 const expected=window.exportAssets['media/file/'+name+'.bin'].chunks;
 assert.deepEqual(loaded,['export-assets.js',...expected]);assert(!window.exportAssetChunk,'chunk global must be released');
 const bytes=new Uint8Array(await zipBlob.arrayBuffer()),v=new DataView(bytes.buffer),entries=new Map();let i=0;
 while(v.getUint32(i,true)===0x04034b50){const size=v.getUint32(i+18,true),n=v.getUint16(i+26,true),extra=v.getUint16(i+28,true);const entry=new TextDecoder().decode(bytes.slice(i+30,i+30+n)),start=i+30+n+extra;entries.set(entry,bytes.slice(start,start+size));i=start+size;}
 assert.deepEqual(Buffer.from(entries.get('media/file/'+name+'.bin')),payload);
 assert(!entries.has('media/file/'+(name==='small'?'large':'small')+'.bin'));
 assert(new TextDecoder().decode(entries.get('index.html')).includes('media/file/'+name+'.bin'));
 console.log(name+': selected_bytes='+payload.length+' chunks='+expected.length+' exact ZIP bytes; no unrelated media loaded');
}
(async()=>{await check('small');await check('large');})().catch(e=>{console.error(e);process.exitCode=1;});
