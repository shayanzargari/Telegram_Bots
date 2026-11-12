import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from french_notes.telegram_bot.__main__ import StartupError,run
from french_notes.telegram_bot.offline import FakeRequest,FAKE_TOKEN
from french_notes.telegram_bot.refresh import LibraryManager
from french_notes.telegram_bot.state import State


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);notes=root/'notes';notes.mkdir();(notes/'Example.txt').write_text('Hello\n')
        self.state=State(root/'state',notes);self.addCleanup(self.state.close)
        self.state.redeem(self.state.invitation('owner'),1,'Owner')
        self.manager=LibraryManager(notes,self.state);self.manager.publish(self.manager.candidate())
        self.fake=FakeRequest(self.state)

    async def execute(self):
        class Speech:
            def close(self):pass
        with patch('telegram.request.HTTPXRequest',side_effect=lambda **kw:self.fake),patch('french_notes.speech.FrenchSpeech',return_value=Speech()):
            await asyncio.wait_for(run(self.state,self.manager,FAKE_TOKEN),5)

    async def test_existing_webhook_is_not_removed(self):
        self.fake.webhook='https://existing.example/webhook'
        with self.assertRaisesRegex(StartupError,'webhook'):await self.execute()
        self.assertFalse(any(c['method']=='deletewebhook' for c in self.fake.calls))
        self.assertTrue(self.state.has_owner())

    async def test_wrong_bot_and_invalid_token_are_rejected(self):
        self.fake.username='Different_bot'
        with self.assertRaisesRegex(StartupError,'different bot'):await self.execute()
        self.fake.username='Easy_French_Learning_bot'
        self.fake.failures['getme'].append({'error_code':401,'description':'Unauthorized'})
        with self.assertRaises(StartupError):await self.execute()
        self.assertTrue(self.state.has_owner())

    async def test_real_polling_lifecycle_stops_on_conflict(self):
        self.fake.failures['getupdates'].append({'error_code':409,'description':'Conflict: another getUpdates request'})
        await self.execute()
        methods=[c['method'] for c in self.fake.calls]
        self.assertIn('setmyname',methods);self.assertIn('getupdates',methods)
        self.assertTrue((self.state.root/'reader.backup.sqlite3').exists())
        self.assertEqual(self.state.meta('bot_id'),'123456')


if __name__=='__main__':unittest.main()
