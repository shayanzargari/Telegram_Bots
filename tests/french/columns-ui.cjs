const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..','..'),work=process.env.FRENCH_NOTES_CHECKS||process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french');
const sourceNotes=process.env.FRENCH_NOTES_TEST_SOURCE||path.join(__dirname,'support/private/current-notes');
const run=fs.mkdtempSync(path.join(work,'run-')),notes=path.join(run,'notes'),stateDir=path.join(run,'state');
fs.mkdirSync(notes);for(const name of fs.readdirSync(sourceNotes).filter(name=>name.endsWith('.txt')))fs.copyFileSync(path.join(sourceNotes,name),path.join(notes,name));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const decode=bytes=>bytes.toString('utf8').replace(/^\uFEFF/,'').replace(/\r\n|\r/g,'\n');
(async()=>{
 let child,browser,page,output='',stderr='';const report={checks:[],errors:[]};
 const pass=message=>{report.checks.push(message);console.log('PASS',message);};
 async function ready(){await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved'&&!document.querySelector('#editor').disabled);}
 async function tab(title){await page.getByRole('button',{name:title,exact:true}).click();await page.waitForFunction(title=>document.querySelector('#page-title').textContent===title&&!document.querySelector('#editor').disabled,title);}
 async function checkAlignment(){
  const geometry=await page.locator('.columns-row').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('.column-field')].map(cell=>{const r=cell.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})));
  assert.ok(geometry.length);const first=geometry[0];
  for(const cells of geometry){assert.equal(cells.length,first.length);for(let column=0;column<first.length;column++){assert.ok(Math.abs(cells[column].x-first[column].x)<.6);assert.ok(Math.abs(cells[column].width-first[column].width)<.6);assert.ok(Math.abs(cells[column].y-cells[0].y)<.6);assert.ok(Math.abs(cells[column].height-cells[0].height)<.6);}}
  const overflowing=await page.locator('.columns-row textarea').evaluateAll(fields=>fields.filter(field=>field.scrollHeight>field.clientHeight+2||field.getBoundingClientRect().height>field.closest('.columns-row').getBoundingClientRect().height+1).map(field=>field.getAttribute('aria-label')));
  assert.deepEqual(overflowing,[],'All cell text must fit its row height');
 }
 try{
  child=spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-m','french_notes.app','--data-dir',notes,'--state-dir',stateDir,'--no-browser','--idle-seconds','0'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.on('data',c=>output+=c);child.stderr.on('data',c=>stderr+=c);
  for(let i=0;i<100&&!output.includes('http://');i++)await sleep(70);const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(url,stderr);
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:960}});page.on('pageerror',e=>report.errors.push(e.message));
  await page.route('**/api/speech',route=>route.fulfill({status:200,contentType:'audio/mpeg',body:fs.readFileSync(path.join(__dirname,'fixtures/denise.mp3'))}));
  await page.goto(url);await ready();assert.ok(await page.locator('#columns-editor').isVisible());
  const titles=await page.locator('.category').allTextContents();assert.equal(titles.length,await page.evaluate(async()=>(await api('categories')).categories.length));
  for(const title of titles){await tab(title);await checkAlignment();}
  pass('Column boundaries and row heights align across every active tab, including the six-column expression layout');
  await tab('Words');await page.screenshot({path:path.join(work,'aligned-words.png'),fullPage:true});
  const source=path.join(notes,'wrods, verbs.txt'),before=fs.readFileSync(source);
  const field=page.locator('.columns-row textarea[lang=fa]').first(),old=await field.inputValue();
  assert.equal(await field.getAttribute('dir'),'rtl');await field.fill(old+'؛ یادداشت آزمایشی');
  assert.ok(fs.readFileSync(source).equals(before));await field.press('Control+s');await ready();assert.ok(decode(fs.readFileSync(source)).includes('؛ یادداشت آزمایشی'));
  await page.locator('#undo-save').click();await ready();assert.ok(fs.readFileSync(source).equals(before));
  pass('Cells remain directly editable; Ctrl+S saves and Undo Save restores exact source bytes');
  const fresh=page.locator('.columns-row textarea[lang=fr]').first(),original=await fresh.inputValue();await fresh.focus();await fresh.press('End');await fresh.pressSequentially('X');await fresh.press('Control+z');
  assert.equal(await fresh.inputValue(),original);pass('Native typing undo works inside an aligned cell');
  const raw=await page.locator('#editor').inputValue();await page.getByRole('button',{name:'Text',exact:true}).click();assert.equal(await page.locator('#editor').inputValue(),raw);
  const extra='\nUn exemple.    |    یک مثال    |    An example.\n';await page.locator('#editor').fill(raw+extra);await page.getByRole('button',{name:'Columns',exact:true}).click();
  assert.equal(await page.locator('.columns-row textarea[lang=fr]').last().inputValue(),'Un exemple.');await checkAlignment();
  await page.locator('.columns-row textarea[lang=en]').last().press('Control+s');await ready();await page.locator('#undo-save').click();await ready();assert.ok(fs.readFileSync(source).equals(before));
  pass('Switching between Columns and Text preserves the full note and allows adding rows');
  const draftField=page.locator('.columns-row textarea[lang=fa]').first();await draftField.fill(old+'؛ پیش‌نویس');await tab('Verbs');await tab('Words');assert.equal(await page.locator('.columns-row textarea[lang=fa]').first().inputValue(),old+'؛ پیش‌نویس');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Unsaved changes'&&!document.querySelector('#editor').disabled);assert.equal(await page.locator('.columns-row textarea[lang=fa]').first().inputValue(),old+'؛ پیش‌نویس');
  await page.locator('.columns-row textarea[lang=fa]').first().fill(old);await ready();pass('Column edits survive tab changes and reload as unsaved drafts');
  await tab('Additional Notes');await page.locator('#search').fill('Attacher quelque chose');const result=page.locator('.search-result').filter({has:page.locator('strong',{hasText:/^Additional Notes$/})}).first();await result.click();
  await page.waitForFunction(()=>document.activeElement?.tagName==='TEXTAREA'&&document.activeElement.lang==='fr');
  assert.equal(await page.evaluate(()=>document.activeElement.value.slice(document.activeElement.selectionStart,document.activeElement.selectionEnd)),'Attacher quelque chose');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  report.sampleRows=await page.locator('.columns-row').evaluateAll(rows=>rows.slice(0,4).map(row=>({y:row.getBoundingClientRect().y,height:row.getBoundingClientRect().height,cells:[...row.querySelectorAll('textarea')].map(field=>({text:field.value,y:field.getBoundingClientRect().y,height:field.getBoundingClientRect().height,scrollTop:field.scrollTop}))})));
  await page.screenshot({path:path.join(work,'aligned-additional.png'),fullPage:true});pass('Search selects the matching text inside its aligned cell');
  await tab('Words');const firstFrench=page.locator('.columns-row textarea[lang=fr]').first();await firstFrench.focus();await firstFrench.press('Control+Enter');
  await page.locator('.columns-row').nth(1).locator('textarea[lang=fr]').fill('Nouvelle ligne.');await page.locator('.columns-row').nth(1).locator('textarea[lang=fa]').fill('ردیف جدید');await page.locator('.columns-row').nth(1).locator('textarea[lang=en]').fill('New row.');
  await page.locator('#save').click();await ready();assert.ok(decode(fs.readFileSync(source)).includes('Nouvelle ligne.    |    ردیف جدید    |    New row.'));await page.locator('#undo-save').click();await ready();assert.ok(fs.readFileSync(source).equals(before));
  pass('Adding a row keeps three cells together and is undoable');
  for(const viewport of [{width:1100,height:800},{width:850,height:900},{width:375,height:812},{width:812,height:375}]){
   await page.setViewportSize(viewport);await checkAlignment();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(viewport.width===375){const panel=page.locator('#columns-editor');await panel.evaluate(el=>el.scrollLeft=el.scrollWidth);assert.ok(await panel.evaluate(el=>el.scrollLeft>0));await panel.evaluate(el=>el.scrollLeft=0);}
   await page.screenshot({path:path.join(work,`aligned-${viewport.width}.png`),fullPage:true});
  }
  pass('Long text wraps within aligned cells; narrow windows scroll the table without widening the page');
  assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.failure=error.stack;if(page)await page.screenshot({path:path.join(work,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{fs.writeFileSync(path.join(work,'ui-results.json'),JSON.stringify(report,null,2));if(browser)await browser.close();if(child)child.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
