const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..','..');
const work=process.env.FRENCH_NOTES_CHECKS||process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage();const report={checks:[],errors:[]};
 page.on('pageerror',error=>report.errors.push(error.message));
 try{
  await page.setContent('<textarea id="source"></textarea><div id="panel"><div id="rows"></div></div><div id="toggle"><button data-view="columns"></button></div>');
  await page.addScriptTag({path:path.join(__dirname,'../../.venvs/french-editor/Lib/site-packages/french_notes/ui/columns.js')});
  const outcome=await page.evaluate(()=>{
   const source=document.querySelector('#source');
   const columns=new NotesColumns(source,document.querySelector('#panel'),document.querySelector('#rows'),document.querySelector('#toggle'));
   const row=text=>`${text}    |    ترجمه    |    Translation`;
   const original=['A','B','C','D'].map(row).join('\n');
   function open(text,key){source.value=text;columns.load(text,key);columns.setDisabled(false);}
   const names=()=>columns.records.map(record=>record.parts?.[0]);
   open(original,'first');columns.removeRow(1);columns.insertRow(0);columns.undoEdit();columns.redoEdit();columns.undoRemove();
   const afterHistory=names();
   open(original,'second');columns.removeRow(1);columns.insertRow(0);const draft=source.value;
   open(row('Other'),'other');open(draft,'second');columns.undoRemove();const afterTabs=names();
   open('2. Cause — علت','heading');const field=document.querySelector('textarea[lang="fa"]');field.focus();field.setSelectionRange(0,field.value.length);field.dispatchEvent(new Event('beforeinput'));field.value+=' تازه';field.dispatchEvent(new Event('input'));columns.undoEdit();
   const headingFocus=document.activeElement.lang,headingText=source.value;
   columns.focusRange(source.value.indexOf('علت'),source.value.length);const searchFocus=document.activeElement.lang;
   columns.redoEdit();columns.focusRange(source.value.lastIndexOf('Cause'),source.value.length);const editedHeadingFocus=document.activeElement.lang;
   open('A plain explanation','note');const note=document.querySelector('#rows textarea');note.value='یادداشت تازه';note.dispatchEvent(new Event('input'));
   return {afterHistory,afterTabs,headingFocus,headingText,searchFocus,editedHeadingFocus,noteDirection:note.dir};
  });
  assert.deepEqual(outcome.afterHistory,['','A','B','C','D'],'Undo Remove keeps its position after undo/redo');report.checks.push('Deleted rows retain their neighbors across undo and redo');
  assert.deepEqual(outcome.afterTabs,['','A','B','C','D'],'Undo Remove keeps its position after changing tabs');report.checks.push('Deleted rows retain their neighbors across tab changes');
  assert.equal(outcome.headingFocus,'fa','Undo keeps focus in the edited heading language');assert.equal(outcome.headingText,'2. Cause — علت');report.checks.push('Undo restores the original heading bytes and Farsi cell focus');
  assert.equal(outcome.searchFocus,'fa');assert.equal(outcome.editedHeadingFocus,'en');report.checks.push('Search and selection reach the correct cells in original and edited headings');
  assert.equal(outcome.noteDirection,'rtl');report.checks.push('Persian explanation direction updates immediately while editing');
  assert.deepEqual(report.errors,[]);report.status='passed';console.log(report.checks.map(check=>'PASS '+check).join('\n'));
 }catch(error){report.status='failed';report.failure=error.stack;throw error;}
 finally{fs.writeFileSync(path.join(work,'undo-results.json'),JSON.stringify(report,null,2));await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
