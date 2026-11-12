// Asset generation is an explicit tool, separate from regression discovery.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../../..');
(async()=>{
 const output=process.env.BOT_CENTER_ASSET_OUTPUT||path.join(root,'work/assets');fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:256,height:256},deviceScaleFactor:1});
  const svg=fs.readFileSync(path.join(root,'.venvs/french-editor/Lib/site-packages/french_notes/ui/icon.svg'),'utf8');
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{width:256px;height:256px}</style>${svg}`);
  const png=await page.screenshot({omitBackground:true});
  const header=Buffer.alloc(22);header.writeUInt16LE(1,2);header.writeUInt16LE(1,4);header.writeUInt16LE(1,10);header.writeUInt16LE(32,12);header.writeUInt32LE(png.length,14);header.writeUInt32LE(22,18);
  fs.writeFileSync(path.join(output,'French Notes.ico'),Buffer.concat([header,png]));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
