"""Real PTB Update/Service crash boundaries with an isolated strict transport."""
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.refresh import LibraryManager

SCRIPT=r'''
import asyncio,json,os,sys
from pathlib import Path
from types import SimpleNamespace
from telegram import Update
from telegram.ext import Application
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.refresh import LibraryManager
from french_notes.telegram_bot.service import Service
from french_notes.telegram_bot.offline import FakeRequest,FAKE_TOKEN
folder=Path(sys.argv[1]);stage=sys.argv[2]
async def run():
 state=State(folder/'state',folder/'notes');manager=LibraryManager(folder/'notes',state)
 request=FakeRequest(state);request.messages[1,101]=json.loads((folder/'message.json').read_text())
 request.next_id=101
 async def before(name,params):
  if stage=='before_commit' and name in ('editmessagetext','sendmessage'):os._exit(23)
  if stage=='before_remote_send' and name=='sendmessage':os._exit(23)
 request.before_request=before
 original=request.do_request
 async def remote(*args,**kwargs):
  response=await original(*args,**kwargs)
  url=args[0] if args else kwargs['url']
  if url.lower().endswith('/sendmessage'):
   path=folder/'remote-send-count.json';count=json.loads(path.read_text()) if path.exists() else 0
   path.write_text(json.dumps(count+1))
   if stage=='after_remote_send':os._exit(23)
  return response
 request.do_request=remote
 app=Application.builder().token(FAKE_TOKEN).request(request).get_updates_request(request).build()
 await app.initialize();service=Service(state,manager,automatic_audio=False,preload=False)
 update=Update.de_json(json.loads((folder/'update.json').read_text()),app.bot)
 await service.handle(update,SimpleNamespace(bot=app.bot))
 if stage=='after_commit':os._exit(23)
 offset=int(state.meta('polling_offset'))
 await app.bot.get_updates(offset=offset,timeout=0)
 (folder/'ack.json').write_text(json.dumps({'offset':offset}))
 if stage=='after_ack':os._exit(23)
 await service.close();await app.shutdown();state.close()
asyncio.run(run())
'''

class UpdateDurabilityTests(unittest.TestCase):
    def exercise(self,stage):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);notes=folder/'notes';notes.mkdir();(notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
            state=State(folder/'state',notes);state.redeem(state.invitation('owner'),1,'Owner')
            manager=LibraryManager(notes,state);manager.publish(manager.candidate())
            state.save_session(1,anchor=101,generation=1)
            handle=state.buttons(1,1,1,manager.current.version,[{'kind':'invite'}])[0]
            baseline=state.db.execute('SELECT COUNT(*) FROM invitations').fetchone()[0];state.close()
            message={'message_id':101,'date':1,'chat':{'id':1,'type':'private'},'from':{'id':123456,'is_bot':True,'first_name':'Fixture'},'text':'Access'}
            update={'update_id':999,'callback_query':{'id':'fixture999','from':{'id':1,'is_bot':False,'first_name':'Owner'},'chat_instance':'fixture','data':handle,'message':message}}
            (folder/'message.json').write_text(json.dumps(message));(folder/'update.json').write_text(json.dumps(update))
            child=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),stage],capture_output=True,text=True,timeout=15)
            self.assertEqual(child.returncode,23,child.stderr)
            state=State(folder/'state',notes)
            self.assertEqual(state.db.execute('SELECT COUNT(*) FROM invitations').fetchone()[0],baseline+(stage!='before_commit'))
            self.assertEqual(state.meta('polling_offset'),None if stage=='before_commit' else '1000');state.close()
            replay=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),'replay'],capture_output=True,text=True,timeout=15)
            self.assertEqual(replay.returncode,0,replay.stderr)
            state=State(folder/'state',notes)
            self.assertEqual(state.db.execute('SELECT COUNT(*) FROM invitations').fetchone()[0],baseline+1)
            self.assertEqual(state.meta('polling_offset'),'1000');state.close()
            self.assertEqual(json.loads((folder/'ack.json').read_text())['offset'],1000)
    def test_crash_before_commit_rolls_back_receipt_and_invitation(self):self.exercise('before_commit')
    def test_crash_after_commit_before_ack_does_not_create_second_invitation(self):self.exercise('after_commit')
    def test_crash_after_ack_does_not_create_second_invitation(self):self.exercise('after_ack')

    def uncertain_send(self,stage,expected_sends):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);notes=folder/'notes';notes.mkdir()
            (notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
            state=State(folder/'state',notes);state.redeem(state.invitation('owner'),1,'Owner')
            manager=LibraryManager(notes,state);manager.publish(manager.candidate());state.save_session(1,anchor=101,generation=1);state.close()
            message={'message_id':101,'date':1,'chat':{'id':1,'type':'private'},'from':{'id':123456,'is_bot':True,'first_name':'Fixture'},'text':'Previous menu'}
            update={'update_id':999,'message':{'message_id':10999,'date':1,'chat':{'id':1,'type':'private'},'from':{'id':1,'is_bot':False,'first_name':'Owner'},'text':'/start'}}
            (folder/'message.json').write_text(json.dumps(message));(folder/'update.json').write_text(json.dumps(update))
            child=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),stage],capture_output=True,text=True,timeout=15)
            self.assertEqual(child.returncode,23,child.stderr)
            intention=folder/'state/delivery-intentions.json'
            self.assertEqual(json.loads(intention.read_text()),{'999:1:1':999})
            for _ in range(2):
                replay=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),'replay'],capture_output=True,text=True,timeout=15)
                self.assertEqual(replay.returncode,0,replay.stderr)
                count=folder/'remote-send-count.json'
                self.assertEqual(json.loads(count.read_text()) if count.exists() else 0,expected_sends)
            state=State(folder/'state',notes);self.assertEqual(state.meta('polling_offset'),'1000');self.assertEqual(state.session(1)['anchor'],101);state.close()
            self.assertFalse(intention.exists())
            # A new explicit menu request is a new operation and can recover UI.
            update['update_id']=1000;update['message']['message_id']=11000;update['message']['text']='/menu'
            (folder/'update.json').write_text(json.dumps(update))
            explicit=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),'replay'],capture_output=True,text=True,timeout=15)
            self.assertEqual(explicit.returncode,0,explicit.stderr)
            self.assertEqual(json.loads((folder/'remote-send-count.json').read_text()),expected_sends+1)
            state=State(folder/'state',notes);self.assertEqual(state.meta('polling_offset'),'1001');self.assertEqual(state.session(1)['anchor'],102);state.close()

    def test_crash_after_remote_send_does_not_blindly_send_again(self):self.uncertain_send('after_remote_send',1)
    def test_crash_with_pre_send_intention_requires_explicit_menu_for_uncertain_delivery(self):self.uncertain_send('before_remote_send',0)

    def test_invalid_delivery_markers_are_preserved_and_block_state_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);notes=folder/'notes';notes.mkdir()
            state=State(folder/'state',notes);state.close();marker=folder/'state/delivery-intentions.json';marker.write_text('[]')
            with self.assertRaisesRegex(ValueError,'delivery recovery markers'):State(folder/'state',notes)
            self.assertEqual(marker.read_text(),'[]')

if __name__=='__main__':unittest.main()
