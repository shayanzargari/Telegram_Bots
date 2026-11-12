import asyncio
import copy
import html
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from french_notes.telegram_bot.offline import Harness


class FixtureSpeech:
    def __init__(self):self.calls=[];self.started=threading.Event();self.release=threading.Event();self.release.set()
    def synthesize(self,text,voice,speed):
        self.calls.append((text,voice,speed));self.started.set()
        self.release.wait(5)
        return (Path(__file__).parent/'fixtures/denise.mp3').read_bytes()
    def close(self):self.release.set()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.notes=self.root/'notes';self.notes.mkdir()
        (self.notes/'Practice.txt').write_text('\n'.join(f'Bonjour {i}    |    سلام {i}    |    Hello {i}' for i in range(25))+'\n','utf-8')
        self.speech=FixtureSpeech();self.h=await Harness(self.notes,self.root/'state',self.speech).start()
        self.addAsyncCleanup(self.h.close)
        self.no_network=patch.object(socket.socket,'connect',side_effect=AssertionError('External networking is forbidden in offline tests'))
        self.no_network.start();self.addCleanup(self.no_network.stop)

    async def open_entry(self,user=1):
        await self.h.message('/menu',user)
        await self.h.click('Browse material',user)
        await self.h.click('Practice',user)
        await self.h.click('Bonjour 0',user)

    async def wait_audio(self):
        for _ in range(100):
            if not self.h.service.jobs:return
            await asyncio.sleep(.01)
        self.fail('Audio job did not finish')

    async def test_real_handlers_browse_search_back_and_clean_chat(self):
        await self.open_entry()
        await self.h.click('Next entry')
        self.assertIn('Hello 1',self.h.current_message()['text'])
        await self.h.click('Back to list')
        await self.h.click('Next page')
        await self.h.click('Bonjour 8')
        await self.h.click('Back to list')
        self.assertIn('page 2/4',self.h.current_message()['text'])
        await self.h.message('سلام 12')
        self.assertIn('1 matches',self.h.current_message()['text'])
        await self.h.click(self.h.buttons()[0]['text'])
        self.assertIn('Hello 12',self.h.current_message()['text'])
        await self.h.click('Back to results')
        self.assertIn('سلام 12',html.unescape(self.h.current_message()['text']))
        self.assertEqual(len(self.h.request.messages),1)
        self.assertTrue(all(len(b['callback_data'].encode())<=64 for b in self.h.buttons()))

    async def test_start_and_menu_reply_with_fresh_visible_menu_and_keep_chat_clean(self):
        await self.open_entry()
        for command in ('/start','/start','/menu','/start@Easy_French_Learning_bot'):
            previous=self.h.state.session(1)['anchor']
            begin=len(self.h.request.calls)
            await self.h.message(command)
            current=self.h.state.session(1)['anchor']
            self.assertNotEqual(current,previous)
            self.assertNotIn((1,previous),self.h.request.messages)
            self.assertIn('Browse material',[b['text'] for b in self.h.buttons()])
            self.assertEqual(len(self.h.request.messages),1)
            self.assertTrue(self.h.state.user(1)['owner'])
            calls=self.h.request.calls[begin:]
            self.assertEqual(calls[0]['method'],'sendmessage')
            self.assertEqual(calls[1]['method'],'deletemessage')

    async def test_failed_start_send_preserves_previous_menu_and_does_not_delete_command(self):
        await self.h.message('/start')
        previous=self.h.state.session(1)['anchor']
        begin=len(self.h.request.calls)
        self.h.request.failures['sendmessage'].append({'error_code':503,'description':'Injected unavailable'})
        await self.h.message('/start')
        self.assertEqual(self.h.state.session(1)['anchor'],previous)
        self.assertIn((1,previous),self.h.request.messages)
        self.assertFalse(any(c['method']=='deletemessage' for c in self.h.request.calls[begin:]))
        await self.h.message('/start')
        self.assertNotEqual(self.h.state.session(1)['anchor'],previous)
        self.assertEqual(len(self.h.request.messages),1)

    async def test_unauthorized_group_forged_and_revoked_inputs(self):
        await self.h.message('Bonjour',99)
        self.assertFalse(any('Hello' in m.get('text','') for (u,_),m in self.h.request.messages.items() if u==99))
        before=len(self.h.request.calls);await self.h.message('Bonjour',98,chat_type='group')
        self.assertEqual(len(self.h.request.calls),before)
        await self.open_entry();message=copy.deepcopy(self.h.current_message());handle=self.h.buttons()[0]['callback_data']
        await self.h.callback(handle,99,message)
        self.h.reader(2);await self.open_entry(2)
        old=copy.deepcopy(self.h.current_message(2));old_handle=self.h.buttons(2)[0]['callback_data']
        self.h.state.revoke(1,2);await self.h.callback(old_handle,2,old)
        protected=[r for r in self.h.request.calls if r['method']=='sendmessage' and r['params'].get('chat_id')==2 and 'Hello' in r['params'].get('text','')]
        self.assertEqual(len(protected),0) # Reader content is edited into its existing anchor, never sent after revoke.

    async def test_audio_works_and_revocation_during_generation_blocks_delivery(self):
        self.h.reader(2);await self.open_entry(2)
        self.speech.release.clear()
        await self.h.click('Listen',2)
        for _ in range(50):
            if self.speech.started.is_set():break
            await asyncio.sleep(.01)
        async with self.h.service.gates[2]:self.h.state.revoke(1,2)
        self.speech.release.set();await self.wait_audio()
        self.assertFalse(any(r['method']=='sendaudio' for r in self.h.request.calls))
        await self.open_entry();await self.h.click('Listen');await self.wait_audio()
        self.assertTrue(self.h.current_message(field='audio')['audio'])
        await self.h.callback(next(iter(self.h.request.messages[(1,self.h.state.session(1)['audio'])]['reply_markup']['inline_keyboard']))[0]['callback_data'],1,
                              self.h.request.messages[(1,self.h.state.session(1)['audio'])])
        self.assertIsNone(self.h.state.session(1)['audio'])

    async def test_navigation_does_not_wait_for_speech_or_deliver_obsolete_audio(self):
        await self.open_entry();self.speech.release.clear()
        await self.h.click('Listen')
        await asyncio.wait_for(self.h.click('Next entry'),.5)
        self.assertIn('Hello 1',self.h.current_message()['text'])
        self.speech.release.set();await self.wait_audio()
        self.assertFalse(any(r['method']=='sendaudio' for r in self.h.request.calls))

    async def test_stale_buttons_after_refresh_cannot_select_new_position(self):
        await self.h.message('/menu');await self.h.click('Browse material');await self.h.click('Practice')
        old=copy.deepcopy(self.h.current_message());handle=self.h.buttons()[0]['callback_data']
        (self.notes/'Practice.txt').write_text('Inserted    |    جدید    |    New\n'+(self.notes/'Practice.txt').read_text('utf-8'),'utf-8')
        self.h.manager.publish(self.h.manager.candidate())
        await self.h.callback(handle,message=old)
        self.assertIn('Notes updated',self.h.current_message()['text'])
        self.assertNotIn('English\nNew',self.h.current_message()['text'])

    async def test_missing_anchor_rate_limit_and_network_recovery(self):
        await self.open_entry();old=self.h.state.session(1)['anchor']
        self.h.request.messages.pop((1,old))
        await self.h.message('/menu')
        self.assertNotEqual(self.h.state.session(1)['anchor'],old)
        self.h.request.failures['editmessagetext'].append({'error_code':429,'description':'Too Many Requests','parameters':{'retry_after':1}})
        await self.h.message('/search');self.assertIn('Type a French',self.h.current_message()['text'])
        self.h.request.failures['editmessagetext'].append({'error_code':503,'description':'Service unavailable'})
        await self.h.message('/settings')
        await self.h.message('/menu');self.assertIn('French_Learning',self.h.current_message()['text'])

    async def test_duplicate_updates_are_idempotent_and_restart_keeps_buttons(self):
        from telegram import Update
        data=self.h.message_update('/menu')
        await self.h.app.process_update(Update.de_json(data,self.h.app.bot))
        before=len(self.h.request.calls)
        await self.h.app.process_update(Update.de_json(data,self.h.app.bot))
        self.assertEqual(before,len(self.h.request.calls))
        handle=self.h.buttons()[0]['callback_data'];message=copy.deepcopy(self.h.current_message())
        # Reload state through another process-compatible connection.
        from french_notes.telegram_bot.state import State
        other=State(self.root/'state',self.notes)
        try:self.assertIsNotNone(other.resolve(handle,1,1))
        finally:other.close()

    async def test_owner_invites_and_revokes_through_real_buttons(self):
        await self.h.message('/menu');await self.h.click('Access');await self.h.click('Create invitation')
        text=html.unescape(self.h.current_message()['text']);token=text.split('?start=')[1]
        await self.h.message('/start '+token,2);self.assertTrue(self.h.state.allowed(2))
        await self.h.message('/start '+token,3);self.assertFalse(self.h.state.allowed(3))
        await self.h.message('/menu');await self.h.click('Access');await self.h.click('Readers')
        await self.h.click('Reader 2 · active');await self.h.click('Revoke access');await self.h.click('Confirm revoke')
        self.assertFalse(self.h.state.allowed(2))

    async def test_revocation_serializes_with_inflight_delivery(self):
        self.h.reader(2);await self.open_entry(2)
        entered=asyncio.Event();release=asyncio.Event()
        async def pause(name,params):
            if name=='sendaudio':entered.set();await release.wait()
        self.h.request.before_request=pause
        await self.h.click('Listen',2)
        await asyncio.wait_for(entered.wait(),1)
        async def revoke():
            async with self.h.service.gates[2]:self.h.state.revoke(1,2)
        task=asyncio.create_task(revoke());await asyncio.sleep(.01)
        self.assertFalse(task.done())
        release.set();await task;await self.wait_audio()
        self.assertFalse(self.h.state.allowed(2))
        sent=sum(r['method']=='sendaudio' for r in self.h.request.calls)
        await self.h.message('Bonjour',2)
        self.assertEqual(sum(r['method']=='sendaudio' for r in self.h.request.calls),sent)

    async def test_forged_owner_button_in_reader_chat_does_not_create_invite(self):
        await self.h.message('/menu');await self.h.click('Access')
        handle=next(b['callback_data'] for b in self.h.buttons() if b['text']=='Create invitation')
        self.h.reader(2);await self.h.message('/menu',2)
        before=len(self.h.state.invitations())
        await self.h.callback(handle,2)
        self.assertEqual(len(self.h.state.invitations()),before)
        self.assertNotIn('Create invitation',[b['text'] for b in self.h.buttons(2)])


if __name__=='__main__':unittest.main()
