"""Actual PTB handlers + strict simulated Bot API. No network or real token."""
from __future__ import annotations

import asyncio
from collections import defaultdict,deque
import copy
import json
import os
from pathlib import Path
import time

from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest

from . import BOT_USERNAME
from .refresh import LibraryManager
from .service import Service,attach
from .state import State

FAKE_TOKEN='123456:offline_fixture_012345678901234567890'


class FakeRequest(BaseRequest):
    def __init__(self,state=None):
        super().__init__();self.state=state;self.calls=[];self.messages={};self.next_id=100
        self.failures=defaultdict(deque);self.updates=deque();self.before_request=None
        self.webhook='';self.username=BOT_USERNAME

    @property
    def read_timeout(self):return 1
    async def initialize(self):pass
    async def shutdown(self):pass

    async def do_request(self,url,method,request_data=None,**kwargs):
        name=url.rsplit('/',1)[-1].lower()
        params=copy.deepcopy(request_data.parameters if request_data else {})
        uploads=request_data.multipart_data if request_data else {}
        # Direct InputFile uploads live only in multipart_data, not parameters.
        # Model the transmitted field so tests exercise upload and file-id reuse.
        if name=='sendaudio' and 'audio' in uploads and 'audio' not in params:
            params['audio']='attach://audio'
        if self.before_request:await self.before_request(name,params)
        self.calls.append({'method':name,'params':params})
        if self.failures[name]:
            failure=self.failures[name].popleft()
            return failure.get('error_code',400),json.dumps(dict(ok=False,**failure)).encode()
        result=True
        if name=='getme':
            result={'id':123456,'is_bot':True,'first_name':'French_Learning','username':self.username}
        elif name=='getwebhookinfo':result={'url':self.webhook,'pending_update_count':0,'has_custom_certificate':False}
        elif name=='getupdates':
            result=list(self.updates);self.updates.clear()
            if not result:await asyncio.sleep(.01)
        elif name in ('sendmessage','editmessagetext','sendaudio','editmessagemedia'):
            chat=int(params['chat_id']);text=params.get('text','')
            public=text.startswith(('Invitation required.','Owner setup is not finished yet.','Closed.'))
            if self.state and not public and not self.state.allowed(chat):
                raise AssertionError('Protected request attempted after revocation or without access.')
            if name.startswith('edit'):
                key=(chat,int(params['message_id']))
                if key not in self.messages:
                    return 400,json.dumps({'ok':False,'error_code':400,'description':'Bad Request: message to edit not found'}).encode()
                result=self.messages[key]
            else:
                self.next_id+=1;key=(chat,self.next_id)
                result={'message_id':self.next_id,'date':int(time.time()),'chat':{'id':chat,'type':'private'},
                        'from':{'id':123456,'is_bot':True,'first_name':'French_Learning'}}
            if text:result['text']=text
            if 'reply_markup' in params:result['reply_markup']=params['reply_markup']
            if name in ('sendaudio','editmessagemedia'):
                requested=params.get('media',{}).get('media','') if name=='editmessagemedia' else params.get('audio','')
                if not requested or (requested.startswith('attach://') and requested[9:] not in uploads):
                    return 400,json.dumps({'ok':False,'error_code':400,'description':'Bad Request: missing audio upload'}).encode()
                file_id=requested if isinstance(requested,str) and not requested.startswith('attach://') else 'fixture_audio_'+str(len(self.calls))
                result['audio']={'file_id':file_id,'file_unique_id':'fixture_unique',
                                 'duration':1,'mime_type':'audio/mpeg','file_size':500}
                result['caption']=params.get('media',{}).get('caption','') if name=='editmessagemedia' else params.get('caption','')
            self.messages[key]=copy.deepcopy(result)
        elif name=='deletemessage':
            key=(int(params['chat_id']),int(params['message_id']))
            if key[1]<10000 and key not in self.messages:
                return 400,json.dumps({'ok':False,'error_code':400,'description':'Bad Request: message to delete not found'}).encode()
            self.messages.pop(key,None)
        return 200,json.dumps({'ok':True,'result':result},ensure_ascii=False).encode('utf-8')


class Harness:
    def __init__(self,source,state_dir,speech=None,automatic_audio=False):
        self.state=State(state_dir,source)
        self.manager=LibraryManager(source,self.state)
        if not self.manager.current:self.manager.publish(self.manager.candidate())
        if not self.state.has_owner():self.state.redeem(self.state.invitation('owner'),1,'Owner')
        self.request=FakeRequest(self.state)
        self.app=Application.builder().token(FAKE_TOKEN).request(self.request).get_updates_request(self.request).concurrent_updates(32).build()
        self.service=Service(self.state,self.manager,speech,automatic_audio=automatic_audio,preload=automatic_audio)
        attach(self.app,self.service)
        self.counter=self.state.db.execute('SELECT COALESCE(MAX(id),0) FROM updates').fetchone()[0]
        self.actions=0

    async def start(self):await self.app.initialize();return self
    async def close(self):
        await self.service.close()
        if self.app.updater.running:await self.app.updater.stop()
        if self.app.running:await self.app.stop()
        await self.app.shutdown();self.state.close()

    def reader(self,user):
        if not self.state.allowed(user):self.state.redeem(self.state.invitation(),user,'Reader '+str(user))

    def message_update(self,text,user=1,chat_type='private'):
        self.counter+=1
        return {'update_id':self.counter,'message':{'message_id':10000+self.counter,'date':int(time.time()),
                'chat':{'id':user,'type':chat_type},'from':{'id':user,'is_bot':False,'first_name':'Reader '+str(user)},'text':text}}

    async def message(self,text,user=1,chat_type='private'):
        self.actions+=1
        await self.app.process_update(Update.de_json(self.message_update(text,user,chat_type),self.app.bot))

    def current_message(self,user=1,field=None):
        session=self.state.session(user)
        message_id=session[field] if field else session['continuation'] or session['anchor']
        return self.request.messages.get((user,message_id))

    def buttons(self,user=1):
        message=self.current_message(user)
        return [b for row in (message or {}).get('reply_markup',{}).get('inline_keyboard',[]) for b in row]

    async def callback(self,handle,user=1,message=None):
        self.counter+=1;self.actions+=1
        original=copy.deepcopy(message or self.current_message(user))
        data={'update_id':self.counter,'callback_query':{'id':str(self.counter),'from':{'id':user,'is_bot':False,'first_name':'Reader '+str(user)},
              'chat_instance':'offline','data':handle,'message':original}}
        await self.app.process_update(Update.de_json(data,self.app.bot))

    async def click(self,label,user=1):
        button=next((b for b in self.buttons(user) if b['text']==label),None)
        if not button:raise AssertionError('Button absent: '+label)
        await self.callback(button['callback_data'],user)


async def smoke(source,state_dir,hold=False):
    harness=await Harness(source,state_dir).start()
    try:
        await harness.message('/start')
        await harness.click('Browse material')
        await harness.click(harness.buttons()[0]['text'])
        if any(b['text']=='All entries' for b in harness.buttons()):await harness.click('All entries')
        entries=[b for b in harness.buttons() if b['text'] not in ('Menu','Search','Back','Previous page','Next page')]
        if entries:await harness.click(entries[0]['text'])
        await harness.message('English')
        await harness.message('/settings')
        await harness.click(next(b['text'] for b in harness.buttons() if b['text'].startswith('Henri')))
        await harness.message('/menu')
        await harness.click('Access')
        await harness.click('Create invitation')
        report={'passed':True,'actions':harness.actions,'api_calls':len(harness.request.calls),
                'transport':'fake Telegram via actual python-telegram-bot handlers','real_telegram':False}
        (Path(state_dir)/'offline-smoke.json').write_text(json.dumps(report,indent=2),'utf-8')
        print(json.dumps(report))
        if hold:
            await harness.app.start()
            await harness.app.updater.start_polling(timeout=1,poll_interval=.05,bootstrap_retries=0)
            harness.service.watcher=asyncio.create_task(harness.service.watch())
            (Path(state_dir)/'offline-ready.json').write_text(json.dumps({'pid':os.getpid(),'nonce':os.environ.get('FRENCH_BOT_HARNESS_NONCE','')}),'utf-8')
            print('OFFLINE POLLING READY. Ctrl+C tests graceful shutdown.',flush=True)
            await asyncio.Event().wait()
    finally:
        await harness.close()
        (Path(state_dir)/'offline-shutdown.json').write_text(json.dumps({'closed':True,'polling_stopped':not harness.app.updater.running,'application_stopped':not harness.app.running}),'utf-8')
