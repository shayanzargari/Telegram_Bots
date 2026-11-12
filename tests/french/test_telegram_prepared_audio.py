import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from french_notes.telegram_bot.audio import AudioPreparation,audio_key
from french_notes.telegram_bot.offline import Harness
from french_notes.telegram_bot.prepare_audio import prepare

FIXTURE=Path(__file__).parent/'fixtures/denise.mp3'


class CachedSpeech:
    def __init__(self):
        self.calls=[];self.saved={};self.blocked=None;self.started=threading.Event();self.release=threading.Event();self.fail=False
    def cached(self,text,voice,speed):return self.saved.get((text,voice,speed))
    def synthesize(self,text,voice,speed):
        self.calls.append((text,voice,speed));self.started.set()
        if text==self.blocked or self.blocked=='*':self.release.wait(5)
        if self.fail:raise RuntimeError('Fixture unavailable')
        data=FIXTURE.read_bytes();self.saved[text,voice,speed]=data;return data
    def close(self):self.release.set()


class PreparedAudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.notes=self.root/'notes';self.notes.mkdir()
        (self.notes/'Practice.txt').write_text('\n'.join(f'Bonjour {i}    |    سلام {i}    |    Hello {i}' for i in range(12)),'utf-8')
        self.speech=CachedSpeech();self.h=await Harness(self.notes,self.root/'state',self.speech,automatic_audio=True).start()
        self.addAsyncCleanup(self.h.close)

    async def listing(self):
        await self.h.message('/menu');await self.h.click('Browse material');await self.h.click('Practice')

    async def wait_player(self):
        for _ in range(150):
            if self.h.state.session(1)['audio'] and not self.h.service.jobs:return
            await asyncio.sleep(.01)
        self.fail('Player did not become available')

    async def test_entry_has_player_without_listen_and_reuses_recording(self):
        await self.listing();await self.h.click('Bonjour 0');await self.wait_player()
        self.assertTrue(self.h.current_message(field='audio')['audio'])
        self.assertTrue(self.h.current_message(field='audio')['audio']['file_id'])
        self.assertNotIn('Listen',[b['text'] for b in self.h.buttons()])
        self.assertTrue(all('سلام' not in text and 'Hello' not in text for text,_,_ in self.speech.calls))
        before=len([c for c in self.speech.calls if c[0]=='Bonjour 0'])
        await self.h.click('Back to list');await self.h.click('Bonjour 0');await self.wait_player()
        self.assertEqual(len([c for c in self.speech.calls if c[0]=='Bonjour 0']),before)
        media=[c for c in self.h.request.calls if c['method']=='sendaudio'][-1]
        self.assertTrue(media['params']['audio'].startswith('fixture_audio_'))

    async def test_foreground_entry_does_not_wait_for_other_background_phrase(self):
        self.speech.blocked='Bonjour 0'
        await self.listing()
        for _ in range(80):
            if self.speech.started.is_set():break
            await asyncio.sleep(.005)
        await self.h.click('Bonjour 5')
        await asyncio.wait_for(self.wait_player(),.5)
        self.assertFalse(self.speech.release.is_set())
        self.assertIn(('Bonjour 5','denise','1'),self.speech.calls)
        self.speech.release.set()

    async def test_manual_cached_listen_uses_one_media_request_and_repeat_none(self):
        self.h.service.automatic_audio=False;self.h.service.preload=False
        await self.listing();await self.h.click('Bonjour 0')
        self.h.state.set_meta(audio_key('Bonjour 0','denise','1'),'fixture_audio_saved')
        start=len(self.h.request.calls)
        await self.h.click('Listen');await self.wait_player()
        methods=[c['method'] for c in self.h.request.calls[start:]]
        self.assertEqual(methods,['answercallbackquery','sendaudio'])
        self.assertEqual(self.speech.calls,[])
        start=len(self.h.request.calls);await self.h.click('Listen')
        self.assertEqual([c['method'] for c in self.h.request.calls[start:]],['answercallbackquery'])

    async def test_bad_telegram_file_id_recovers_from_local_recording(self):
        self.h.service.preload=False
        self.speech.saved['Bonjour 0','denise','1']=FIXTURE.read_bytes()
        self.h.state.set_meta(audio_key('Bonjour 0','denise','1'),'expired_file_id')
        self.h.request.failures['sendaudio'].append({'error_code':400,'description':'Bad Request: wrong file identifier'})
        await self.listing();await self.h.click('Bonjour 0');await self.wait_player()
        self.assertEqual(self.speech.calls,[])
        self.assertTrue(self.h.current_message(field='audio')['audio'])

    async def test_mode_and_voice_preferences_are_preserved(self):
        await self.h.message('/settings');await self.h.click('Listen button only')
        self.assertFalse(self.h.state.user(1)['auto_audio'])
        await self.h.click('Henri');await self.h.click('Slow')
        await self.listing();await self.h.click('Bonjour 0');await self.h.click('Listen');await self.wait_player()
        self.assertIn(('Bonjour 0','henri','0.8'),self.speech.calls)
        await self.h.message('/settings');await self.h.click('Audio with entry')
        self.assertTrue(self.h.state.user(1)['auto_audio'])

    async def test_failed_generation_has_retry_without_automatic_loop(self):
        self.h.service.preload=False;self.speech.fail=True
        await self.listing();await self.h.click('Bonjour 0')
        for _ in range(100):
            if not self.h.service.jobs:break
            await asyncio.sleep(.01)
        self.assertEqual(len(self.speech.calls),1)
        self.assertIn('Retry audio',[b['text'] for b in self.h.buttons()])
        self.speech.fail=False;await self.h.click('Retry audio');await self.wait_player()

    async def test_revoked_readers_do_not_prepare_queued_phrases(self):
        self.h.reader(2);self.speech.blocked='Bonjour 0'
        await self.h.message('/menu',2);await self.h.click('Browse material',2);await self.h.click('Practice',2)
        for _ in range(80):
            if self.speech.started.is_set():break
            await asyncio.sleep(.005)
        self.h.state.revoke(1,2);self.speech.release.set()
        await asyncio.wait_for(self.h.service.preparation.queue.join(),1)
        self.assertEqual(len(self.speech.calls),1)
        self.assertFalse(any(c['method']=='sendaudio' for c in self.h.request.calls))

    async def test_preparation_deduplicates_and_survives_navigation_cancel(self):
        self.speech.blocked='Shared'
        prep=self.h.service.preparation
        first=asyncio.create_task(prep.get('Shared','denise','1'))
        for _ in range(80):
            if self.speech.started.is_set():break
            await asyncio.sleep(.005)
        first.cancel();await asyncio.gather(first,return_exceptions=True)
        second=asyncio.create_task(prep.get('Shared','denise','1'))
        self.speech.release.set();await second
        self.assertEqual(len(self.speech.calls),1)

    async def test_bundle_is_resumable_and_sends_no_telegram_messages(self):
        before=len(self.h.request.calls)
        with patch('french_notes.telegram_bot.prepare_audio.FrenchSpeech',return_value=self.speech):
            first=prepare(self.h.state,self.notes,limit=2)
            second=prepare(self.h.state,self.notes,limit=2)
        self.assertTrue(first['complete']);self.assertEqual(second['already_cached'],2)
        self.assertEqual(len(self.speech.calls),2)
        self.assertEqual(len(self.h.request.calls),before)

    async def test_rapid_navigation_cancels_generation_that_has_not_started(self):
        self.speech.blocked='*';prep=self.h.service.preparation
        tasks=[asyncio.create_task(prep.get('Phrase '+str(i),'denise','1')) for i in range(40)]
        for _ in range(100):
            if len(self.speech.calls)==2:break
            await asyncio.sleep(.005)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);await asyncio.sleep(.01)
        self.assertLessEqual(len(prep.inflight),2)
        self.speech.release.set()
        await asyncio.gather(*list(prep.inflight.values()),return_exceptions=True)
        self.assertEqual(len(self.speech.calls),2)

    async def test_long_recording_parts_reuse_one_player(self):
        self.h.service.preload=False
        self.h.state.setting(1,auto_audio=False)
        path=self.notes/'Practice.txt'
        path.write_text(('Bonjour. '*900)+'    |    فارسی    |    English\n','utf-8')
        self.h.manager.publish(self.h.manager.candidate())
        await self.listing()
        await self.h.click(self.h.buttons()[0]['text'])
        await self.h.click('Listen 1/3');await self.wait_player()
        player=self.h.state.session(1)['audio']
        first=self.h.current_message(field='audio')['audio']['file_id']
        await self.h.click('Listen 2/3');await self.wait_player()
        self.assertEqual(self.h.state.session(1)['audio'],player)
        self.assertNotEqual(self.h.current_message(field='audio')['audio']['file_id'],first)
        self.assertEqual(sum(c['method']=='sendaudio' for c in self.h.request.calls),1)
        self.assertEqual(sum(c['method']=='editmessagemedia' for c in self.h.request.calls),1)

    async def test_removed_player_does_not_return_after_pending_part_finishes(self):
        self.h.service.preload=False
        self.h.state.setting(1,auto_audio=False)
        (self.notes/'Practice.txt').write_text(('Bonjour. '*900)+'    |    فارسی    |    English\n','utf-8')
        self.h.manager.publish(self.h.manager.candidate())
        await self.listing();await self.h.click(self.h.buttons()[0]['text'])
        await self.h.click('Listen 1/3');await self.wait_player()
        player=self.h.current_message(field='audio')
        self.speech.blocked='*';self.speech.started.clear()
        await self.h.click('Listen 2/3')
        for _ in range(80):
            if self.speech.started.is_set():break
            await asyncio.sleep(.005)
        self.assertTrue(self.speech.started.is_set())
        remove=player['reply_markup']['inline_keyboard'][0][0]['callback_data']
        await self.h.callback(remove,message=player)
        self.speech.release.set()
        await asyncio.gather(*list(self.h.service.preparation.inflight.values()),return_exceptions=True)
        await asyncio.sleep(.01)
        self.assertIsNone(self.h.state.session(1)['audio'])
        self.assertEqual(sum(c['method']=='sendaudio' for c in self.h.request.calls),1)

    async def test_uneditable_player_is_replaced_using_cached_file_id(self):
        self.h.service.preload=False;self.h.service.automatic_audio=False
        await self.listing();await self.h.click('Bonjour 0');await self.h.click('Listen');await self.wait_player()
        old=self.h.state.session(1)['audio']
        self.h.state.set_meta('displayed_audio:1','another-recording')
        self.h.request.failures['editmessagemedia'].append({'error_code':400,'description':"Bad Request: message can't be edited"})
        await self.h.click('Listen');await self.wait_player()
        self.assertNotEqual(old,self.h.state.session(1)['audio'])
        self.assertEqual(len(self.speech.calls),1)
        self.assertTrue(self.h.current_message(field='audio')['audio']['file_id'])


if __name__=='__main__':unittest.main()
