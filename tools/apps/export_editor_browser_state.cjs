// Read only an isolated copy of the dedicated application profile; all network
// requests are fulfilled locally. Never open the user's unrelated browser data.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {chromium}=require('playwright');
(async()=>{
 const state=path.resolve(process.argv[2]||'');
 if(!process.argv[2]||!state.includes(path.join('work','migration')))throw new Error('An explicit staged migration editor directory is required.');
 const server=JSON.parse(fs.readFileSync(path.join(state,'server.json'),'utf8'));
 const origin=new URL(server.url);if(origin.hostname!=='127.0.0.1'||origin.protocol!=='http:')throw new Error('The recorded editor origin is not local.');
 const scratch=path.join(path.dirname(path.dirname(state)),'browser-profile-export');
 if(fs.existsSync(scratch))throw new Error('Browser export scratch already exists; keep the prior evidence and use a new staging generation.');
 fs.cpSync(path.join(state,'window-profile'),scratch,{recursive:true});
 const browser=await chromium.launchPersistentContext(scratch,{channel:'msedge',headless:true,serviceWorkers:'block',args:['--disable-background-networking','--no-first-run']});
 let values;
 try{await browser.route('**/*',route=>route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Isolated editor state export</title>'}));const page=await browser.newPage();await page.goto(origin.origin);values=await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(key=>key.startsWith('french-notes.')).map(key=>[key,localStorage.getItem(key)])));}
 finally{await browser.close();}
 const output=path.join(state,'browser-state.json');const data={schema:1,source_folder:server.folder,values};fs.writeFileSync(output,JSON.stringify(data,null,2));
 const reportPath=path.resolve(state,'../../../staging-report.json');const report=JSON.parse(fs.readFileSync(reportPath,'utf8'));
 report.files.push({owner:'editor',source:'dedicated browser profile at recorded source origin',target:'state/french/editor/browser-state.json',sha256:crypto.createHash('sha256').update(fs.readFileSync(output)).digest('hex'),bytes:fs.statSync(output).size,transform:'Only French Notes application localStorage exported from a copied profile with all network blocked.'});
 report.browser_state_export={keys:Object.keys(values).length,source_origin:origin.origin,source_folder:server.folder,network:'Blocked; local route only',original_profile_changed:false};fs.writeFileSync(reportPath,JSON.stringify(report,null,2));
 console.log(JSON.stringify({application_keys:Object.keys(values).length,source_origin:origin.origin,original_profile_changed:false,staged_file:output}));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
