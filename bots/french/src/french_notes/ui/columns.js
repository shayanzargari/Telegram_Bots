'use strict';

class NotesColumns {
 constructor(source, panel, rows, toggle, player=null, controls={}) {
  this.source=source;this.panel=panel;this.rows=rows;this.toggle=toggle;
  this.records=[];this.active=false;this.available=false;this.disabled=true;this.mode='columns';
  this.lastPosition=0;
  this.player=player;
  this.addButton=controls.add;this.undoButton=controls.undo;this.removed=[];
  this.past=[];this.future=[];this.histories=new Map();this.documentKey=null;this.editGroup=null;
  this.nextRecordId=1;this.cellSelection=null;
  if(this.addButton)this.addButton.onclick=()=>this.appendRow();
  if(this.undoButton)this.undoButton.onclick=()=>this.undoRemove();
 }
 get expressionLayout(){return this.documentKey==='Expressions.txt';}
 get tenseSections(){return this.documentKey==='Indicatif.txt'?['Présent','Passé composé','Imparfait','Plus-que-parfait','Futur simple','Futur antérieur','Futur proche','Passé récent','Passé simple','Formules et fragments']:this.documentKey==='Conditionnel.txt'?['Présent','Passé','Formules et fragments']:[];}
 get tenseLayout(){return this.tenseSections.length>0;}
 get columnDefinitions(){return this.expressionLayout?[
  ['Expression','fr','ltr'],['Farsi meaning','fa','rtl'],['English meaning','en','ltr'],
  ['French example','fr','ltr'],['Farsi translation','fa','rtl'],['English translation','en','ltr']
 ]:[[this.documentKey==='@verbs'?'Infinitif présent':'French','fr','ltr'],['Farsi','fa','rtl'],['English','en','ltr']];}
 updateHeading(){
  this.panel.classList.toggle('expression-layout',this.expressionLayout);
  this.panel.classList.toggle('tense-layout',this.tenseLayout);
  this.panel.setAttribute('aria-label',this.expressionLayout?'Editable expressions, meanings, and examples':'Editable French, Farsi, and English columns');
  const heading=this.panel.querySelector('.columns-heading');if(!heading)return;
  heading.replaceChildren(...this.columnDefinitions.map(([label,lang,dir])=>{const span=document.createElement('span');span.textContent=label==='Farsi'?'فارسی':label;span.lang=label==='Farsi'?lang:'en';span.dir=dir;return span;}));
  const actions=document.createElement('span');actions.className='row-heading';actions.textContent='Rows';heading.append(actions);
 }
 speechText(record){return this.expressionLayout?(record.parts[3].trim()||record.parts[0]):record.parts[0];}
 parse(text) {
  const titles={'Contrast':['Contraste','Contrast'],'Cause':['Cause','Cause'],'Result':['Conséquence','Result'],'Addition':['Addition','Addition'],'Condition':['Condition','Condition'],'Order / Time':['Ordre / Temps','Order / Time']};
  const verbTitles={'Verbs with À':['Verbes avec à','فعل‌ها با حرف اضافهٔ à','Verbs with à'],'Verbs with DE':['Verbes avec de','فعل‌ها با حرف اضافهٔ de','Verbs with de'],'Verbs with other prepositions':['Verbes avec d’autres prépositions','فعل‌ها با حرف‌های اضافهٔ دیگر','Verbs with other prepositions']};
  return text.split('\n').map(line=>{
   if(this.tenseLayout&&line.startsWith('## ')&&this.tenseSections.includes(line.slice(3).trim()))return {text:line,sectionHeader:line.slice(3).trim()};
   const parts=line.split('    |    ');
   if(this.tenseLayout&&parts.length===4)return {parts:parts.slice(0,3),secondary:parts[3],metadataPresent:true};
   if(this.expressionLayout&&parts.length===6)return {parts};
   if(this.expressionLayout&&parts.length===3){const displayed=['','','',...parts];return {parts:displayed,original:line,originalParts:[...displayed]};}
   if(parts.length===3&&(/^[1-6]\.\s*(?:Contraste|Cause|Conséquence|Addition|Condition|Ordre \/ Temps)$/u.test(parts[0])||/^Verbes avec /u.test(parts[0])))return {parts,heading:true};
   const bilingual=line.match(/^(\d+\.\s*)([^—]+?)\s*—\s*(.+)$/u);
   if(parts.length!==3&&bilingual&&titles[bilingual[2]]){
    const [french,english]=titles[bilingual[2]],display=[bilingual[1]+french,bilingual[3],english];
    return {parts:display,heading:true,original:line,originalParts:[...display],sourceColumns:[0,line.lastIndexOf(bilingual[3]),bilingual[1].length]};
   }
   const markdown=line.match(/^###\s+(.+)$/u);
   if(parts.length!==3&&markdown&&verbTitles[markdown[1]]){
    const display=verbTitles[markdown[1]];return {parts:[...display],heading:true,original:line,originalParts:[...display]};
   }
   if(parts.length!==3&&line.includes('\t')){
    const fragments=line.split('\t').map(value=>value.trim().replace(/مفر\s+د/gu,'مفرد'));
    return {fragments,original:line,originalFragments:[...fragments]};
   }
   return parts.length===3?{parts}:{text:line};
  }).map(record=>this.identify(record));
 }
 identify(record){record.id=this.nextRecordId++;return record;}
 recordText(row){
  if(this.tenseLayout&&row.parts)return row.parts.join('    |    ')+(row.secondary||row.metadataPresent?'    |    '+(row.secondary||''):'');
  const values=row.parts||row.fragments,original=row.originalParts||row.originalFragments;
  if(row.original&&values.every((value,index)=>value===original[index]))return row.original;
  if(row.fragments&&row.original){const source=row.original.split('\t');return row.fragments.map((value,index)=>{if(value===original[index])return source[index];const padding=source[index].match(/^(\s*)[\s\S]*?(\s*)$/u);return padding[1]+value+padding[2];}).join('\t');}
  return row.parts?row.parts.join('    |    '):row.fragments?row.fragments.join('\t'):row.text;
 }
 serialize() {
  return this.records.map(row=>this.recordText(row)).join('\n');
 }
 load(text,documentKey=this.documentKey) {
  this.player?.stop();
  if(this.documentKey)this.histories.set(this.documentKey,{text:this.serialize(),records:structuredClone(this.records),past:this.past,future:this.future,removed:this.removed});
  const saved=this.histories.get(documentKey),keep=saved?.text===text;this.documentKey=documentKey;
  this.updateHeading();
  this.past=keep?saved.past:[];this.future=keep?saved.future:[];this.removed=keep?saved.removed:[];this.editGroup=null;
  this.records=keep?structuredClone(saved.records):this.parse(text);this.cellSelection=null;this.available=true;
  this.toggle.hidden=!this.available;
  if(this.available)this.render();else this.rows.replaceChildren();
  this.applyMode();
 }
 setMode(mode) {
  this.player?.stop();
  this.mode=mode;
  if(mode==='columns')this.load(this.source.value);else this.applyMode();
  this.focusRange(this.source.selectionStart,this.source.selectionEnd);
 }
 applyMode() {
  this.active=this.available&&this.mode==='columns';
  this.source.hidden=this.active;this.panel.hidden=!this.active;
  if(this.addButton)this.addButton.hidden=!this.active||this.tenseLayout;
  this.updateRowControls();
  this.toggle.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===this.mode)));
 }
 setDisabled(disabled) {
  if(disabled!==this.disabled) {
   this.disabled=disabled;
   this.rows.querySelectorAll('textarea, button, select').forEach(field=>field.disabled=disabled);
  }
  this.toggle.querySelectorAll('button').forEach(button=>button.disabled=disabled);
  this.updateRowControls();
 }
 updateRowControls() {
  if(this.addButton)this.addButton.disabled=this.disabled;
  if(this.undoButton){this.undoButton.hidden=!this.active||!this.removed.length;this.undoButton.disabled=this.disabled;}
 }
 snapshot(){return {text:this.serialize(),records:structuredClone(this.records),start:this.source.selectionStart,end:this.source.selectionEnd,cell:structuredClone(this.cellSelection),removed:structuredClone(this.removed)};}
 checkpoint(group=null) {
  const now=Date.now();
  if(!group||group!==this.editGroup||now-this.editTime>650){this.past.push(this.snapshot());if(this.past.length>80)this.past.shift();}
  this.editGroup=group;this.editTime=now;this.future=[];
 }
 restore(snapshot) {
  this.editGroup=null;this.records=structuredClone(snapshot.records);this.removed=structuredClone(snapshot.removed);
  this.source.value=snapshot.text;const scroll=this.panel.scrollTop;this.render();this.panel.scrollTop=scroll;
  this.source.dispatchEvent(new Event('input',{bubbles:true}));
  const cell=snapshot.cell,target=cell&&this.rows.querySelector(`[data-line="${cell.line}"][data-column="${cell.column}"]`);
  if(target){target.focus({preventScroll:true});target.setSelectionRange(cell.start,cell.end);target.scrollIntoView({block:'nearest',inline:'nearest'});this.remember(target);}
  else this.focusRange(snapshot.start,snapshot.end);
 }
 undoEdit() {
  if(this.disabled||!this.past.length)return;
  this.future.push(this.snapshot());this.restore(this.past.pop());
 }
 redoEdit() {
  if(this.disabled||!this.future.length)return;
  this.past.push(this.snapshot());this.restore(this.future.pop());
 }
 commitRows(index=0,column=0) {
  this.player?.stop();const scroll=this.panel.scrollTop;
  this.source.value=this.serialize();this.render();this.panel.scrollTop=scroll;
  this.source.dispatchEvent(new Event('input',{bubbles:true}));
  const target=this.rows.querySelector(`[data-line="${index}"][data-column="${column}"]`);
  if(target){target.focus();target.setSelectionRange(0,0);this.remember(target);}
  this.updateRowControls();
 }
 appendRow() {
  if(this.tenseLayout){this.appendToSection(this.sectionOf(Number(document.activeElement?.dataset.line||0)));return;}
  let index=this.records.length;
  while(index>0&&!this.records[index-1].parts&&!this.records[index-1].fragments&&!this.records[index-1].text.trim())index--;
  this.insertRow(index);
 }
 sectionOf(index){
  for(let i=Math.min(index,this.records.length-1);i>=0;i--)if(this.records[i].sectionHeader)return this.records[i].sectionHeader;
  return this.tenseSections[0];
 }
 sectionEnd(section){
  const header=this.records.findIndex(row=>row.sectionHeader===section);
  if(header<0)return this.records.length;
  let end=this.records.findIndex((row,index)=>index>header&&row.sectionHeader);
  if(end<0)end=this.records.length;
  while(end>header+1&&!this.records[end-1].parts&&!this.records[end-1].text?.trim())end--;
  return end;
 }
 appendToSection(section){
  if(this.disabled)return;
  if(!this.records.some(row=>row.sectionHeader===section)){
   this.checkpoint();this.records.push(this.identify({text:'## '+section,sectionHeader:section}));
  }
  this.insertRow(this.sectionEnd(section));
 }
 moveToSection(index,section){
  if(this.disabled||section===this.sectionOf(index)||!this.tenseSections.includes(section))return;
  this.checkpoint();const [record]=this.records.splice(index,1);
  if(record.secondary)record.secondary=record.secondary.split(';').map(value=>value.trim()).filter(value=>value!==section).join('; ');
  const target=this.sectionEnd(section);this.records.splice(target,0,record);this.commitRows(target);
 }
 insertRow(index,column=0) {
  if(this.disabled)return;
  this.checkpoint();
  this.records.splice(index,0,this.identify({parts:this.columnDefinitions.map(()=> '')}));this.commitRows(index,column);
 }
 removeRow(index) {
  if(this.disabled)return;
  const record=this.records[index];if(!record)return;
  this.checkpoint();
  this.removed.push({record,index,before:this.records[index-1]?.id,after:this.records[index+1]?.id});
  this.records.splice(index,1);this.commitRows(Math.min(index,this.records.length-1));
 }
 undoRemove() {
  if(this.disabled||!this.removed.length)return;
  this.checkpoint();
  const saved=this.removed.pop();let index=this.records.findIndex(record=>record.id===saved.after);
  if(index<0){const before=this.records.findIndex(record=>record.id===saved.before);index=before<0?Math.min(saved.index,this.records.length):before+1;}
  this.records.splice(index,0,saved.record);this.commitRows(index);
 }
 lineText(index) {
  return this.recordText(this.records[index]);
 }
 startOf(index,column=0) {
  let offset=0;
  for(let i=0;i<index;i++)offset+=this.lineText(i).length+1;
  const row=this.records[index];
  if(row.fragments){const fragments=this.recordText(row).split('\t');for(let i=0;i<column;i++)offset+=fragments[i].length+1;return offset+(fragments[column].match(/^\s*/u)?.[0].length||0);}
  if(row.original&&this.recordText(row)===row.original){
   if(this.expressionLayout&&row.parts&&column>=3)return offset+row.parts.slice(3,column).reduce((total,value)=>total+value.length+9,0);
   return offset+(row.sourceColumns?.[column]||0);
  }
  if(this.records[index].parts)for(let i=0;i<column;i++)offset+=this.records[index].parts[i].length+9;
  return offset;
 }
 remember(field) {
  if(!field.isConnected||!this.records[Number(field.dataset.line)])return;
  const start=this.startOf(Number(field.dataset.line),Number(field.dataset.column||0));
  this.source.setSelectionRange(start+field.selectionStart,start+field.selectionEnd);
  this.lastPosition=start+field.selectionStart;
  this.cellSelection={line:Number(field.dataset.line),column:Number(field.dataset.column||0),start:field.selectionStart,end:field.selectionEnd};
  this.source.dispatchEvent(new Event('select'));
 }
 changed(field) {
  const index=Number(field.dataset.line),column=Number(field.dataset.column||0),row=this.records[index];
  this.checkpoint(`cell:${index}:${column}`);
  if(this.tenseLayout&&row.parts&&column===0){row.secondary='';row.metadataPresent=false;field.parentElement.querySelector('.tense-badges')?.remove();}
  if(row.parts&&field.value.includes('\n')) {
   const values=field.value.split('\n');row.parts[column]=values.shift();
   const added=values.map(value=>{const parts=this.columnDefinitions.map(()=> '');parts[column]=value;return this.identify({parts});});
   this.records.splice(index+1,0,...added);this.source.value=this.serialize();this.render();
   const target=this.rows.querySelector(`[data-line="${index+added.length}"][data-column="${column}"]`);
   target.focus();target.setSelectionRange(target.value.length,target.value.length);this.remember(target);
  }else{
   if(row.parts)row.parts[column]=field.value;else if(row.fragments)row.fragments[column]=field.value;else row.text=field.value;
   field.parentElement.dataset.value=field.value;
   if(!row.parts){field.dir=/[\u0600-\u06ff]/u.test(field.value)?'rtl':'auto';field.parentElement.dir=field.dir;}
   this.source.value=this.serialize();this.remember(field);
  }
  if(row.parts&&this.columnDefinitions[column][1]==='fr')this.player?.prepareSoon(field.value);
  this.source.dispatchEvent(new Event('input',{bubbles:true}));
 }
 move(field,backwards,insert) {
  const index=Number(field.dataset.line),column=Number(field.dataset.column);
  let next=-1;
  if(!insert)for(let i=index+(backwards?-1:1);i>=0&&i<this.records.length;i+=backwards?-1:1) {
   if(this.tenseLayout&&this.records[i].sectionHeader)break;
   if(this.records[i].parts){next=i;break;}
  }
  if(next<0&&!backwards) {
   this.insertRow(index+1,column);return;
  }
  if(next>=0) {
   const target=this.rows.querySelector(`[data-line="${next}"][data-column="${column}"]`);
   target.focus();target.setSelectionRange(0,0);this.remember(target);
  }
 }
 field(value,index,column,tabular) {
  const wrapper=document.createElement('div');wrapper.className='column-field';wrapper.dataset.value=value;
  const field=document.createElement('textarea');field.rows=1;field.spellcheck=false;field.value=value;
  field.dataset.line=index;field.dataset.column=column;field.disabled=this.disabled;
  const definition=tabular?this.columnDefinitions[column]:null;
  const language=tabular?definition[0]:`Notes ${column+1}`;
  field.setAttribute('aria-label',`${language}, row ${index+1}`);
  field.lang=tabular?definition[1]:'';field.dir=tabular?definition[2]:(/[\u0600-\u06ff]/u.test(value)?'rtl':'auto');
  wrapper.dir=field.dir;
  field.addEventListener('input',()=>this.changed(field));
  field.addEventListener('beforeinput',()=>this.remember(field));
  ['click','keyup','select','focus'].forEach(event=>field.addEventListener(event,()=>this.remember(field)));
  if(tabular)field.addEventListener('keydown',event=>{
   if(event.key==='Enter'&&!event.isComposing){event.preventDefault();this.move(field,event.shiftKey,event.ctrlKey||event.metaKey);}
  });
  if(tabular){field.placeholder=definition[0]==='Farsi'?'فارسی':definition[0];}
  if(tabular&&definition[1]==='fr')field.addEventListener('blur',()=>this.player?.prioritize(field.value));
  wrapper.append(field);return wrapper;
 }
 render() {
  this.player?.stop();
  const content=document.createDocumentFragment();
  const groups=new Map();
  if(this.tenseLayout)for(const [number,name]of this.tenseSections.entries()){
   const section=document.createElement('section');section.className='tense-table';section.dataset.section=name;
   const title=document.createElement('header');title.className='tense-title';
   const heading=document.createElement('h2');heading.id='tense-title-'+number;heading.textContent=name;
   const add=document.createElement('button');add.type='button';add.className='quiet-button section-add';add.textContent='Add Row';add.disabled=this.disabled;add.setAttribute('aria-label','Add row to '+name);add.onclick=()=>this.appendToSection(name);
   title.append(heading,add);section.append(title);
   const table=document.createElement('div');table.className='tense-grid';table.setAttribute('role','table');table.setAttribute('aria-labelledby',heading.id);
   const columns=this.panel.querySelector('.columns-heading')?.cloneNode(true)||document.createElement('div');columns.setAttribute('role','row');columns.querySelectorAll('span').forEach(span=>span.setAttribute('role','columnheader'));
   const body=document.createElement('div');body.className='tense-body';body.setAttribute('role','rowgroup');table.append(columns,body);section.append(table);content.append(section);groups.set(name,body);
  }
  let currentSection=this.tenseSections[0];
  this.records.forEach((record,index)=>{
   if(record.sectionHeader){currentSection=record.sectionHeader;return;}
   if(this.tenseLayout&&!record.parts&&!record.fragments&&!record.text?.trim())return;
   const row=document.createElement('div');row.dataset.sourceLine=index;
   row.className=record.parts?'columns-row':'notes-row';
   if(this.tenseLayout)row.setAttribute('role','row');
   if(record.heading)row.classList.add('section-heading');
   if(record.parts){
    record.parts.forEach((value,column)=>row.append(this.field(value,index,column,true)));
    if(this.tenseLayout){
     row.querySelectorAll('.column-field').forEach(field=>field.setAttribute('role','cell'));
     if(record.secondary){const badges=document.createElement('div');badges.className='tense-badges';badges.setAttribute('aria-label','Other tenses');for(const label of record.secondary.split(';').map(value=>value.trim()).filter(Boolean)){const badge=document.createElement('span');badge.textContent=label;badges.append(badge);}row.firstElementChild.append(badges);}
    }
    if(this.player&&!record.heading&&record.parts[2].trim()!=='English'){
     const button=document.createElement('button');button.type='button';button.className='row-audio';button.dataset.row=index+1;button.disabled=this.disabled;
     button.innerHTML=this.player.icon();button.title='Listen in French';button.setAttribute('aria-label',`Listen in French, row ${index+1}`);button.setAttribute('aria-pressed','false');
     button.onclick=()=>this.player.play(this.speechText(this.records[index]),button);
     button.onpointerenter=()=>this.player.prioritize(this.speechText(this.records[index]));
     button.onfocus=()=>this.player.prioritize(this.speechText(this.records[index]));
     const audioField=row.children[this.expressionLayout?3:0];audioField.classList.add('with-audio');audioField.append(button);
    }
   }
   else{
    if(record.fragments){row.classList.add('comparison-row');row.style.setProperty('--note-count',record.fragments.length);record.fragments.forEach((value,column)=>row.append(this.field(value,index,column,false)));}
    else{
     if(!record.text.trim())row.classList.add('blank-row');
     if(/^\s*[-_]{5,}/.test(record.text))row.classList.add('section-row');
     row.append(this.field(record.text,index,0,false));
    }
   }
   const heading=record.parts&&record.parts[0].trim()==='French'&&record.parts[2].trim()==='English';
   if(!heading&&(record.parts||record.fragments||record.text.trim())){
    const actions=document.createElement('div');actions.className='row-actions';
    if(this.tenseLayout&&record.parts){
     actions.setAttribute('role','cell');const move=document.createElement('select');move.className='move-section';move.disabled=this.disabled;move.setAttribute('aria-label','Move to section, row '+(index+1));move.title='Move to section';
     for(const label of this.tenseSections){const option=document.createElement('option');option.value=label;option.textContent=label;move.append(option);}move.value=currentSection;move.onchange=()=>this.moveToSection(index,move.value);actions.append(move);
    }
    for(const [name,label,path,callback] of [
     ['insert-row','Insert row below','M12 5v14M5 12h14',()=>this.insertRow(index+1)],
     ['remove-row','Remove row','M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 10v7M14 10v7',()=>this.removeRow(index)]
    ]){
     const button=document.createElement('button');button.type='button';button.className=name;button.disabled=this.disabled;button.title=label;button.setAttribute('aria-label',`${label}, row ${index+1}`);
     button.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;button.onclick=callback;actions.append(button);
    }
    row.append(actions);
   }
   (this.tenseLayout?groups.get(currentSection)||groups.get(this.tenseSections.at(-1)):content).append(row);
  });
  for(const body of groups.values())if(!body.children.length){const empty=document.createElement('p');empty.className='tense-empty';empty.textContent='No entries yet.';body.append(empty);}
  this.rows.replaceChildren(content);
  this.player?.prepareEntries(this.records.filter(row=>row.parts&&!row.heading&&row.parts[2].trim()!=='English').map(row=>this.speechText(row)));
  this.updateRowControls();
 }
 focusRange(start=this.lastPosition,end=start) {
  this.source.setSelectionRange(start,end);
  if(!this.active){this.source.focus();return;}
  let offset=0,target=null,localStart=0,localEnd=0;
  for(let index=0;index<this.records.length;index++) {
   const row=this.records[index],text=this.lineText(index);
   if(start<=offset+text.length||index===this.records.length-1) {
    let relative=Math.max(0,start-offset),column=0;
    if(row.fragments){while(column<row.fragments.length-1&&start>=this.startOf(index,column+1))column++;relative=start-this.startOf(index,column);}
    else if(row.parts&&row.original&&row.originalParts&&this.expressionLayout&&this.recordText(row)===row.original){column=3;while(column<5&&relative>row.parts[column].length){relative-=row.parts[column].length+9;column++;}}
    else if(row.parts&&this.recordText(row)!==row.original)while(column<row.parts.length-1&&relative>row.parts[column].length){relative-=row.parts[column].length+9;column++;}
    else if(row.sourceColumns&&relative>=row.sourceColumns[1]){column=1;relative-=row.sourceColumns[1];}
    target=this.rows.querySelector(`[data-line="${index}"][data-column="${column}"]`);
    if(!target&&this.tenseLayout){const group=[...this.rows.querySelectorAll('.tense-table')].find(node=>node.dataset.section===this.sectionOf(index));group?.querySelector('textarea, .section-add')?.focus({preventScroll:true});return;}
    localStart=Math.max(0,Math.min(relative,target.value.length));localEnd=Math.min(target.value.length,localStart+end-start);break;
   }
   offset+=text.length+1;
  }
  if(target){target.focus({preventScroll:true});target.setSelectionRange(localStart,localEnd);target.scrollIntoView({block:'nearest',inline:'nearest'});this.remember(target);}
 }
}
