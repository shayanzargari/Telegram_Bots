const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..','..');
const work=process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french');
const run=fs.mkdtempSync(path.join(work,'expressions-ui-'));
const notes=path.join(run,'notes'),stateDir=path.join(run,'state');fs.mkdirSync(notes);
for(const name of fs.readdirSync(path.join(__dirname,'support/private/expression-notes')))fs.copyFileSync(path.join(__dirname,'support/private/expression-notes',name),path.join(notes,name));
const plan=JSON.parse(fs.readFileSync(path.join(__dirname,'support/private/expression-plan.json'),'utf8'));
const staged=spawnSync(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-c',
 "import json,sys,uuid;from pathlib import Path;from french_notes.editor_store import EditorStore;s=EditorStore(Path(sys.argv[1]),Path(sys.argv[2]));p=json.loads(Path(sys.argv[3]).read_text('utf-8'));[(lambda d:s.save_document(n,e['text'],d['revision'],d['section_revision'],str(uuid.uuid4())))(s.read_document(n)) for n,e in p['documents'].items()]",
 notes,stateDir,path.join(__dirname,'support/private/expression-plan.json')],{cwd:root,windowsHide:true,encoding:'utf8'});
assert.equal(staged.status,0,staged.stderr);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 let child,browser,page;
 const report={checks:[],errors:[],fixture:run};
 const pass=message=>{report.checks.push(message);console.log('PASS',message);};
 async function start(){
  let output='',errors='';
  child=spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-m','french_notes.app','--data-dir',notes,'--state-dir',stateDir,'--no-browser','--idle-seconds','0'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  child.stdout.on('data',data=>output+=data);child.stderr.on('data',data=>errors+=data);
  for(let i=0;i<100&&!output.includes('http://');i++)await sleep(80);
  const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(url,errors);return url;
 }
 async function stop(){if(!child||child.exitCode!==null||child.signalCode!==null)return;const current=child;await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture editor did not exit before restart')),10000);current.once('exit',()=>{clearTimeout(timer);resolve();});current.kill();});}
 async function saved(){await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved'&&!document.querySelector('#editor').disabled);}
 async function tab(name){await page.getByRole('button',{name,exact:true}).click();await page.waitForFunction(name=>document.querySelector('#page-title').textContent===name&&!document.querySelector('#editor').disabled,name);}
 try{
  let url=await start();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1920,height:1080}});page.on('pageerror',error=>report.errors.push(error.message));
  const requests=[];await page.route('**/api/speech',route=>{requests.push(route.request().postDataJSON());return route.fulfill({status:200,contentType:'audio/mpeg',body:fs.readFileSync(path.join(__dirname,'fixtures/denise.mp3'))});});
  await page.goto(url);await saved();
  const titles=await page.locator('.category').allTextContents();assert.equal(titles.length,15);
  for(const title of titles){await tab(title);assert.equal(await page.locator('.columns-heading span:not(.row-heading)').count(),title==='Expressions'?6:3);}
  pass('All 15 tabs open; Expressions has six columns and the other tabs retain three');
  await tab('Expressions');const expressionFile=path.join(notes,'Expressions.txt'),original=fs.readFileSync(expressionFile);
  assert.deepEqual(await page.locator('.columns-heading span').allTextContents(),['Expression','Farsi meaning','English meaning','French example','Farsi translation','English translation','Rows']);
  const expected=plan.documents['Expressions.txt'].text.split('\n').filter(line=>line.includes('    |    ')).map(line=>line.split('    |    '));
  assert.equal(await page.locator('.columns-row').count(),expected.length);
  for(const [index,values]of expected.entries()){
   const fields=page.locator('.columns-row').nth(index).locator('textarea');
   assert.deepEqual(await fields.evaluateAll(items=>items.map(item=>item.value)),values);
   assert.deepEqual(await fields.evaluateAll(items=>items.map(item=>[item.lang,item.dir])),[['fr','ltr'],['fa','rtl'],['en','ltr'],['fr','ltr'],['fa','rtl'],['en','ltr']]);
  }
  assert.ok(fs.readFileSync(expressionFile).equals(original));
  pass('Every staged expression, both meanings, original French example, and translations render exactly without saving');
  await page.screenshot({path:path.join(work,'expressions-desktop.png'),fullPage:true});
  const first=page.locator('.columns-row').first(),fields=first.locator('textarea');
  for(let column=0;column<6;column++)await fields.nth(column).fill(expected[0][column]+' test');
  await page.locator('#save').click();await saved();
  assert.deepEqual(fs.readFileSync(expressionFile,'utf8').replace(/^\uFEFF/,'').split(/\r?\n/)[0].split('    |    '),expected[0].map(value=>value+' test'));
  await page.locator('#undo-save').click();await saved();assert.ok(fs.readFileSync(expressionFile).equals(original));
  pass('All six cells save correctly; Undo Save restores the exact original bytes');
  await page.locator('#search').fill('out of order');await page.locator('.search-result').filter({has:page.locator('strong',{hasText:/^Expressions$/})}).first().click();
  await page.waitForFunction(()=>!document.querySelector('#editor').disabled&&document.activeElement?.dataset.column==='2');
  assert.equal(await page.evaluate(()=>document.activeElement.value.slice(document.activeElement.selectionStart,document.activeElement.selectionEnd).toLowerCase()),'out of order');
  pass('Search focuses and selects the correct English meaning cell after the added columns');
  await first.locator('.row-audio').click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');
  assert.equal(await page.evaluate(()=>frenchAudio.lastPlayed.text),expected[0][3]);
  await page.waitForFunction(()=>frenchAudio.audio&&frenchAudio.audio.currentTime>0.1);await page.locator('#stop-speech').click();
  assert.ok(requests.every(request=>!/[\u0600-\u06ff]/u.test(request.text)));
  pass('Pronunciation plays the French example using a local MP3 fixture and sends no Farsi meaning or translation');
  await fields.nth(5).fill('First translation\nSecond translation');
  const added=page.locator('.columns-row').nth(1).locator('textarea');assert.equal(await added.count(),6);assert.equal(await added.nth(5).inputValue(),'Second translation');
  await page.keyboard.press('Control+z');assert.equal(await page.locator('#editor').inputValue(),plan.documents['Expressions.txt'].text);
  pass('Pasting multiple lines into the sixth column creates six-cell rows and Ctrl+Z restores the original');
  await page.locator('#add-row').click();const last=page.locator('.columns-row').last();assert.equal(await last.locator('textarea').count(),6);
  await last.locator('textarea').nth(0).fill('avoir confiance');
  await last.locator('textarea').nth(2).fill('to trust');
  await last.locator('.row-audio').click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');
  assert.equal(await page.evaluate(()=>frenchAudio.lastPlayed.text),'avoir confiance');await page.locator('#stop-speech').click();
  await last.locator('textarea').nth(5).focus();await page.keyboard.press('Control+Enter');assert.equal(await page.locator('.columns-row').last().locator('textarea').count(),6);
  await page.keyboard.press('Control+z');
  await tab('Words');await tab('Expressions');assert.equal(await page.locator('.columns-row').last().locator('textarea').nth(2).inputValue(),'to trust');
  await page.waitForFunction(async()=>{const saved=await api('drafts');return saved.drafts['Expressions.txt']?.text.includes('avoir confiance');});
  const previousUrl=url;await stop();url=await start();assert.notEqual(url,previousUrl,'Draft recovery must use a different localhost port');await page.goto(url);await page.waitForFunction(()=>!document.querySelector('#editor').disabled&&document.querySelector('#page-title').textContent==='Expressions'&&document.querySelector('#save-status').textContent==='Unsaved changes');
  assert.equal(await page.locator('.columns-row').last().locator('textarea').nth(2).inputValue(),'to trust');
  await page.locator('.columns-row').last().locator('.remove-row').click();await page.locator('#undo-remove').click();
  assert.equal(await page.locator('.columns-row').last().locator('textarea').nth(0).inputValue(),'avoir confiance');
  await page.locator('.columns-row').last().locator('.remove-row').click();await saved();assert.ok(fs.readFileSync(expressionFile).equals(original));
  pass('Six-cell rows, blank-example pronunciation, Ctrl+Enter, drafts after restart, and remove/restore work');
  const third=page.locator('.columns-row').nth(2).locator('textarea');await third.nth(0).focus();
  for(let column=1;column<6;column++){
   await page.keyboard.press('Tab');
   if(column===4){assert.ok(await page.evaluate(()=>document.activeElement.classList.contains('row-audio')));await page.keyboard.press('Tab');}
   assert.equal(await page.evaluate(()=>document.activeElement.dataset.column),String(column));
  }
  await third.nth(5).press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.dataset.column),'5');assert.equal(await page.evaluate(()=>document.activeElement.dataset.line),'3');
  pass('Tab follows all six columns in order and Enter moves down the same column');
  const legacy="J'ai besoin d'aide.    |    به کمک نیاز دارم.    |    I need help.\r\n";
  fs.writeFileSync(expressionFile,legacy);await page.locator('#refresh').click();await saved();
  assert.deepEqual(await page.locator('.columns-row').first().locator('textarea').evaluateAll(items=>items.map(item=>item.value)),['','','',"J'ai besoin d'aide.",'به کمک نیاز دارم.','I need help.']);
  await page.locator('#search').fill('need help');await page.locator('.search-result').filter({has:page.locator('strong',{hasText:/^Expressions$/})}).first().click();
  await page.waitForFunction(()=>document.activeElement?.dataset.column==='5');
  assert.equal(await page.evaluate(()=>document.activeElement.value.slice(document.activeElement.selectionStart,document.activeElement.selectionEnd)),'need help');
  assert.equal(fs.readFileSync(expressionFile,'utf8'),legacy);
  await page.locator('.columns-row').first().locator('textarea').nth(1).fill('نیاز داشتن');await page.locator('#save').click();await saved();
  assert.equal(fs.readFileSync(expressionFile,'utf8').split(/\r?\n/)[0].split('    |    ').length,6);
  await page.locator('#undo-save').click();await saved();assert.equal(fs.readFileSync(expressionFile,'utf8'),legacy);
  pass('Legacy three-column notes open and search in six columns without rewriting; editing and Undo Save remain compatible');
  fs.writeFileSync(expressionFile,original);await page.locator('#refresh').click();await saved();
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const panel=page.locator('#columns-editor');assert.ok(await panel.evaluate(node=>node.scrollWidth>node.clientWidth));
  await panel.evaluate(node=>node.scrollLeft=node.scrollWidth);assert.ok(await page.locator('.remove-row').first().isVisible());
  await page.screenshot({path:path.join(work,'expressions-narrow.png'),fullPage:true});
  pass('At narrow widths the expression table scrolls inside the editor and all controls remain reachable');
  assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.failure=error.stack;if(page)await page.screenshot({path:path.join(work,'expressions-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{fs.writeFileSync(path.join(work,'expressions-ui-results.json'),JSON.stringify(report,null,2));if(browser)await browser.close();await stop();}
})().catch(error=>{console.error(error);process.exitCode=1;});
