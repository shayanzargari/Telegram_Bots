const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..','..'),work=process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french');
const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'support/private/indicatif-ledger.json'),'utf8'));
const run=fs.mkdtempSync(path.join(work,'browser-')),notes=path.join(run,'notes'),stateDir=path.join(run,'state');
fs.cpSync(path.join(__dirname,'support/private/indicatif-notes'),notes,{recursive:true});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const normal=text=>text.replace(/^\uFEFF/,'').replace(/\r\n|\r/g,'\n');
const sections=['Présent','Passé composé','Imparfait','Plus-que-parfait','Futur simple','Futur antérieur','Futur proche','Passé récent','Passé simple','Formules et fragments'];
(async()=>{
 let child,browser,page;
 const report={checks:[],errors:[],fixture:run};const pass=name=>{report.checks.push(name);console.log('PASS',name);};
 async function start(){let output='',errors='';child=spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-m','french_notes.app','--data-dir',notes,'--state-dir',stateDir,'--no-browser','--idle-seconds','0'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.on('data',data=>output+=data);child.stderr.on('data',data=>errors+=data);for(let i=0;i<100&&!output.includes('http://');i++)await sleep(80);const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(url,errors);return url;}
 async function ready(status='Saved'){await page.waitForFunction(status=>document.querySelector('#save-status').textContent===status&&!document.querySelector('#editor').disabled,status);}
 async function tab(title){await page.getByRole('button',{name:title,exact:true}).click();await page.waitForFunction(title=>document.querySelector('#page-title').textContent===title&&!document.querySelector('#editor').disabled,title);}
 const table=name=>page.locator('.tense-table').filter({has:page.getByRole('heading',{name,exact:true})});
 async function sentence(text){const index=await page.locator('.columns-row').evaluateAll((rows,text)=>rows.findIndex(row=>[...row.querySelectorAll('textarea[lang=fr]')].some(field=>field.value===text)),text);assert.ok(index>=0,text);return page.locator('.columns-row').nth(index);}
 try{
  let url=await start();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1920,height:1080}});page.on('pageerror',error=>report.errors.push(error.message));
  const speech=[];await page.route('**/api/speech',route=>{speech.push(route.request().postDataJSON());return route.fulfill({status:200,contentType:'audio/mpeg',body:fs.readFileSync(path.join(__dirname,'fixtures/denise.mp3'))});});
  await page.goto(url);await ready();const titles=await page.locator('.category').allTextContents();
  assert.deepEqual(titles.slice(0,2),['Indicatif','Conditionnel']);assert.equal(titles.length,Object.keys(manifest.counts).length);assert.ok(!titles.includes('Overall Examples')&&!titles.includes('Passé Composé'));assert.equal(titles[titles.indexOf('Faire')+1],'Avoir');
  pass('Manifest tab count, Indicatif/Conditionnel order, Avoir position, and removal of legacy tabs');
  for(const title of titles){
   await tab(title);const entries=await page.locator('.columns-row:not(.section-heading)').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('textarea')].map(field=>field.value)));
   for(const values of entries){assert.ok(values.length===(title==='Expressions'?6:3),title);assert.ok(values.every(value=>value.trim()),title+' missing translation');}
   const direction=await page.locator('.columns-row textarea[lang=fa]').evaluateAll(fields=>fields.every(field=>field.dir==='rtl'));assert.ok(direction,title);
  }
  pass('Every active tab opens with complete sentence/vocabulary cells and RTL Farsi; Expressions retains six columns');
  await tab('Indicatif');assert.deepEqual(await page.locator('.tense-title h2').allTextContents(),sections);
  for(const section of sections)assert.equal(await table(section).locator('.columns-row').count(),manifest.table_counts['Indicatif.txt'][section]||0,section);
  assert.equal(await table('Plus-que-parfait').locator('.columns-row').count(),0);assert.equal(await table('Futur antérieur').locator('.columns-row').count(),0);
  const mixed=await sentence("Tu as laissé les courses dans la voiture parce que c'était trop lourd.");
  assert.equal(await mixed.evaluate(node=>node.closest('.tense-table').dataset.section),'Passé composé');assert.equal(await mixed.locator('.tense-badges').textContent(),'Imparfait');
  assert.equal(await (await sentence('Ils ont démoli tout ce qu\'il avait construit.')).locator('.tense-badges').textContent(),'Plus-que-parfait');
  for(const [text,section]of [['Elle est étonnée par son comportement.','Présent'],['Il doit appeler un informaticien car il a attrapé un virus.','Présent'],['Je vais enlever ce bijou car il me gêne.','Futur proche'],['Il sera plombier.','Futur simple']])assert.equal(await (await sentence(text)).evaluate(node=>node.closest('.tense-table').dataset.section),section,text);
  pass('Every table count matches the ledger; mixed tenses, passive states, futur proche, and empty sections classify correctly');
  await page.screenshot({path:path.join(work,'indicatif-desktop.png'),fullPage:true});
  const source=path.join(notes,'Indicatif.txt'),original=fs.readFileSync(source);
  await mixed.locator('textarea[lang=fr]').fill("Tu as laissé les courses dans la voiture parce que c'était trop lourd. test");assert.equal(await mixed.locator('.tense-badges').count(),0);
  await page.keyboard.press('Control+z');assert.equal(await mixed.locator('.tense-badges').textContent(),'Imparfait');
  await mixed.locator('.move-section').selectOption('Imparfait');assert.equal(await (await sentence("Tu as laissé les courses dans la voiture parce que c'était trop lourd.")).evaluate(node=>node.closest('.tense-table').dataset.section),'Imparfait');
  await page.keyboard.press('Control+z');assert.equal(await (await sentence("Tu as laissé les courses dans la voiture parce que c'était trop lourd.")).evaluate(node=>node.closest('.tense-table').dataset.section),'Passé composé');
  await ready();assert.equal(await page.locator('#editor').inputValue(),normal(original.toString('utf8')));
  pass('French edits clear secondary badges; moving between tables and Ctrl+Z restore the full original entry');
  await table('Plus-que-parfait').getByRole('button',{name:'Add row to Plus-que-parfait',exact:true}).click();
  let added=table('Plus-que-parfait').locator('.columns-row').last();await added.locator('textarea[lang=fr]').fill('Elle avait déjà quitté la maison.');await added.locator('textarea[lang=fa]').fill('او قبلاً خانه را ترک کرده بود.');await added.locator('textarea[lang=en]').fill('She had already left the house.');
  await added.locator('textarea[lang=en]').press('Enter');assert.equal(await table('Plus-que-parfait').locator('.columns-row').count(),2);await page.keyboard.press('Control+z');assert.equal(await table('Plus-que-parfait').locator('.columns-row').count(),1);
  await page.locator('#save').click();await ready();assert.ok(fs.readFileSync(source,'utf8').includes('Elle avait déjà quitté la maison.'));await page.locator('#undo-save').click();await ready();assert.ok(fs.readFileSync(source).equals(original));
  pass('Add Row and Enter stay in the selected empty table; save and Undo Save preserve exact bytes');
  const first=table('Présent').locator('.columns-row').first();const french=await first.locator('textarea[lang=fr]').inputValue();await first.locator('.remove-row').click();await page.keyboard.press('Control+z');assert.equal(await table('Présent').locator('.columns-row').first().locator('textarea[lang=fr]').inputValue(),french);await page.keyboard.press('Control+Shift+z');await page.locator('#undo-remove').click();assert.equal(await table('Présent').locator('.columns-row').first().locator('textarea[lang=fr]').inputValue(),french);await ready();
  pass('Remove, Ctrl+Z/redo, and Undo Remove retain the row and its section');
  await page.locator('#search').fill('demolished everything');await page.locator('.search-result').filter({has:page.locator('strong',{hasText:/^Indicatif$/})}).first().click();await page.waitForFunction(()=>document.activeElement?.lang==='en');assert.equal(await page.evaluate(()=>document.activeElement.value.slice(document.activeElement.selectionStart,document.activeElement.selectionEnd)),'demolished everything');
  await first.locator('.row-audio').click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');assert.equal(await page.evaluate(()=>frenchAudio.lastPlayed.text),french);await page.waitForFunction(()=>frenchAudio.audio.currentTime>0.1);await page.locator('#stop-speech').click();assert.ok(speech.every(item=>!/[\u0600-\u06ff]/u.test(item.text)));
  pass('Search selects the translated cell in the right table; pronunciation plays French only');
  await table('Futur antérieur').getByRole('button',{name:'Add row to Futur antérieur',exact:true}).click();let draft=table('Futur antérieur').locator('.columns-row').last();await draft.locator('textarea[lang=fr]').fill('Elle aura fini.');await draft.locator('textarea[lang=fa]').focus();await sleep(350);child.kill();url=await start();await page.goto(url);await ready('Unsaved changes');assert.equal(await table('Futur antérieur').locator('.columns-row textarea[lang=fr]').inputValue(),'Elle aura fini.');await table('Futur antérieur').locator('.remove-row').click();await ready();assert.ok(fs.readFileSync(source).equals(original));
  pass('Uncommitted rows survive a server restart without altering saved notes');
  await tab('Conditionnel');assert.equal(await page.locator('.row-audio').count(),manifest.counts['Conditionnel.txt']);assert.equal(await table('Passé').locator('.columns-row').count(),0);
  await tab('Avoir');assert.ok(await sentence('Avoir besoin de'));assert.ok(await sentence("J'ai confiance en lui."));assert.equal(await page.locator('.columns-row').count(),manifest.counts['Avoir.txt']);
  await tab('Indicatif');assert.ok(await sentence("J'ai raté le permis trois fois."));await tab('Verbs');assert.equal(await page.locator('.columns-heading').first().locator('span').first().textContent(),'Infinitif présent');assert.ok(await sentence('se trouver'));assert.ok(await sentence('Convaincre'));
  pass('Conditionnel uses existing examples; Avoir expressions are separated from auxiliaries; Verbs uses infinitif présent');
  await page.goto(url+'/?note='+encodeURIComponent('Overall Examples.txt'));await ready();assert.equal(await page.locator('#page-title').textContent(),'Indicatif');
  await page.evaluate(()=>{state.drafts['Overall Examples.txt']={text:'Old unsaved phrase',revision:'a'.repeat(64),section_revision:'b'.repeat(64)};localDrafts();localStorage.setItem(state.storageKey+'.selected','passe compose.txt');});await page.reload();await ready();assert.equal(await page.locator('#page-title').textContent(),'Indicatif');assert.ok(await page.locator('#recovered-drafts').isVisible());assert.ok(fs.readFileSync(source).equals(original));
  const download=page.waitForEvent('download');await page.locator('#download-recovered-drafts').click();await (await download).saveAs(path.join(work,'recovered-drafts-fixture.json'));
  pass('Old links and selections redirect safely; stale source drafts are downloadable and cannot overwrite merged notes');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));const panel=page.locator('#columns-editor');assert.ok(await panel.evaluate(node=>node.scrollWidth>node.clientWidth));await panel.evaluate(node=>node.scrollLeft=node.scrollWidth);assert.ok(await table('Présent').locator('.move-section').first().isVisible());await page.screenshot({path:path.join(work,'indicatif-narrow.png'),fullPage:true});
  pass('Narrow windows scroll the tables inside the editor with movement controls reachable');
  assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.failure=error.stack;if(page)await page.screenshot({path:path.join(work,'notebooks-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{fs.writeFileSync(path.join(work,'notebooks-ui-results.json'),JSON.stringify(report,null,2));if(browser)await browser.close();if(child)child.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
