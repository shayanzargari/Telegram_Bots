'use strict';
const $=id=>document.getElementById(id);
const paths={
 notebook:'M5 3h14v18H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3ZM7 3v15M3 18h16M11 7h4M11 11h3',
 letters:'m3 17 4-10 4 10M4.5 13h5M15 7h3a3 3 0 0 1 0 6h-3m0-6v10h3a2 2 0 0 0 0-4',
 spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
 question:'M9 9a3 3 0 0 1 6 1c0 2-3 2-3 4m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 compass:'m16 8-3 5-5 3 3-5ZM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 link:'m10 13 4-4m-6 7-1 1a4 4 0 0 1-6-5l4-4a4 4 0 0 1 6 0m2-1 1-1a4 4 0 0 1 6 5l-4 4a4 4 0 0 1-6 0',
 pen:'m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-4L4 15Z',
 arrow:'M4 12h16m-7-7 7 7-7 7',flower:'M12 12c-9-6-9 6 0 0s-3-9 0 0 9 9 0 0-9-3 0 0 9-9 0 0M12 12v9',
 flow:'M4 6h10a4 4 0 0 1 0 8H8a4 4 0 0 0 0 8m-4-16 3-3M4 6l3 3',clock:'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 sliders:'M4 7h8m4 0h4M4 17h3m4 0h9M12 4v6M7 14v6',connect:'M4 4h5v5H4ZM15 15h5v5h-5ZM7 9v8h8M15 7h5m-2.5-2.5v5',
 folder:'M3 7V5h6l2 2h10v13H3Z',search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 refresh:'M20 7v5h-5M4 17v-5h5M5.3 7a8 8 0 0 1 13-1L20 9M4 15l1.7 3a8 8 0 0 0 13-1',
 undo:'M4 9h10a6 6 0 0 1 0 12M4 9l5-5M4 9l5 5',save:'M4 3h13l4 4v14H3V3ZM7 3v6h10V3M7 21v-8h10v8',close:'m6 6 12 12M6 18 18 6'
};
function icon(name){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.notebook}"/></svg>`;}
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
const token=document.querySelector('meta[name="app-token"]').content;
const frenchAudio=new FrenchAudio(token,$('speech-status'),$('stop-speech'),$('speech-speed'),$('speech-voice'),showError);
const columns=new NotesColumns($('editor'),$('columns-editor'),$('columns-rows'),$('editor-view'),frenchAudio,{add:$('add-row'),undo:$('undo-remove')});
$('editor-view').querySelectorAll('button').forEach(button=>button.onclick=()=>columns.setMode(button.dataset.view));
document.querySelector('.skip-link').onclick=event=>{event.preventDefault();columns.focusRange($('editor').selectionStart,$('editor').selectionEnd);};
const state={file:null,base:null,categories:[],drafts:{},legacy:{},storageKey:'',busy:false,loading:false,sequence:0,searchSequence:0,request:null,draftTimer:null,draftQueue:Promise.resolve(),pending:new Map(),toastTimer:null};
const normalized=text=>text.replace(/\r\n|\r/g,'\n');
const oldNotes={'Overall Examples.txt':'Indicatif.txt','passe compose.txt':'Indicatif.txt'};
function recoverOldDrafts(){
 if(!state.categories.some(item=>item.name==='Indicatif.txt'))return;
 const key=state.storageKey+'.recovered';let recovered={};try{recovered=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
 for(const name of Object.keys(oldNotes)){
  if(state.drafts[name]){recovered[name]=state.drafts[name];delete state.drafts[name];}
  if(state.legacy[name]){recovered[name+' (append)']={text:state.legacy[name]};delete state.legacy[name];}
 }
 if(!Object.keys(recovered).length)return;
 try{localStorage.setItem(key,JSON.stringify(recovered));localStorage.setItem(state.legacyKey,JSON.stringify(state.legacy));localDrafts();}catch{showError('Please download the recovered drafts before closing this window.');}
 const banner=$('recovered-drafts');if(banner)banner.hidden=false;
 if($('download-recovered-drafts'))$('download-recovered-drafts').onclick=()=>{const link=document.createElement('a');const url=URL.createObjectURL(new Blob([JSON.stringify(recovered,null,2)],{type:'application/json;charset=utf-8'}));link.href=url;link.download='French Notes recovered drafts.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
}
async function api(route,body){
 const response=await fetch('/api/'+route,{method:body===undefined?'GET':'POST',headers:{'X-Notes-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok){const error=new Error(data.error||'Could not complete the request.');error.code=data.code;throw error;}return data;
}
function showError(message=''){$('error').textContent=message;$('error').hidden=!message;}
function toast(message){clearTimeout(state.toastTimer);$('toast-message').textContent=message;$('toast').hidden=false;state.toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
function dirty(){return !!state.file&&$('editor').value!==state.file.text;}
function localDrafts(){try{localStorage.setItem(state.storageKey,JSON.stringify(state.drafts));}catch{}}
function browserValues(){const values={};try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(key.startsWith('french-notes.'))values[key]=localStorage.getItem(key);}}catch{}return values;}
function keepBrowserState(){return api('browser-state',{values:browserValues()});}
for(const id of ['speech-speed','speech-voice'])$(id).addEventListener('change',()=>keepBrowserState().catch(()=>showError('Could not keep your voice preferences. Your notes are still here.')));
function markTabs(){document.querySelectorAll('.category').forEach(button=>{const name=button.dataset.name,active=name===state.file?.name;button.classList.toggle('active',active);button.disabled=state.busy;if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');button.querySelector('.dirty-dot').hidden=!state.drafts[name];});}
function controls(){
 const blocked=state.busy||state.loading,changed=dirty();
 $('editor').disabled=blocked||!state.file;$('save').disabled=blocked||!changed;$('refresh').disabled=blocked||!state.file;
 columns.setDisabled(blocked||!state.file);
 $('undo-save').disabled=blocked||changed||!state.file?.undo_id;
 $('save-status').textContent=state.loading?'Opening…':state.busy?'Saving…':changed?'Unsaved changes':'Saved';
 $('save-status').classList.toggle('dirty',changed);markTabs();cursor();
}
function cursor(){const editor=$('editor'),before=editor.value.slice(0,editor.selectionStart);const line=before.split('\n').length,column=Array.from(before.slice(before.lastIndexOf('\n')+1)).length+1;$('cursor-position').textContent=`Line ${line}, Column ${column}`;}
function capture(){
 if(!state.file||state.loading)return;
 const name=state.file.name;
 if(dirty()){state.drafts[name]={text:$('editor').value,revision:state.base.revision,section_revision:state.base.section_revision};}
 else delete state.drafts[name];
 state.pending.set(name,state.drafts[name]||null);localDrafts();controls();
 clearTimeout(state.draftTimer);state.draftTimer=setTimeout(()=>flushDrafts().catch(e=>showError('Could not keep the draft: '+e.message)),200);
}
function flushDrafts(){
 clearTimeout(state.draftTimer);const pending=[...state.pending.entries()];state.pending.clear();
 state.draftQueue=state.draftQueue.catch(()=>{}).then(async()=>{for(const [name,draft] of pending){try{await api('draft',{name,draft});}catch(e){if(!state.pending.has(name))state.pending.set(name,draft);throw e;}}await keepBrowserState();});
 return state.draftQueue;
}
function renderCategories(){
 $('categories').replaceChildren();
 state.categories.forEach(category=>{const button=document.createElement('button');button.type='button';button.className='category';button.dataset.name=category.name;button.innerHTML=icon(category.icon);const label=document.createElement('span');label.textContent=category.title;const dot=document.createElement('span');dot.className='dirty-dot';dot.setAttribute('aria-hidden','true');button.append(label,dot);button.onclick=()=>selectFile(category.name);$('categories').append(button);});
 markTabs();
}
function migrateLegacy(file){
 const key=file.name==='@additional'?'wrods, verbs.txt':file.name;
 const previous=state.legacy[key];if(!previous?.trim()||state.drafts[file.name])return;
 state.drafts[file.name]={text:file.text+(file.text.endsWith('\n')?'':'\n')+previous,revision:file.revision,section_revision:file.section_revision};
 state.pending.set(file.name,state.drafts[file.name]);delete state.legacy[key];localDrafts();
 // Retain the original append draft in storage until the new draft is durable.
 flushDrafts().then(()=>{try{localStorage.setItem(state.legacyKey,JSON.stringify(state.legacy));}catch{}}).catch(e=>showError(e.message));
}
function showFile(file,line=null,query=''){
 state.file=file;migrateLegacy(file);const draft=state.drafts[file.name];
 state.base=draft||file;$('editor').value=draft?draft.text:file.text;
 columns.load($('editor').value,file.name);$('columns-editor').scrollTop=0;
 if($('editor').value===file.text){delete state.drafts[file.name];state.base=file;localDrafts();}
 $('page-title').textContent=file.title;document.title=file.title+' · French Notes';
 $('editor').setAttribute('aria-label','Edit '+file.title);$('editor').scrollTop=0;
 if(draft&&draft.section_revision!==file.section_revision)showError('This note changed outside the editor. Your unsaved draft is shown. Refresh to review the file before saving.');
 if(line!==null){const lines=$('editor').value.split('\n');let start=lines.slice(0,line).reduce((n,s)=>n+s.length+1,0);const target=lines[line]||'';const match=query?target.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()):-1;if(match>=0)start+=match;const end=start+(match>=0?query.length:target.length);$('editor').setSelectionRange(start,end);requestAnimationFrame(()=>{if(columns.active)columns.focusRange(start,end);else{const editor=$('editor');editor.focus();editor.scrollTop=Math.max(0,(line-4)*parseFloat(getComputedStyle(editor).lineHeight));}});}
 try{localStorage.setItem(state.storageKey+'.selected',file.name);}catch{}
 keepBrowserState().catch(()=>{});
 controls();
}
async function selectFile(name,line=null,query=''){
 if(state.busy)return;capture();showError();const sequence=++state.sequence;state.loading=true;controls();
 try{await flushDrafts();const file=await api('document?name='+encodeURIComponent(name));if(sequence!==state.sequence)return;showFile(file,line,query);$('search-panel').hidden=true;}
 catch(e){if(sequence===state.sequence)showError(e.message);}
 finally{if(sequence===state.sequence){state.loading=false;controls();}}
}
async function save(){
 if(state.busy||state.loading||!state.file)return false;if(!dirty())return true;
 capture();showError();state.busy=true;controls();
 const name=state.file.name,text=$('editor').value,base=state.base;
 try{
  await flushDrafts();const signature=name+'\0'+text+'\0'+base.section_revision;
  if(state.request?.signature!==signature)state.request={signature,id:crypto.randomUUID()};
  const result=await api('save',{name,text,revision:base.revision,section_revision:base.section_revision,request_id:state.request.id});
  const scroll=columns.active?$('columns-editor').scrollTop:$('editor').scrollTop,start=$('editor').selectionStart,end=$('editor').selectionEnd;
  delete state.drafts[name];state.pending.set(name,null);localDrafts();showFile(result.file);$('editor').setSelectionRange(start,end);if(columns.active)$('columns-editor').scrollTop=scroll;else $('editor').scrollTop=scroll;state.request=null;
  await flushDrafts();toast('Saved.');return true;
 }catch(e){showError(e.message);return false;}
 finally{state.busy=false;controls();if(columns.active){const scroll=$('columns-editor').scrollTop;columns.focusRange($('editor').selectionStart,$('editor').selectionEnd);$('columns-editor').scrollTop=scroll;}else $('editor').focus();}
}
async function undoSave(){
 if(state.busy||state.loading||dirty()||!state.file?.undo_id)return;
 const file=state.file;showError();state.busy=true;controls();
 try{const result=await api('undo-save',{name:file.name,revision:file.revision,section_revision:file.section_revision,operation_id:file.undo_id});showFile(result.file);toast('Last save undone.');}
 catch(e){showError(e.message);}
 finally{state.busy=false;controls();}
}
function reloadChoice(){return new Promise(resolve=>{const finish=value=>{$('reload-dialog').close();resolve(value);};$('reload-cancel').onclick=()=>finish('cancel');$('reload-discard').onclick=()=>finish('discard');$('reload-save').onclick=()=>finish('save');$('reload-dialog').oncancel=e=>{e.preventDefault();finish('cancel');};$('reload-dialog').showModal();$('reload-cancel').focus();});}
async function refresh(){
 if(state.busy||state.loading||!state.file)return;
 if(dirty()){
  const choice=await reloadChoice();if(choice==='cancel')return;
  if(choice==='save'){if(!await save())return;}
  else{delete state.drafts[state.file.name];state.pending.set(state.file.name,null);localDrafts();$('editor').value=state.file.text;await flushDrafts();}
 }
 const name=state.file.name;await selectFile(name);
 try{const data=await api('categories');state.categories=data.categories;renderCategories();}catch(e){showError(e.message);}
}
function highlight(element,text,query){const haystack=text.toLocaleLowerCase(),needle=query.toLocaleLowerCase();let start=0,index;while(needle&&(index=haystack.indexOf(needle,start))!==-1){element.append(document.createTextNode(text.slice(start,index)));const mark=document.createElement('mark');mark.textContent=text.slice(index,index+needle.length);element.append(mark);start=index+needle.length;}element.append(document.createTextNode(text.slice(start)));}
let searchTimer;
async function search(){
 const query=$('search').value.trim(),sequence=++state.searchSequence;
 if(!query){$('search-panel').hidden=true;return;}
 $('search-panel').hidden=false;$('search-summary').textContent='Searching…';$('search-results').replaceChildren();
 try{const data=await api('search?q='+encodeURIComponent(query));if(sequence!==state.searchSequence)return;$('search-summary').textContent=`${data.total} ${data.total===1?'match':'matches'}${data.total>60?' · first 60 shown':''}`;
  if(!data.results.length){const p=document.createElement('p');p.className='search-empty';p.textContent='No matching notes.';$('search-results').append(p);}
  data.results.forEach(result=>{const button=document.createElement('button');button.className='search-result';const title=document.createElement('strong');title.textContent=result.title;const text=document.createElement('p');text.dir='auto';highlight(text,result.text,query);button.append(title,text);button.onclick=()=>selectFile(result.name,result.line,query);$('search-results').append(button);});
 }catch(e){$('search-summary').textContent=e.message;}
}
$('editor').addEventListener('input',()=>{capture();showError();});
['click','keyup','select'].forEach(event=>$('editor').addEventListener(event,cursor));
$('save').onclick=save;$('undo-save').onclick=undoSave;$('refresh').onclick=()=>refresh().catch(e=>showError(e.message));
$('search').oninput=()=>{clearTimeout(searchTimer);state.searchSequence++;searchTimer=setTimeout(search,160);};
$('search').onfocus=()=>{if($('search').value.trim())search();};
$('close-search').onclick=()=>{$('search-panel').hidden=true;$('search').focus();};$('close-toast').onclick=()=>{$('toast').hidden=true;};
document.addEventListener('click',event=>{if(!event.target.closest('.search-wrap'))$('search-panel').hidden=true;});
document.addEventListener('keydown',event=>{
 if((event.ctrlKey||event.metaKey)&&columns.active&&!['INPUT','SELECT'].includes(event.target.tagName)&&!document.querySelector('dialog[open]')){
  if(event.key.toLowerCase()==='z'){event.preventDefault();event.shiftKey?columns.redoEdit():columns.undoEdit();}
  if(event.key.toLowerCase()==='y'){event.preventDefault();columns.redoEdit();}
 }
 if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}
 if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('search').focus();$('search').select();}
 if(event.key==='Escape')$('search-panel').hidden=true;
 if(event.key==='ArrowDown'&&document.activeElement===$('search')&&!$('search-panel').hidden){event.preventDefault();$('search-results').querySelector('button')?.focus();}
});
window.addEventListener('beforeunload',()=>{capture();for(const [name,draft] of state.pending){fetch('/api/draft',{method:'POST',headers:{'X-Notes-Token':token,'Content-Type':'application/json'},body:JSON.stringify({name,draft}),keepalive:true}).catch(()=>{});}});
async function ping(){try{await api('ping');$('connection-status').textContent='';}catch{$('connection-status').textContent='Connection paused';}}
async function init(){
 try{
  const [data,saved,portable]=await Promise.all([api('categories'),api('drafts'),api('browser-state')]);state.categories=data.categories;state.drafts=saved.drafts;
  state.storageKey='french-notes.editor.'+data.folder;state.legacyKey='french-notes.drafts.'+data.folder;
  // Application-owned browser state survives a profile, origin or notes-root
  // relocation. Existing server drafts/current-origin values take precedence.
  try{for(const [key,value] of Object.entries(portable.values||{})){if(key.startsWith('french-notes.')&&localStorage.getItem(key)===null)localStorage.setItem(key,value);}
   const oldKey='french-notes.editor.'+(portable.source_folder||data.folder),oldDrafts=JSON.parse(localStorage.getItem(oldKey)||'{}')||{};
   for(const [name,draft] of Object.entries(oldDrafts)){if(!state.drafts[name]&&draft&&typeof draft.text==='string')state.drafts[name]=draft;}
   localDrafts();
   if(oldKey!==state.storageKey){for(const suffix of ['.selected','.recovered']){const value=localStorage.getItem(oldKey+suffix);if(value!==null&&localStorage.getItem(state.storageKey+suffix)===null)localStorage.setItem(state.storageKey+suffix,value);}
    const legacy=localStorage.getItem('french-notes.drafts.'+(portable.source_folder||data.folder));if(legacy&&localStorage.getItem(state.legacyKey)===null)localStorage.setItem(state.legacyKey,legacy);}
   const voice=localStorage.getItem('french-notes.audio-voice'),speed=localStorage.getItem('french-notes.audio-speed');if(['denise','henri','vivienne'].includes(voice))$('speech-voice').value=voice;if(['1','0.8'].includes(speed))$('speech-speed').value=speed;
  }catch{}
  $('folder-path').textContent=data.folder;$('folder-path').title=data.folder;
  let selected;try{const local=JSON.parse(localStorage.getItem(state.storageKey)||'{}');if(local&&typeof local==='object')for(const [name,draft] of Object.entries(local)){if(!state.drafts[name]&&draft&&typeof draft.text==='string')state.drafts[name]=draft;}state.legacy=JSON.parse(localStorage.getItem(state.legacyKey)||'{}')||{};selected=localStorage.getItem(state.storageKey+'.selected');}catch{}
  recoverOldDrafts();renderCategories();if(!state.categories.length){$('save-status').textContent='No notes';return;}
  const requested=new URLSearchParams(location.search).get('note');
  const preferred=requested||selected;const redirected=state.categories.some(item=>item.name==='Indicatif.txt')?(oldNotes[preferred]||preferred):preferred;
  const first=state.categories.find(item=>item.name===redirected)||state.categories[0];await selectFile(first.name);await ping();setInterval(ping,15000);
 }catch(e){showError(e.message);$('save-status').textContent='Could not open notes';}
}
init();
