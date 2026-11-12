'use strict';
const $ = id => document.getElementById(id);
const paths = {
  notebook:'M5 3h14v18H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3ZM7 3v15M3 18h16M11 7h4M11 11h3',
  letters:'m3 17 4-10 4 10M4.5 13h5M15 7h3a3 3 0 0 1 0 6h-3m0-6v10h3a2 2 0 0 0 0-4',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
  question:'M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4m.1 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  compass:'m16 8-3 5-5 3 3-5ZM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  link:'m10 13 4-4m-6 7-1 1a4 4 0 0 1-6-5l4-4a4 4 0 0 1 6 0m2-1 1-1a4 4 0 0 1 6 5l-4 4a4 4 0 0 1-6 0',
  pen:'m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-4L4 15Z',
  arrow:'M4 12h16m-7-7 7 7-7 7',
  flower:'M12 12c-9-6-9 6 0 0s-3-9 0 0 9 9 0 0-9-3 0 0 9-9 0 0M12 12v9',
  flow:'M4 6h10a4 4 0 0 1 0 8H8a4 4 0 0 0 0 8m-4-16 3-3M4 6l3 3',
  clock:'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  sliders:'M4 7h8m4 0h4M4 17h3m4 0h9M12 4v6M7 14v6',
  connect:'M4 4h5v5H4ZM15 15h5v5h-5ZM7 9v8h8M15 7h5m-2.5-2.5v5',
  folder:'M3 7V5h6l2 2h10v13H3Z',
  search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  lines:'M4 6h16M4 12h12M4 18h15',
  refresh:'M20 7v5h-5M4 17v-5h5M5.3 7a8 8 0 0 1 13-1L20 9M4 15l1.7 3a8 8 0 0 0 13-1',
  file:'M14 2H5v20h14V7ZM14 2v5h5M8 12h8M8 16h6',
  plus:'M12 5v14M5 12h14',
  corner:'M19 5v9H5m4-4-4 4 4 4',
  undo:'M4 9h10a6 6 0 0 1 0 12M4 9l5-5M4 9l5 5',
  'check-circle':'m8 12 3 3 5-6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  close:'m6 6 12 12M6 18 18 6',
  copy:'M9 9h12v12H9ZM15 5V3H3v12h2',
  branch:'M6 22C9 14 14 8 21 3M10 15C3 14 2 8 5 6c4 1 5 5 5 9Zm4-5c-3-3-3-7 0-9 3 2 3 6 0 9Zm-5 7c6-4 10-3 11 0-3 4-7 3-11 0Zm7-10c6-3 8-1 8 1-3 3-5 2-8-1Z'
};
function icon(name){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.notebook}"/></svg>`;}
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
const state={file:null,categories:[],folder:'',busy:false,loading:false,selectSequence:0,searchSequence:0,drafts:{},requests:{},storageKey:'',toastTimer:null};
const token=document.querySelector('meta[name="app-token"]').content;
async function api(path,body){
  const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Notes-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){const error=new Error(data.error||'Something went wrong. Please try again.');error.code=data.code;throw error;}
  return data;
}
function toast(message){clearTimeout(state.toastTimer);$('toast-message').textContent=message;$('toast').hidden=false;state.toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
function error(message){$('form-error').textContent=message;$('form-error').hidden=!message;}
function saveDraft(){
  if(!state.file)return;
  const text=$('draft').value;
  state.drafts[state.file.name]=text;
  try{localStorage.setItem(state.storageKey,JSON.stringify(state.drafts));$('draft-saved').textContent=text?'Draft saved':'';}
  catch{$('draft-saved').textContent=text?'Unsaved draft':'';}
}
function draftCount(){
  const lines=$('draft').value.split(/\r?\n/).filter(x=>x.trim()).length;
  $('draft-lines').textContent=lines?`${lines} ${lines===1?'line':'lines'} ready to add`:'Ready when you are';
  $('add-button').disabled=state.busy||state.loading||!state.file||!lines;
}
function setBusy(value){
  state.busy=value;
  $('draft').disabled=value||state.loading;
  $('refresh').disabled=value||state.loading;
  document.querySelectorAll('.category').forEach(el=>el.disabled=value);
  $('undo').disabled=value||state.loading||!state.file?.undo_id;
  $('add-label').textContent=value?'Saving…':`Add to ${state.file?.title||'your notes'}`;
  draftCount();
}
function renderCategories(){
  const nav=$('categories');nav.replaceChildren();
  let group=null,container;
  state.categories.forEach(category=>{
    if(category.group!==group){group=category.group;container=document.createElement('section');container.className='nav-group';const label=document.createElement('h2');label.textContent=group;container.append(label);nav.append(container);}
    const button=document.createElement('button');button.className='category';button.type='button';button.dataset.name=category.name;button.disabled=state.busy;
    button.innerHTML=icon(category.icon);const text=document.createElement('span');text.textContent=category.title;button.append(text);
    button.addEventListener('click',()=>selectFile(category.name));container.append(button);
  });
  $('category-count').textContent=state.categories.length;
  markCategory();
}
function markCategory(){document.querySelectorAll('.category').forEach(el=>{const active=el.dataset.name===state.file?.name;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});}
function highlight(element,text,query){
  if(!query){element.textContent=text;return;}
  const lower=text.toLocaleLowerCase();const needle=query.toLocaleLowerCase();let start=0,index;
  while((index=lower.indexOf(needle,start))!==-1){element.append(document.createTextNode(text.slice(start,index)));const mark=document.createElement('mark');mark.textContent=text.slice(index,index+needle.length);element.append(mark);start=index+needle.length;}
  element.append(document.createTextNode(text.slice(start)));
}
function renderNotes(targetLine=null,query=''){
  const notes=$('notes');notes.replaceChildren();
  const lines=state.file.text.split(/\r\n|\r|\n/);let gap=false,count=0;
  lines.forEach((line,index)=>{
    if(!line.trim()){gap=true;return;}
    const heading=/^\s*[-_=]{3,}/.test(line)||/^\s*#{1,4}\s/.test(line);
    const title=line.replace(/^\s*[-_=#]+|[-_=]+\s*$/g,'').trim();
    if(heading&&!title)return;
    if(heading&&count===0)return;
    if(gap&&count){const spacer=document.createElement('div');spacer.className='note-gap';notes.append(spacer);}
    gap=false;
    const row=document.createElement('p');row.className='note-line'+(heading?' note-heading':'');row.dir='auto';row.dataset.line=index;
    highlight(row,heading?title:line,query);if(index===targetLine)row.classList.add('target-line');notes.append(row);count++;
  });
  if(!count)notes.innerHTML=`<div class="empty">${icon('notebook')}<h3>A fresh page</h3><p>Add your first notes using the box alongside.</p></div>`;
  $('line-count').textContent=`${count} ${count===1?'line':'lines'}`;
  notes.setAttribute('aria-busy','false');
  if(targetLine!==null){requestAnimationFrame(()=>{const target=notes.querySelector(`[data-line="${targetLine}"]`);if(target)notes.scrollTop+=target.getBoundingClientRect().top-notes.getBoundingClientRect().top-24;});}
  else notes.scrollTop=0;
}
function showFile(file,targetLine=null,query=''){
  state.file=file;
  document.title=file.title+' · French Notes';
  $('breadcrumb-current').textContent=file.title;$('page-title').textContent=file.title;$('group-label').textContent=file.group.toUpperCase();
  $('description').textContent=file.description;$('filename').textContent=file.name;$('draft-category').textContent=file.title;
  $('draft').value=state.drafts[file.name]||'';$('draft-saved').textContent=$('draft').value?'Draft saved':'';
  $('add-label').textContent='Add to '+file.title;$('undo').disabled=!file.undo_id;
  markCategory();renderNotes(targetLine,query);draftCount();
  try{localStorage.setItem(state.storageKey+'.selected',file.name);}catch{}
}
async function selectFile(name,targetLine=null,query=''){
  if(state.busy)return;
  saveDraft();error('');const sequence=++state.selectSequence;
  state.loading=true;setBusy(false);
  $('notes').setAttribute('aria-busy','true');
  try{const file=await api('file?name='+encodeURIComponent(name));if(sequence!==state.selectSequence)return;showFile(file,targetLine,query);$('search-panel').hidden=true;}
  catch(e){if(sequence===state.selectSequence){error(e.message);$('notes').setAttribute('aria-busy','false');}}
  finally{if(sequence===state.selectSequence){state.loading=false;setBusy(state.busy);}}
}
function duplicateDecision(duplicates){
  $('duplicate-description').textContent=`${duplicates.length} ${duplicates.length===1?'line looks':'lines look'} like something already in this file or repeated in your paste.`;
  $('duplicate-list').replaceChildren();
  duplicates.slice(0,8).forEach(item=>{const row=document.createElement('p');row.dir='auto';row.textContent=item.text;$('duplicate-list').append(row);});
  if(duplicates.length>8){const row=document.createElement('p');row.textContent=`And ${duplicates.length-8} more…`;$('duplicate-list').append(row);}
  return new Promise(resolve=>{
    const finish=value=>{$('duplicates').close();resolve(value);};
    $('duplicate-cancel').onclick=()=>finish('cancel');$('duplicate-skip').onclick=()=>finish('skip');$('duplicate-keep').onclick=()=>finish('keep');
    $('duplicates').oncancel=event=>{event.preventDefault();finish('cancel');};
    $('duplicates').showModal();$('duplicate-cancel').focus();
  });
}
async function addNotes(event){
  event.preventDefault();if(state.busy||state.loading||!state.file||!$('draft').value.trim())return;
  const file=state.file;let text=$('draft').value;error('');setBusy(true);
  try{
    const preview=await api('preview',{name:file.name,text,revision:file.revision});
    let allow_duplicates=false;
    if(preview.duplicates.length){
      const choice=await duplicateDecision(preview.duplicates);
      if(choice==='cancel')return;
      if(choice==='skip'){
        const skip=new Set(preview.duplicates.map(d=>d.line));text=text.split(/\r\n|\r|\n/).filter((_,i)=>!skip.has(i+1)).join('\n');
        if(!text.trim()){toast('These lines are already in your notes. Nothing was added.');return;}
      }else allow_duplicates=true;
    }
    const signature=file.name+'\0'+text;
    if(state.requests[file.name]?.signature!==signature)state.requests[file.name]={signature,id:crypto.randomUUID()};
    const result=await api('append',{name:file.name,text,revision:file.revision,request_id:state.requests[file.name].id,allow_duplicates});
    state.drafts[file.name]='';$('draft').value='';saveDraft();showFile(result.file);$('notes').scrollTop=$('notes').scrollHeight;
    delete state.requests[file.name];
    toast(`${result.added_lines} ${result.added_lines===1?'line added':'lines added'} to ${file.title}.`);
  }catch(e){error(e.message);}
  finally{setBusy(false);$('draft').focus();}
}
async function undo(){
  if(state.busy||state.loading||!state.file?.undo_id)return;
  const file=state.file;error('');setBusy(true);
  try{const result=await api('undo',{name:file.name,revision:file.revision,operation_id:file.undo_id});showFile(result.file);toast('Last addition undone. Your earlier notes are restored.');}
  catch(e){error(e.message);}
  finally{setBusy(false);}
}
let searchTimer;
async function search(){
  const query=$('search').value.trim();const sequence=++state.searchSequence;
  if(!query){$('search-panel').hidden=true;return;}
  $('search-panel').hidden=false;$('search-summary').textContent='Searching your notebook…';$('search-results').replaceChildren();
  try{
    const data=await api('search?q='+encodeURIComponent(query));
    if(sequence!==state.searchSequence)return;
    $('search-summary').textContent=data.total?`${data.total} ${data.total===1?'match':'matches'}${data.total>60?' · showing the first 60':''}`:'No matches yet';
    if(!data.results.length){const empty=document.createElement('p');empty.className='search-empty';empty.textContent='Try another French word or part of a sentence.';$('search-results').append(empty);return;}
    data.results.forEach(result=>{const button=document.createElement('button');button.className='search-result';const label=document.createElement('strong');label.textContent=result.title;const line=document.createElement('p');line.dir='auto';highlight(line,result.text,query);button.append(label,line);button.onclick=()=>selectFile(result.name,result.line,query);$('search-results').append(button);});
  }catch(e){$('search-summary').textContent=e.message;}
}
$('draft').addEventListener('input',()=>{saveDraft();draftCount();error('');});
$('add-form').addEventListener('submit',addNotes);
$('undo').addEventListener('click',undo);
$('refresh').addEventListener('click',async()=>{if(state.file){await selectFile(state.file.name);if($('form-error').hidden)toast('Your notes are up to date.');}try{const data=await api('categories');state.categories=data.categories;renderCategories();}catch{}});
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);state.searchSequence++;searchTimer=setTimeout(search,160);});
$('search').addEventListener('focus',()=>{if($('search').value.trim())search();});
$('close-search').onclick=()=>{$('search-panel').hidden=true;$('search').focus();};
$('close-toast').onclick=()=>{$('toast').hidden=true;};
document.addEventListener('click',event=>{if(!event.target.closest('.search-wrap'))$('search-panel').hidden=true;});
document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('search').focus();$('search').select();}
  if(event.key==='Escape'){$('search-panel').hidden=true;}
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&document.activeElement===$('draft')&&!$('duplicates').open){event.preventDefault();$('add-form').requestSubmit();}
  if(event.key==='ArrowDown'&&document.activeElement===$('search')&&!$('search-panel').hidden){event.preventDefault();$('search-results').querySelector('button')?.focus();}
});
async function ping(){try{await api('ping');$('connection-status').textContent='Everything stays on your computer';}catch{$('connection-status').textContent='Connection paused · reopen French Notes if it does not return';}}
async function init(){
  try{
    const data=await api('categories');state.categories=data.categories;state.folder=data.folder;state.storageKey='french-notes.drafts.'+data.folder;
    $('folder-path').textContent=data.folder;$('folder-path').title=data.folder;
    let selected;try{const saved=JSON.parse(localStorage.getItem(state.storageKey)||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))state.drafts=Object.fromEntries(Object.entries(saved).filter(([k,v])=>typeof v==='string'));selected=localStorage.getItem(state.storageKey+'.selected');}catch{}
    renderCategories();
    if(!data.categories.length){$('notes').innerHTML=`<div class="empty">${icon('folder')}<h3>No text files yet</h3><p>Add a .txt file to your notes folder, then reopen the app.</p></div>`;return;}
    const first=data.categories.find(c=>c.name===selected)||data.categories[0];await selectFile(first.name);await ping();setInterval(ping,15000);
  }catch(e){error(e.message);$('notes').setAttribute('aria-busy','false');$('connection-status').textContent='Could not open your notes';}
}
init();
