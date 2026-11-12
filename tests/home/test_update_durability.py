"""Crash/replay the real owning Home poll loop against isolated transport."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from home_bot.storage import Store

SCRIPT=r'''
import json,os,sys
from pathlib import Path
from home_bot.storage import Store
from home_bot.bot import Bot
folder=Path(sys.argv[1]);stage=sys.argv[2]
update=json.loads((folder/'update.json').read_text())
class Transport:
 def __init__(self):self.polls=0;self.bot=None
 def call(self,method,payload=None,**kwargs):
  if method=='answerCallbackQuery' and stage=='before_commit':os._exit(23)
  if method=='getUpdates':
   self.polls+=1
   if self.polls==1:return [update]
   if stage=='after_commit':os._exit(23)
   (folder/'ack.json').write_text(json.dumps(payload))
   if stage=='after_ack':os._exit(23)
   self.bot.running=False;return []
  return {'message_id':77}
 def send(self,chat,text,**kwargs):return self.call('sendMessage',{'chat_id':chat,'text':text,**kwargs})
store=Store(folder);api=Transport();bot=Bot(store,api,'fixture',folder);api.bot=bot
bot.maintenance=lambda:None
bot._run();store.close()
'''

class UpdateDurabilityTests(unittest.TestCase):
    def exercise(self,stage):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);store=Store(folder)
            store.add_member(101,'Owner','owner');store.set('chat_id',-100111)
            task=store.add_task('shopping','Fixture milk',101)
            store.set('list_id',77);store.set('panel_state_list_id',{'callbacks':[f'task:complete:{task}:1']})
            store.set('screen','list');store.set('view',{'category':'shopping','mode':'all','page':0})
            store.close()
            update={'update_id':999,'callback_query':{'id':'fixture-999','from':{'id':101},'message':{'message_id':77,'chat':{'id':-100111,'type':'supergroup'}},'data':f'task:complete:{task}:1'}}
            (folder/'update.json').write_text(json.dumps(update))
            result=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),stage],capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,23,result.stderr)
            store=Store(folder)
            self.assertEqual(bool(store.task(task)['done']),stage!='before_commit')
            self.assertEqual(store.get('last_update_id',-1),-1 if stage=='before_commit' else 999)
            store.close()
            replay=subprocess.run([sys.executable,'-c',SCRIPT,str(folder),'replay'],capture_output=True,text=True,timeout=12)
            self.assertEqual(replay.returncode,0,replay.stderr)
            store=Store(folder)
            self.assertTrue(store.task(task)['done']);self.assertEqual(store.task(task)['revision'],2)
            self.assertEqual(store.get('last_update_id'),999);store.close()
            self.assertEqual(json.loads((folder/'ack.json').read_text())['offset'],1000)
    def test_crash_before_commit_replays_without_lost_mutation(self):self.exercise('before_commit')
    def test_crash_after_commit_before_ack_does_not_mutate_twice(self):self.exercise('after_commit')
    def test_crash_after_ack_does_not_mutate_twice(self):self.exercise('after_ack')

if __name__=='__main__':unittest.main()
