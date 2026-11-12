'use strict';

class FrenchAudio {
 constructor(token,status,stopButton,speed,voice,onError) {
  this.token=token;this.status=status;this.stopButton=stopButton;this.speed=speed;this.voice=voice;this.onError=onError;
  this.sequence=0;this.audio=null;this.button=null;this.url=null;
  this.cache=new Map();this.pending=new Map();this.cacheBytes=0;this.entries=[];this.queue=[];this.backgroundRunning=false;this.backgroundRetryAt=0;this.closed=false;
  stopButton.onclick=()=>this.stop();
  try{const saved=localStorage.getItem('french-notes.audio-speed');if(['1','0.8'].includes(saved))speed.value=saved;}catch{}
  try{const saved=localStorage.getItem('french-notes.audio-voice');if(['denise','henri','vivienne'].includes(saved))voice.value=saved;}catch{}
  speed.onchange=()=>{this.stop();this.prepareEntries(this.entries);try{localStorage.setItem('french-notes.audio-speed',speed.value);}catch{}};
  voice.onchange=()=>{this.stop();this.prepareEntries(this.entries);try{localStorage.setItem('french-notes.audio-voice',voice.value);}catch{}};
  window.addEventListener('pagehide',()=>{this.closed=true;clearTimeout(this.prepareTimer);clearTimeout(this.editTimer);this.stop();});
 }
 clean(text){return text.replace(/^\s*(?:\d+[.)]|[-•✅❌])\s*/u,'').trim();}
 key(text,voice=this.voice.value,speed=this.speed.value){return JSON.stringify([this.clean(text),voice,speed]);}
 async getBlob(text,voice=this.voice.value,speed=this.speed.value) {
  text=this.clean(text);const key=this.key(text,voice,speed);
  if(this.cache.has(key)){const blob=this.cache.get(key);this.cache.delete(key);this.cache.set(key,blob);return blob;}
  if(this.pending.has(key))return this.pending.get(key);
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),110000);
  const request=(async()=>{
   const response=await fetch('/api/speech',{method:'POST',headers:{'X-Notes-Token':this.token,'Content-Type':'application/json'},body:JSON.stringify({text,voice,speed}),signal:controller.signal});
   if(!response.ok){const result=await response.json();throw new Error(result.error||'French audio is unavailable. You can keep editing and save this row.');}
   const blob=await response.blob();
   this.cache.set(key,blob);this.cacheBytes+=blob.size;
   while(this.cache.size>512||this.cacheBytes>48*1024*1024){const first=this.cache.keys().next().value;this.cacheBytes-=this.cache.get(first).size;this.cache.delete(first);}
   return blob;
  })();
  this.pending.set(key,request);
  try{return await request;}finally{clearTimeout(timer);this.pending.delete(key);}
 }
 prepareEntries(texts) {
  this.entries=[...new Set(texts.map(text=>this.clean(text)).filter(text=>text&&text.length<=4000))];
  this.queue=this.entries.map(text=>({text,voice:this.voice.value,speed:this.speed.value}));
  clearTimeout(this.prepareTimer);this.prepareTimer=setTimeout(()=>this.drain(),250);
 }
 prepareSoon(text) {
  clearTimeout(this.editTimer);this.editTimer=setTimeout(()=>this.prioritize(text),700);
 }
 prioritize(text) {
  text=this.clean(text);if(!text||text.length>4000)return;
  if(!this.entries.includes(text))this.entries.push(text);
  const voice=this.voice.value,speed=this.speed.value,key=this.key(text,voice,speed);
  if(this.cache.has(key))return;
  this.queue=this.queue.filter(item=>this.key(item.text,item.voice,item.speed)!==key);
  this.queue.unshift({text,voice,speed});this.drain();
 }
 async drain() {
  if(this.backgroundRunning||this.closed||Date.now()<this.backgroundRetryAt)return;
  this.backgroundRunning=true;
  try{
   while(this.queue.length&&!this.closed){
    const item=this.queue.shift();
    try{await this.getBlob(item.text,item.voice,item.speed);}
    catch{this.backgroundRetryAt=Date.now()+30000;break;}
   }
  }finally{this.backgroundRunning=false;}
 }
 icon(playing=false) {
  const path=playing?'M7 7h10v10H7Z':'m4 9 4 0 5-4v14l-5-4H4Zm12-1a6 6 0 0 1 0 8m2-11a10 10 0 0 1 0 14';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
 }
 mark(button,playing) {
  if(!button)return;
  button.innerHTML=this.icon(playing);button.setAttribute('aria-pressed',String(playing));
  button.title=playing?'Stop French audio':'Listen in French';
  button.setAttribute('aria-label',(playing?'Stop French audio':'Listen in French')+', row '+button.dataset.row);
  button.closest('.columns-row')?.classList.toggle('is-speaking',playing);
 }
 stop() {
  this.sequence++;
  if(this.audio){this.audio.pause();this.audio.src='';this.audio=null;}
  if(this.url){URL.revokeObjectURL(this.url);this.url=null;}
  this.mark(this.button,false);this.button=null;this.status.textContent='';this.stopButton.hidden=true;
 }
 async play(text,button) {
  if(this.button===button){this.stop();return;}
  this.stop();
  this.onError('');
  text=this.clean(text);
  if(!text){this.onError('Add a French word or sentence to this row first.');return;}
  const sequence=this.sequence;this.button=button;this.mark(button,true);this.stopButton.hidden=false;
  this.status.textContent='Preparing French audio…';
  try{
   const voice=this.voice.value,speed=this.speed.value;this.lastPlayed={text,voice,speed};
   const blob=await this.getBlob(text,voice,speed);if(sequence!==this.sequence)return;
   this.url=URL.createObjectURL(blob);const audio=new Audio(this.url);this.audio=audio;
   audio.playbackRate=1;audio.preservesPitch=true;
   audio.onended=()=>{if(sequence===this.sequence)this.stop();};
   audio.onerror=()=>{if(sequence===this.sequence){this.stop();this.onError('French audio could not be played. Please try again.');}};
   await audio.play();if(sequence===this.sequence)this.status.textContent='Playing French';
  }catch(error){if(sequence!==this.sequence||error.name==='AbortError')return;this.stop();this.onError(error.message);}
 }
}
