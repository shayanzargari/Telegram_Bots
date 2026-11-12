// Decode and play an actual prepared MP3 locally. This is not phone verification.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
  const work=process.argv[2]||process.env.BOT_CENTER_TEST_OUTPUT;
  if(!work)throw new Error('Provide an explicit isolated audio audit work directory.');
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage();
    await page.setContent('<button id="play">Play local recording</button>');
    const bytes=fs.readFileSync(path.join(work,'prepared-sample.mp3')).toString('base64');
    await page.evaluate(data=>{
      window.sample=new Audio('data:audio/mpeg;base64,'+data);
      window.sample.volume=0;
      document.getElementById('play').onclick=()=>window.sample.play();
    },bytes);
    await page.click('#play');
    await page.waitForFunction(()=>window.sample.currentTime>.15,{timeout:10000});
    const report=await page.evaluate(()=>({decoded:true,playhead:window.sample.currentTime,duration:window.sample.duration,
      readyState:window.sample.readyState,error:window.sample.error?.code||null,mutedLocalCheck:true,phoneVerified:false}));
    fs.writeFileSync(path.join(work,'local-playback.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{await browser.close();}
})();
