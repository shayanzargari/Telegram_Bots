"""Actual bundled dashboard in isolated Edge; no production data or tokens."""
from __future__ import annotations
import json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from playwright.sync_api import sync_playwright

WORKSPACE=Path(__file__).resolve().parents[2]
COREPY=WORKSPACE/'.venvs/core/Scripts/python.exe'

class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='center-ui-');cls.root=Path(cls.temp.name)
        (cls.root/'config').mkdir();(cls.root/'data/fixture').mkdir(parents=True)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
        cls.origin='http://127.0.0.1:'+str(cls.port)
        (cls.root/'config/installation.json').write_text('{"schema":1,"generation":"isolated-ui"}')
        (cls.root/'config/settings.json').write_text(json.dumps({'port':cls.port}))
        manifest={'schema_version':1,'contract_version':1,'id':'fixture','display_name':'Fixture bot','python':str(COREPY),'module':'fixture.managed','cwd':'data/fixture','data_dir':'data/fixture','log_dir':'logs/fixture','credential_ref':'fixture','telegram_id':None,'capabilities':['polling'],'required_state':[],'jobs':[]}
        (cls.root/'config/registry.json').write_text(json.dumps({'schema':1,'bots':[manifest]}))
        seed='from pathlib import Path;from bot_center.commands import CommandService;s=CommandService(Path(__import__("sys").argv[1]));s.enroll({"id":"fixture"});s.close()'
        subprocess.run([str(COREPY),'-c',seed,str(cls.root)],check=True,capture_output=True)
        cls.log=(cls.root/'core-output.log').open('w',encoding='utf-8')
        cls.process=subprocess.Popen([str(COREPY),'-m','bot_center','--root',str(cls.root),'serve'],cwd=cls.root,stdin=subprocess.DEVNULL,stdout=cls.log,stderr=cls.log,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                cls.ticket();break
            except (subprocess.CalledProcessError,ValueError):time.sleep(.2)
        else:raise RuntimeError('Isolated UI core did not reach authenticated startup.')
        cls.playwright=sync_playwright().start();cls.browser=cls.playwright.chromium.launch(channel='msedge',headless=True)
    @classmethod
    def rpc(cls,body):
        script='import json,sys;from bot_center_runtime.ipc import request;print(json.dumps(request(sys.argv[1],json.loads(sys.argv[2]))["body"]))'
        result=subprocess.check_output([str(COREPY),'-c',script,str(cls.root),json.dumps(body)],cwd=cls.root,text=True,stderr=subprocess.DEVNULL,timeout=10)
        return json.loads(result)
    @classmethod
    def ticket(cls):
        result=cls.rpc({'action':'dashboard'})
        if not result.get('ticket'):raise ValueError('Core not ready')
        return result['ticket']
    @classmethod
    def tearDownClass(cls):
        try:cls.rpc({'action':'quit','target':'*'})
        except Exception:pass
        try:cls.process.wait(timeout=15)
        except subprocess.TimeoutExpired:cls.process.terminate();cls.process.wait(timeout=10)
        if hasattr(cls,'browser'):cls.browser.close();cls.playwright.stop()
        cls.log.close();cls.temp.cleanup()
    def page(self,width=1366):
        context=self.browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
        page=context.new_page();page.goto(self.origin+'/#ticket='+self.ticket());page.get_by_text('Core ready',exact=True).wait_for();self.addCleanup(context.close);return page
    def test_authenticated_local_ui_lifecycle_and_browser_close(self):
        page=self.page();self.assertEqual('',page.evaluate('location.hash'))
        self.assertEqual(1,page.locator('.bot-card').count())
        page.get_by_label('Enabled: Fixture bot').check();page.get_by_label('Enabled: Fixture bot').wait_for()
        page.wait_for_function("document.querySelector('.toggle-label input').checked")
        self.assertEqual('stopped',self.rpc({'action':'status'})['bots'][0]['desired'])
        page.locator('.bot-card').get_by_role('button',name='Start',exact=True).click()
        page.get_by_text('Needs attention',exact=True).wait_for(timeout=10000)
        page.locator('.bot-card').get_by_role('button',name='Stop',exact=True).click()
        page.wait_for_timeout(500)
        page.close();self.assertIsNone(self.process.poll())
        reopened=self.page();self.assertEqual('stopped',self.rpc({'action':'status'})['bots'][0]['desired'])
    def test_mobile_layout_keyboard_labels_and_bundled_resources(self):
        page=self.page(width=375)
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        page.keyboard.press('Tab');self.assertNotEqual('none',page.evaluate('getComputedStyle(document.activeElement).outlineStyle'))
        page.get_by_role('button',name='+ Add bot',exact=True).click();self.assertTrue(page.get_by_role('dialog').is_visible())
        page.get_by_label('Bot ID',exact=True).fill('invalid-id');page.get_by_label('Display name',exact=True).fill('Example')
        page.get_by_role('button',name='Cancel',exact=True).click()
        self.assertEqual(0,page.locator('script[src^="http"]').count())
        self.assertEqual(0,page.locator('link[href^="http"]').count())
        output=Path(os.environ.get('BOT_CENTER_TEST_OUTPUT',WORKSPACE/'work/tests/manual-dashboard'));output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/'dashboard-mobile.png'),full_page=True)
    def test_schedule_creation_stays_disabled_and_visible(self):
        page=self.page();page.get_by_role('button',name='Add schedule',exact=True).click()
        future=(datetime.now(timezone.utc)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        page.get_by_label('UTC date and time',exact=True).fill(future)
        page.get_by_role('button',name='Create disabled schedule',exact=True).click()
        page.get_by_text('Schedule created disabled. Enable it when ready.',exact=True).wait_for()
        self.assertGreaterEqual(page.locator('#schedule-list .list-row').count(),1)
        self.assertTrue(page.locator('#schedule-list').get_by_role('button',name='Enable',exact=True).first.is_visible())

if __name__=='__main__':unittest.main()
