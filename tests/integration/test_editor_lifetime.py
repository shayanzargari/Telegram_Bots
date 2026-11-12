"""Actual independent editor, native Job-owned reader and browser relocation."""
from __future__ import annotations
import ctypes,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path
from ctypes import wintypes
from contextlib import closing
from bot_center_runtime.ipc import process_creation,IPCError

WORKSPACE=Path(__file__).resolve().parents[2]
EDITOR=WORKSPACE/'.venvs/french-editor/Scripts/python.exe'
READER=WORKSPACE/'.venvs/french-reader/Scripts/python.exe'

SEED=r'''
from pathlib import Path
import json,sys,uuid
from french_notes.editor_store import EditorStore
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.refresh import LibraryManager
root=Path(sys.argv[1]);notes=root/'data/french/notes';notes.mkdir(parents=True)
(notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
store=EditorStore(notes,root/'data/french/editor');doc=store.read_document('Practice.txt')
store.save_document('Practice.txt','Salut    |    سلام    |    Hello\n',doc['revision'],doc['section_revision'],str(uuid.uuid4()))
state=State(root/'data/french/reader',notes);state.redeem(state.invitation('owner'),1,'Fixture owner')
state.setting(1,position={'document':'Practice.txt','record':'preserved-position'})
manager=LibraryManager(notes,state);manager.publish(manager.candidate());state.close()
'''

HARNESS=r'''
import asyncio,json,os,sys,time,uuid
from pathlib import Path
from bot_center.api import Core
from bot_center.supervisor import Run
from bot_center_runtime.paths import Installation
from bot_center_runtime.process import ManagedProcess
from bot_center_runtime.locks import ResourceLock
root=Path(sys.argv[1]);core=None
worker="from bot_center_runtime.protocol import WorkerClient\nc=WorkerClient.from_environment();c.ready()\nwhile not c.stop.wait(.05):c.progress(poll_ok=True)\nc.close()"
def editor():return json.loads((Path(Installation(root).bot('french')['editor_state_dir'])/'server.json').read_text())
async def fresh():
 global core
 if core:await core.close()
 core=Core(root);core.loop=asyncio.get_running_loop()
 for manifest in Installation(root).manifests():core.commands.enroll(manifest,enabled=True)
 await core.open_editor(no_browser=True)
async def start_reader():
 op=core.commands.submit('french','start');manifest=Installation(root).bot('french');runid=uuid.uuid4().hex
 lock=ResourceLock('fixture-reader:'+str(root));lock.acquire()
 process=ManagedProcess([manifest['python'],'-c',worker],{'contract_version':1,'root':str(root),'bot_id':'french','run_id':runid,'token':'','manifest':manifest},cwd=root,environment={'BOT_CENTER_ROOT':str(root)})
 core.supervisor.runs['french']=Run('french',runid,core.commands.bot('french')['revision'],process,lock)
 core.commands.observed('french','Running',run_id=runid)
 return process.pid
async def drain():
 deadline=time.monotonic()+10
 while core.supervisor.runs and time.monotonic()<deadline:
  for key,run in list(core.supervisor.runs.items()):
   if await core.supervisor._observe(run):core.supervisor.runs.pop(key,None)
  await asyncio.sleep(.05)
 if core.supervisor.runs:raise RuntimeError('Fixture reader did not stop')
 # This harness drives native observation without the ordinary tick loop (so
 # it can never reach real identity/network startup). Mirror only tick's final
 # stopped-completion condition after every owned resource has actually drained.
 state=core.commands.bot('french')
 if state['desired']=='stopped' and not core.supervisor.jobs:
  core.commands.mark_satisfied('french',state['revision'],'Stopped')
async def run():
 global core
 await fresh();reader=await start_reader();(root/'harness-ready.json').write_text(json.dumps({'core_pid':os.getpid(),'reader_pid':reader,'editor':editor()}))
 previous=None
 while True:
  await asyncio.sleep(.05)
  try:request=json.loads((root/'harness-command.json').read_text())
  except (OSError,ValueError):continue
  if request['id']==previous:continue
  previous=request['id'];action=request['action'];result={}
  try:
   if action=='stop':
    core.commands.submit('french','stop')
    for owned in core.supervisor.runs.values():core.supervisor.begin_stop(owned)
    await drain();result={'reader_stopped':True}
   elif action=='restart':
    for owned in core.supervisor.runs.values():core.supervisor.begin_stop(owned)
    await drain();result={'reader_pid':await start_reader()}
   elif action=='open':await core.open_editor(no_browser=True)
   elif action=='backup':
    operation=core.commands.submit('*','backup');await core.backup(operation)
    result={'backup_status':core.commands.operation(operation['id'])['status']}
   elif action=='quit':await core.close();core=None;result={'core_closed':True}
   elif action=='reopen':await fresh();result={'reader_pid':await start_reader()}
   elif action=='failure':
    async def fail():raise RuntimeError('Isolated essential supervisor failure')
    task=asyncio.create_task(fail());await asyncio.sleep(0);core.task_done('supervisor',task);await drain()
    result={'readiness':core.readiness(),'reader_stopped':not core.supervisor.runs}
   result['editor']=editor();(root/'harness-result.json').write_text(json.dumps({'id':previous,**result}))
  except Exception as error:(root/'harness-result.json').write_text(json.dumps({'id':previous,'error':type(error).__name__+': '+str(error)}))
asyncio.run(run())
'''

BROWSER=r'''
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[1]);
(async()=>{
 const root=process.argv[2],mode=process.argv[3];const manifest=JSON.parse(fs.readFileSync(path.join(root,'config/registry.json'),'utf8')).bots[0];
 const state=path.join(root,manifest.editor_state_dir),server=JSON.parse(fs.readFileSync(path.join(state,'server.json'),'utf8'));
 const context=await chromium.launchPersistentContext(path.join(state,'window-profile'),{channel:'msedge',headless:true,serviceWorkers:'block'});
 try{
  await context.route('**/api/speech',route=>route.fulfill({status:200,contentType:'audio/mpeg',body:fs.readFileSync(process.argv[4])}));
  const page=await context.newPage();await page.goto(server.url);await page.waitForFunction(()=>!document.querySelector('#editor').disabled);
  if(mode==='seed'){
   await page.getByRole('button',{name:'Text',exact:true}).click();await page.locator('#editor').fill('Brouillon préservé    |    پیش نویس    |    Preserved draft\n');
   await page.locator('#speech-voice').selectOption('henri');await page.locator('#speech-speed').selectOption('0.8');
   await page.waitForFunction(async()=>{const x=await api('drafts');return x.drafts['Practice.txt']?.text.includes('Brouillon préservé');});
   await page.waitForTimeout(300);
  }else{
   assert.equal(await page.locator('#editor').inputValue(),'Brouillon préservé    |    پیش نویس    |    Preserved draft\n');
   assert.equal(await page.locator('#speech-voice').inputValue(),'henri');assert.equal(await page.locator('#speech-speed').inputValue(),'0.8');
   assert.equal(await page.locator('#save-status').textContent(),'Unsaved changes');
  }
  fs.writeFileSync(path.join(root,'browser-'+mode+'.json'),JSON.stringify({passed:true,origin:server.url,profile:path.join(state,'window-profile'),unsaved:await page.locator('#editor').inputValue(),voice:await page.locator('#speech-voice').inputValue(),speed:await page.locator('#speech-speed').inputValue()}));
 }finally{await context.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
'''

@unittest.skipUnless(os.name=='nt' and EDITOR.is_file() and READER.is_file(),'Final native Windows application environments required')
class EditorLifetimeTests(unittest.TestCase):
    def wait(self,predicate,timeout=35):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.1)
        self.fail('Timed out waiting for isolated editor/core fixture')
    def read(self,path):
        try:return json.loads(path.read_text('utf-8'))
        except (OSError,ValueError):return None
    def command(self,root,action):
        identity=uuid.uuid4().hex;temporary=root/'command.tmp';temporary.write_text(json.dumps({'id':identity,'action':action}))
        for attempt in range(7):
            try:os.replace(temporary,root/'harness-command.json');break
            except PermissionError:
                if attempt==6:raise
                time.sleep(.01*2**attempt)
        result=self.wait(lambda:(r if (r:=self.read(root/'harness-result.json')) and r.get('id')==identity else None))
        self.assertNotIn('error',result,result);return result
    def health(self,server):
        import urllib.request
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(server['url']+'/health',timeout=3) as response:return json.load(response)
    def terminate_editor(self,pid,root):
        # Enumerate only descendants of the editor proven by its fixture-owned
        # server record. Capture creation identities before terminating handles.
        identity_script="Get-CimInstance Win32_Process -Filter 'ProcessId = "+str(int(pid))+"' | Select-Object CommandLine | ConvertTo-Json -Compress"
        identity_output=subprocess.check_output(['powershell','-NoProfile','-Command',identity_script],text=True,creationflags=0x08000000).strip()
        if not identity_output:return
        identity=json.loads(identity_output)
        command=identity.get('CommandLine','')
        if 'french_notes.app' not in command or str(root) not in command:raise RuntimeError('Refusing to terminate an editor outside this isolated fixture.')
        script='Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'
        rows=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',script],text=True,creationflags=0x08000000))
        owned={pid};changed=True
        while changed:
            before=len(owned);owned.update(row['ProcessId'] for row in rows if row['ParentProcessId'] in owned);changed=before!=len(owned)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        for child in sorted(owned,key=lambda p:p==pid):
            handle=kernel.OpenProcess(0x0001|0x00100000,False,child)
            if handle:
                try:kernel.TerminateProcess(handle,91);kernel.WaitForSingleObject(handle,3000)
                finally:kernel.CloseHandle(handle)
    def browser(self,root,mode):
        result=subprocess.run(['node','-e',BROWSER,str(WORKSPACE/'node_modules/playwright'),str(root),mode,str(WORKSPACE/'tests/french/fixtures/denise.mp3')],capture_output=True,text=True,encoding='utf-8',timeout=45,creationflags=0x08000000)
        self.assertEqual(result.returncode,0,result.stderr);return self.read(root/('browser-'+mode+'.json'))
    def test_editor_survives_owned_lifetimes_and_relocation(self):
        output=Path(os.environ.get('BOT_CENTER_TEST_OUTPUT',WORKSPACE/'work/tests/manual/editor-lifetime'));output.mkdir(parents=True,exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='fixture-',dir=output));core_process=None;editor_pid=None;completed=False;log=None
        try:
            (root/'config').mkdir();(root/'config/installation.json').write_text('{"generation":"fixture-1"}')
            manifest={'id':'french','display_name':'Isolated French','contract_version':1,'schema_version':1,'module':'french_notes.managed','python':str(READER),'editor_python':str(EDITOR),'cwd':str(root),'data_dir':'data/french/reader','notes_dir':'data/french/notes','editor_state_dir':'data/french/editor','history_path':'data/french/editor/history.json','log_dir':'logs/french','required_state':['reader.sqlite3','identity.json'],'credential_ref':'fixture-french','telegram_id':123456,'jobs':[]}
            (root/'config/registry.json').write_text(json.dumps({'bots':[manifest]}))
            seeded=subprocess.run([str(EDITOR),'-c',SEED,str(root)],capture_output=True,text=True,encoding='utf-8',timeout=15);self.assertEqual(seeded.returncode,0,seeded.stderr)
            history=hashlib.sha256((root/'data/french/editor/history.json').read_bytes()).hexdigest();note=(root/'data/french/notes/Practice.txt').read_bytes()
            log=(root/'core-harness.log').open('wb');env=dict(os.environ,BOT_CENTER_ROOT=str(root));env.pop('PYTHONPATH',None)
            core_process=subprocess.Popen([sys.executable,'-c',HARNESS,str(root)],cwd=root,env=env,stdout=log,stderr=log,creationflags=0x08000000)
            ready=self.wait(lambda:self.read(root/'harness-ready.json'));editor_pid=ready['editor']['pid'];old_origin=ready['editor']['url'];self.browser(root,'seed')
            self.assertTrue(self.command(root,'stop')['reader_stopped']);self.assertEqual(self.health(ready['editor'])['app'],'French Notes')
            self.command(root,'restart');self.assertEqual(self.command(root,'open')['editor']['pid'],editor_pid)
            self.assertEqual(self.command(root,'backup')['backup_status'],'Blocked')
            self.command(root,'quit');self.assertEqual(self.health(ready['editor'])['app'],'French Notes')
            self.command(root,'reopen');failed=self.command(root,'failure');self.assertFalse(failed['readiness']);self.assertTrue(failed['reader_stopped']);self.assertEqual(failed['editor']['pid'],editor_pid)
            fresh=self.command(root,'reopen');reader_pid=fresh['reader_pid'];self.assertEqual(fresh['editor']['pid'],editor_pid)
            core_process.terminate();core_process.wait(timeout=10);self.assertEqual(self.health(ready['editor'])['app'],'French Notes')
            self.wait(lambda:self._gone(reader_pid),timeout=8)
            self.browser(root,'verify');self.terminate_editor(editor_pid,root);editor_pid=None
            for original,moved in [('notes','notes-relocated'),('reader','reader-relocated'),('editor','editor-relocated')]:shutil.copytree(root/'data/french'/original,root/'data/french'/moved)
            manifest.update(data_dir='data/french/reader-relocated',notes_dir='data/french/notes-relocated',editor_state_dir='data/french/editor-relocated',history_path='data/french/editor-relocated/history.json')
            (root/'config/registry.json').write_text(json.dumps({'bots':[manifest]}));(root/'harness-ready.json').unlink();(root/'harness-command.json').unlink();(root/'harness-result.json').unlink()
            port=int(old_origin.rsplit(':',1)[1]);reservation=socket.socket();reservation.bind(('127.0.0.1',port));reservation.listen(1)
            try:
                core_process=subprocess.Popen([sys.executable,'-c',HARNESS,str(root)],cwd=root,env=env,stdout=log,stderr=log,creationflags=0x08000000)
                relocated=self.wait(lambda:self.read(root/'harness-ready.json'));editor_pid=relocated['editor']['pid']
            finally:reservation.close()
            self.assertNotEqual(relocated['editor']['url'],old_origin)
            browser=self.browser(root,'relocated');self.assertTrue(browser['passed'])
            self.assertEqual((root/'data/french/notes-relocated/Practice.txt').read_bytes(),note)
            self.assertEqual(hashlib.sha256((root/'data/french/editor-relocated/history.json').read_bytes()).hexdigest(),history)
            import sqlite3
            with closing(sqlite3.connect(root/'data/french/reader-relocated/reader.sqlite3')) as db:self.assertEqual(json.loads(db.execute('SELECT position FROM users WHERE id=1').fetchone()[0]),{'document':'Practice.txt','record':'preserved-position'})
            self.command(root,'quit');core_process.terminate();core_process.wait(timeout=10)
            evidence={'passed':True,'actual_editor':True,'actual_persistent_edge_profile':True,'reader_stop_restart':True,'quit_core':True,'core_crash_job_child_gone':True,'essential_task_failure':True,'repeated_open_same_session':True,'editor_blocks_exclusive_backup':True,'origin_changed':True,'notes_profile_paths_changed':True,'draft_history_position_voice_speed_preserved':True,'real_telegram':False}
            (output/'editor-lifetime-evidence.json').write_text(json.dumps(evidence,indent=2));completed=True
        finally:
            if core_process and core_process.poll() is None:core_process.terminate();core_process.wait(timeout=10)
            if editor_pid is None:
                for saved in root.glob('data/french/editor*/server.json'):
                    metadata=self.read(saved)
                    if metadata and metadata.get('pid'):self.terminate_editor(metadata['pid'],root)
            if editor_pid:self.terminate_editor(editor_pid,root)
            if log:log.close()
            if completed and root.resolve().parent==output.resolve():shutil.rmtree(root)
    def _gone(self,pid):
        try:process_creation(pid);return False
        except (OSError,IPCError):return True

if __name__=='__main__':unittest.main()
