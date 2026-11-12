const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..','..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

(async()=>{
  const testRoot=fs.mkdtempSync(path.join(process.env.BOT_CENTER_TEST_OUTPUT,'ui-run-'));
  const notes=path.join(testRoot,'notes');fs.mkdirSync(notes);
  for(const file of fs.readdirSync(path.join(__dirname,'support/private/append-notes')))fs.copyFileSync(path.join(__dirname,'support/private/append-notes',file),path.join(notes,file));
  const state=path.join(testRoot,'state');
  // Preserve the historical append-client contract against the installed
  // NotesStore API. The modern production editor remains a separate suite.
  const fixtureServer='from pathlib import Path;import sys;from french_notes.app import Server;from french_notes.store import NotesStore;s=Server(("127.0.0.1",0),NotesStore(Path(sys.argv[1]),Path(sys.argv[2])));print(s.origin,flush=True);s.serve_forever()';
  const child=spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-c',fixtureServer,notes,state],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let output='',stderr='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>stderr+=chunk);
  let browser;
  const report={started:new Date().toISOString(),tests:[],errors:[],folder:testRoot};
  const pass=name=>{report.tests.push(name);console.log('PASS',name);};
  try{
    for(let i=0;i<100&&!output.includes('http://');i++)await sleep(100);
    const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(url,'Server did not start: '+stderr);
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:960},deviceScaleFactor:1});
    page.on('pageerror',err=>report.errors.push(err.message));
    const response=await page.request.get(url),html=await response.text(),appToken=html.match(/name="app-token" content="([^"]+)"/)[1];
    const legacy=fs.readFileSync(path.join(root,'.venvs/french-editor/Lib/site-packages/french_notes/ui/index.html'),'utf8').replace('__APP_TOKEN__',appToken);
    await page.route(url+'/',route=>route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:legacy}));
    await page.addInitScript(folder=>localStorage.setItem('french-notes.drafts.'+folder+'.selected','Overall Examples.txt'),notes);
    await page.goto(url);await page.waitForSelector('.category.active');
    assert.equal(await page.locator('.category').count(),13);
    assert.equal(await page.locator('#page-title').textContent(),'Overall Examples');
    const panelEdges=await page.evaluate(()=>({reader:document.querySelector('.reader').getBoundingClientRect().bottom,composer:document.querySelector('.composer').getBoundingClientRect().bottom}));
    assert.ok(Math.abs(panelEdges.reader-panelEdges.composer)<2,'Reading and writing panels should align');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight),'Desktop layout should fit the app window');
    await page.screenshot({path:path.join(process.env.BOT_CENTER_TEST_OUTPUT,'French Notes.png'),fullPage:true});
    pass('All 13 historical append-client fixture categories load in the desktop layout');
    const source=path.join(notes,'Overall Examples.txt');const before=fs.readFileSync(source);
    const ten=Array.from({length:10},(_,i)=>`Exemple ajouté ${i+1} : un café, s’il vous plaît. فارسی ${i+1}`).join('\n');
    await page.locator('#draft').fill(ten);
    assert.equal(await page.locator('#draft-lines').textContent(),'10 lines ready to add');
    await page.screenshot({path:path.join(process.env.BOT_CENTER_TEST_OUTPUT,'paste-preview.png'),fullPage:true});
    await page.locator('#add-button').click();
    await page.waitForFunction(()=>document.getElementById('draft').value==='');
    const after=fs.readFileSync(source);assert.ok(after.subarray(0,before.length).equals(before));
    assert.ok(after.toString('utf8').includes(ten.split('\n')[9]));
    assert.ok((await page.locator('#toast-message').textContent()).includes('10 lines added'));
    pass('Paste 10 French/Persian lines, append exact original prefix, and confirm success');
    await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('undo').disabled&&!document.getElementById('draft').disabled);
    assert.ok(fs.readFileSync(source).equals(before));pass('Undo restores the complete original file byte for byte');
    const draft='Mon oreille me gratte.\nUne nouvelle phrase pour le test.';
    await page.locator('#draft').fill(draft);await page.locator('#add-button').click();await page.waitForSelector('dialog[open]');
    await page.locator('#duplicate-cancel').click();await page.waitForFunction(()=>!document.getElementById('draft').disabled);
    assert.ok(fs.readFileSync(source).equals(before));assert.equal(await page.locator('#draft').inputValue(),draft);
    pass('Duplicate review can be cancelled without changing notes or draft');
    await page.locator('#add-button').click();await page.waitForSelector('dialog[open]');await page.locator('#duplicate-skip').click();
    await page.waitForFunction(()=>document.getElementById('draft').value==='');
    const appended=fs.readFileSync(source).subarray(before.length).toString('utf8');
    assert.ok(appended.includes('Une nouvelle phrase'));assert.ok(!appended.includes('Mon oreille'));
    await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('undo').disabled&&!document.getElementById('draft').disabled);
    pass('Skip duplicates adds only the explicitly chosen new lines');
    await page.locator('#draft').fill('Mon oreille me gratte.');await page.locator('#add-button').click();await page.waitForSelector('dialog[open]');await page.locator('#duplicate-keep').click();
    await page.waitForFunction(()=>document.getElementById('draft').value==='');assert.ok(fs.readFileSync(source).length>before.length);
    await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('undo').disabled&&!document.getElementById('draft').disabled);
    pass('Explicit keep-all permits repeated examples');
    await page.locator('#draft').fill('Un brouillon conservé.');await page.getByRole('button',{name:'Subjonctif',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('page-title').textContent==='Subjonctif');
    assert.equal(await page.locator('#draft').inputValue(),'');
    await page.getByRole('button',{name:'Overall Examples',exact:true}).click();await page.waitForFunction(()=>document.getElementById('page-title').textContent==='Overall Examples');
    assert.equal(await page.locator('#draft').inputValue(),'Un brouillon conservé.');
    await page.reload();await page.waitForSelector('.category.active');assert.equal(await page.locator('#draft').inputValue(),'Un brouillon conservé.');
    pass('Separate drafts survive category changes and reload');
    await page.locator('#search').fill('indispensable');await page.waitForSelector('.search-result');await page.locator('.search-result').first().click();await page.waitForSelector('.target-line');
    assert.ok((await page.locator('.target-line').textContent()).toLowerCase().includes('indispensable'));
    pass('Global search opens the matching category and highlights the line');
    await page.getByRole('button',{name:'Overall Examples',exact:true}).click();await page.waitForFunction(()=>document.getElementById('page-title').textContent==='Overall Examples');
    await page.locator('#draft').fill('Ne pas perdre mon brouillon.');
    fs.appendFileSync(source,'\r\nUne modification externe.\r\n');const external=fs.readFileSync(source);
    await page.locator('#add-button').click();await page.waitForSelector('#form-error:not([hidden])');
    assert.ok(fs.readFileSync(source).equals(external));assert.equal(await page.locator('#draft').inputValue(),'Ne pas perdre mon brouillon.');
    await page.locator('#refresh').click();await page.waitForFunction(()=>document.getElementById('form-error').hidden&&!document.getElementById('draft').disabled&&document.getElementById('notes').getAttribute('aria-busy')==='false');
    await page.locator('#draft').press('Control+Enter');await page.waitForFunction(()=>document.getElementById('draft').value==='');
    await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('undo').disabled&&!document.getElementById('draft').disabled);
    assert.ok(fs.readFileSync(source).equals(external));pass('External edits block stale saves; refresh and keyboard append preserve them');
    const csrf=await page.evaluate(async()=>{const response=await fetch('/api/append',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});return response.status;});assert.equal(csrf,403);
    pass('Write API refuses a request without the app token');
    await page.locator('#draft').fill('<img src=x onerror=alert(1)> فارسی');await page.locator('#add-button').click();await page.waitForFunction(()=>document.getElementById('draft').value==='');
    assert.equal(await page.locator('#notes img').count(),0);assert.ok((await page.locator('#notes').textContent()).includes('<img src=x'));
    await page.locator('#undo').click();await page.waitForFunction(()=>document.getElementById('undo').disabled&&!document.getElementById('draft').disabled);
    pass('Pasted markup is displayed as text, with Persian preserved');
    for(const viewport of [{width:1100,height:800},{width:850,height:900},{width:390,height:844}]){
      await page.setViewportSize(viewport);await sleep(120);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow at '+viewport.width);
      await page.screenshot({path:path.join(process.env.BOT_CENTER_TEST_OUTPUT,`layout-${viewport.width}.png`),fullPage:true});
    }
    pass('Layouts at 1440, 1100, 850 and 390 pixels have no horizontal overflow');
    assert.deepEqual(report.errors,[]);pass('No browser JavaScript errors');
    report.status='passed';
  }catch(error){report.status='failed';report.failure=error.stack;throw error;}
  finally{
    fs.writeFileSync(path.join(process.env.BOT_CENTER_TEST_OUTPUT,'ui-results.json'),JSON.stringify(report,null,2));
    if(browser)await browser.close();child.kill();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
