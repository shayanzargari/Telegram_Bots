const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..','..'),work=process.env.FRENCH_NOTES_SPEECH_CHECKS||process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french');
const sourceNotes=process.env.FRENCH_NOTES_TEST_SOURCE||path.join(__dirname,'support/private/current-notes');
const run=fs.mkdtempSync(path.join(work,'run-')),notes=path.join(run,'notes'),stateDir=path.join(run,'state');fs.mkdirSync(notes);
const initial={};for(const name of fs.readdirSync(sourceNotes).filter(name=>name.endsWith('.txt'))){const bytes=fs.readFileSync(path.join(sourceNotes,name));fs.writeFileSync(path.join(notes,name),bytes);initial[name]=crypto.createHash('sha256').update(bytes).digest('hex');}
// Exercise the actual worker/cache/MP3 path with a strict local speech provider.
// Selected voice/rate/text still cross the real Unicode JSON worker protocol.
const provider=path.join(run,'provider'),packageDir=path.join(provider,'edge_tts');fs.mkdirSync(packageDir,{recursive:true});
fs.writeFileSync(path.join(packageDir,'__init__.py'),'import os\nfrom pathlib import Path\nclass Communicate:\n def __init__(self,text,voice,**options): self.text=text;self.voice=voice;self.rate=options["rate"]\n async def save(self,output): Path(output).write_bytes(Path(os.environ["FRENCH_FIXTURE_MP3"]).read_bytes())\n');
fs.writeFileSync(path.join(packageDir,'communicate.py'),'import ssl\n_SSL_CTX=ssl.create_default_context()\n');
fs.writeFileSync(path.join(packageDir,'exceptions.py'),'class NoAudioReceived(Exception): pass\n');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 let child,browser,page,output='',stderr='';const report={checks:[],errors:[],speechRequests:[]};const pass=message=>{report.checks.push(message);console.log('PASS',message);};
 async function tab(title){await page.getByRole('button',{name:title,exact:true}).click();await page.waitForFunction(title=>document.querySelector('#page-title').textContent===title&&!document.querySelector('#editor').disabled,title);}
 try{
  child=spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-m','french_notes.app','--data-dir',notes,'--state-dir',stateDir,'--no-browser','--idle-seconds','0'],{cwd:root,env:{...process.env,PYTHONPATH:provider,FRENCH_FIXTURE_MP3:path.join(__dirname,'fixtures/denise.mp3')},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.on('data',c=>output+=c);child.stderr.on('data',c=>stderr+=c);
  for(let i=0;i<100&&!output.includes('http://');i++)await sleep(70);const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(url,stderr);
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:960}});page.on('pageerror',e=>report.errors.push(e.message));
  page.on('request',request=>{if(request.url().endsWith('/api/speech'))report.speechRequests.push(request.postDataJSON());});
  await page.goto(url);await page.waitForFunction(()=>!document.querySelector('#editor').disabled);
  let total=0;for(const title of await page.locator('.category').allTextContents()){await tab(title);const count=await page.locator('.row-audio').count();assert.ok(count>0,title);total+=count;}
  const expectedAudio=await page.evaluate(async()=>{let count=0;for(const item of (await api('categories')).categories){const doc=await api('document?name='+encodeURIComponent(item.name));count+=doc.text.split('\n').filter(line=>{const cells=line.split('    |    ');return [3,4,6].includes(cells.length)&&cells[2].trim()!=='English';}).length;}return count;});assert.equal(total,expectedAudio);pass('Every structured French entry has a play button across the current catalogue');
  await tab('Words');
  const index=await page.locator('.columns-row textarea[lang=fr]').evaluateAll(fields=>fields.findIndex(field=>field.value==='Oreille'));assert.ok(index>=0);
  const button=page.locator('.columns-row').nth(index).locator('.row-audio');await button.click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');
  assert.deepEqual(await page.evaluate(()=>frenchAudio.lastPlayed),{text:'Oreille',voice:'denise',speed:'1'});assert.equal(await button.getAttribute('aria-pressed'),'true');
  await page.waitForFunction(()=>frenchAudio.audio&&frenchAudio.audio.currentTime>0.1);
  await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='');assert.equal(await button.getAttribute('aria-pressed'),'false');
  pass('Real French neural MP3 audio decodes, plays, and finishes; only the French cell is spoken');
  const requestsBefore=report.speechRequests.filter(request=>request.text==='Oreille').length;
  report.prepared_playback_ms=await button.evaluate(async button=>{const start=performance.now();await frenchAudio.play('Oreille',button);return performance.now()-start;});assert.ok(report.prepared_playback_ms<400,`Prepared playback: ${report.prepared_playback_ms} ms`);assert.equal(report.speechRequests.filter(request=>request.text==='Oreille').length,requestsBefore);await page.locator('#stop-speech').click();pass('Prepared audio starts in under 400 ms without making another speech request');
  await page.locator('#speech-speed').selectOption('0.8');await button.click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');assert.equal(await page.evaluate(()=>frenchAudio.audio.playbackRate),1);assert.equal((await page.evaluate(()=>frenchAudio.lastPlayed)).speed,'0.8');
  await page.locator('#stop-speech').click();assert.equal(await page.locator('#speech-status').textContent(),'');assert.equal(await button.getAttribute('aria-pressed'),'false');
  pass('Slow speech is generated by the service; Stop Audio works without changing the note');
  await page.locator('#speech-speed').selectOption('1');
  for(const voice of ['henri','vivienne']){
   await page.getByLabel('Voice',{exact:true}).selectOption(voice);await button.click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');assert.equal((await page.evaluate(()=>frenchAudio.lastPlayed)).voice,voice);await page.waitForFunction(()=>frenchAudio.audio&&frenchAudio.audio.currentTime>0.1);await page.locator('#stop-speech').click();
  }
  pass('Henri and Vivienne selections cross the real worker protocol and play the local MP3 fixture');
  await button.click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');await page.locator('#speech-voice').selectOption('henri');assert.equal(await page.locator('#speech-status').textContent(),'');
  await page.locator('#speech-speed').selectOption('0.8');await page.reload();await page.waitForFunction(()=>!document.querySelector('#editor').disabled);assert.equal(await page.locator('#speech-voice').inputValue(),'henri');assert.equal(await page.locator('#speech-speed').inputValue(),'0.8');await tab('Words');await page.locator('#speech-voice').selectOption('denise');await page.locator('#speech-speed').selectOption('1');
  pass('Changing voices stops playback; voice and speed choices survive reload');
  await button.click();await button.click();await sleep(200);assert.equal(await page.locator('#speech-status').textContent(),'');await button.click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');await tab('Verbs');assert.equal(await page.locator('#speech-status').textContent(),'');
  pass('Repeated clicks and tab changes stop the previous audio');
  const first=page.locator('.columns-row textarea[lang=fr]').first(),old=await first.inputValue();await first.fill('Apprendre le français');await page.locator('.row-audio').first().click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');assert.equal((await page.evaluate(()=>frenchAudio.lastPlayed)).text,'Apprendre le français');await page.locator('#stop-speech').click();await first.fill(old);
  pass('Edited French text is pronounced immediately without saving');
  const cache=path.join(stateDir,'speech'),mp3Files=fs.readdirSync(cache).filter(name=>/^[a-f0-9]{64}\.mp3$/.test(name));assert.ok(mp3Files.length>=5);const cached=Object.fromEntries(mp3Files.map(name=>[name,fs.statSync(path.join(cache,name)).mtimeMs]));
  const token=await page.locator('meta[name=app-token]').getAttribute('content');const response=await page.request.post(url+'/api/speech',{headers:{'X-Notes-Token':token},data:{text:'Oreille'}});assert.equal(response.status(),200);assert.match(response.headers()['content-type'],/^audio\/mpeg/);const bytes=await response.body();assert.equal(bytes.subarray(0,3).toString('hex'),'fff364');assert.deepEqual(Object.fromEntries(mp3Files.map(name=>[name,fs.statSync(path.join(cache,name)).mtimeMs])),cached);
  assert.equal((await page.request.post(url+'/api/speech',{data:{text:'Bonjour'}})).status(),403);assert.equal((await page.request.post(url+'/api/speech',{headers:{'X-Notes-Token':token},data:{text:''}})).status(),400);
  for(const data of [{text:'Bonjour',voice:'unknown'},{text:'Bonjour',speed:'0.1'},{text:'Bonjour',voice:[]},{text:'Bonjour',speed:null}])assert.equal((await page.request.post(url+'/api/speech',{headers:{'X-Notes-Token':token},data})).status(),400);
  pass('Generated audio is cached; the audio API validates input and requires the app token');
  await page.route('**/api/speech',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'The French voice service is unavailable. Check your internet connection and try again; cached phrases still work.'})}));
  await first.fill('Une phrase indisponible');
  await page.locator('.row-audio').first().click();await page.waitForFunction(()=>!document.querySelector('#error').hidden);assert.match(await page.locator('#error').textContent(),/internet/);assert.equal(await page.locator('#stop-speech').isVisible(),false);assert.equal(await page.locator('.row-audio').first().getAttribute('aria-pressed'),'false');await page.unroute('**/api/speech');
  await page.locator('.row-audio').first().click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent==='Playing French');assert.equal(await page.locator('#error').isVisible(),false);await page.locator('#stop-speech').click();await first.fill(old);
  pass('Service failure shows a useful error; retry clears the error and plays again');
  const delayed=[];await page.route('**/api/speech',async route=>{delayed.push(route);await sleep(400);await route.fulfill({status:200,contentType:'audio/mpeg',body:bytes}).catch(()=>{});});await first.fill('Une nouvelle phrase à préparer');await page.locator('.row-audio').first().click();await page.waitForFunction(()=>document.querySelector('#speech-status').textContent.includes('Preparing'));await page.locator('#stop-speech').click();await sleep(650);assert.equal(await page.locator('#speech-status').textContent(),'');assert.equal(await page.evaluate(()=>frenchAudio.audio),null);assert.ok(delayed.length);await page.unroute('**/api/speech');await first.fill(old);
  pass('Cancel during generation prevents a late response from starting playback');
  for(const [name,digest]of Object.entries(initial))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(notes,name))).digest('hex'),digest);
  pass('Listening leaves every note file unchanged');
  await page.screenshot({path:path.join(work,'speech-controls.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.equal(await page.getByLabel('Voice',{exact:true}).isVisible(),true);assert.equal(await page.getByLabel('Speed',{exact:true}).isVisible(),true);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(work,'speech-controls-narrow.png'),fullPage:true});pass('Voice and speed controls remain visible at a narrow window width');assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.failure=error.stack;if(page)await page.screenshot({path:path.join(work,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{fs.writeFileSync(path.join(work,'ui-results.json'),JSON.stringify(report,null,2));if(browser)await browser.close();if(child)child.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
