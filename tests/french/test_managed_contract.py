"""Exercise production PTB lifecycle, effective timeouts and terminal outcomes."""
import asyncio,hashlib,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from french_notes.managed import run_async
from french_notes.telegram_bot.offline import FakeRequest,FAKE_TOKEN
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.refresh import LibraryManager

class Client:
    def __init__(self):self.stop=threading.Event();self.bootstrap={'token':FAKE_TOKEN};self.events=[];self.polls=0
    def emit(self,kind,**fields):self.events.append((kind,fields))
    def ready(self):self.emit('ready')
    def progress(self,poll_ok=False,**fields):
        self.polls+=bool(poll_ok);self.emit('health',**fields)

class ManagedContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        notes=self.root/'notes';notes.mkdir();(notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
        state=State(self.root/'reader',notes);state.bind_bot(123456);state.redeem(state.invitation('owner'),1,'Owner');manager=LibraryManager(notes,state);manager.publish(manager.candidate());state.close()
        self.manifest={'id':'french','telegram_id':123456,'data_dir':str(self.root/'reader'),'notes_dir':str(notes),'editor_state_dir':str(self.root/'editor'),'history_path':str(self.root/'editor/history.json')}
        self.client=Client();self.request=FakeRequest();self.timeouts=[]
        original=self.request.do_request
        async def record(*args,**kwargs):
            url=args[0] if args else kwargs['url']
            if url.lower().endswith('/getupdates'):self.timeouts.append(kwargs.copy())
            return await original(*args,**kwargs)
        self.request.do_request=record
    async def run_fixture(self):
        class Speech:
            def close(self):pass
        with patch('telegram.request.HTTPXRequest',side_effect=lambda **kwargs:self.request),patch('french_notes.speech.FrenchSpeech',return_value=Speech()):
            return await asyncio.wait_for(run_async(self.client,self.manifest),8)
    async def test_initial_network_outage_reconnects_in_same_worker_and_poll_timeout_is_effective(self):
        self.request.failures['getme'].append({'error_code':503,'description':'Temporary network outage'})
        async def stop_after_poll(name,params):
            if name=='getupdates':
                self.assertEqual(params['timeout'],10);self.client.stop.set()
        self.request.before_request=stop_after_poll
        self.assertEqual(await self.run_fixture(),0)
        self.assertGreaterEqual(sum(c['method']=='getme' for c in self.request.calls),2)
        self.assertEqual(self.client.polls,1)
        self.assertFalse(any(kind=='failure' for kind,_ in self.client.events))
        self.assertTrue(any(fields.get('connection')=='Disconnected' for kind,fields in self.client.events))
        self.assertEqual(self.timeouts[0]['read_timeout'],25)
        self.assertEqual(self.timeouts[0]['connect_timeout'],8)
    async def test_wrong_numeric_identity_is_terminal_before_writable_state_or_polling(self):
        path=self.root/'reader/reader.sqlite3';before=hashlib.sha256(path.read_bytes()).hexdigest();self.manifest['telegram_id']=987654
        self.assertEqual(await self.run_fixture(),2)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        self.assertTrue(any(kind=='failure' and fields['category']=='identity' for kind,fields in self.client.events))
        self.assertFalse(any(c['method']=='getupdates' for c in self.request.calls))
    async def test_unexpected_webhook_remains_actionable_without_removal_or_poll(self):
        self.request.webhook='https://fixture.invalid/existing-webhook'
        self.assertEqual(await self.run_fixture(),2)
        self.assertTrue(any(kind=='failure' and fields['category']=='configuration' for kind,fields in self.client.events))
        self.assertFalse(any(c['method'] in {'deletewebhook','getupdates'} for c in self.request.calls))

if __name__=='__main__':unittest.main()
