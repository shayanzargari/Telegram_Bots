import asyncio
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from french_notes.telegram_bot.__main__ import main,owner_pairing_link,StartupError
from french_notes.telegram_bot.offline import Harness
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.guard import ReadOnlyGuard


class OwnerSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.notes=self.root/'notes';self.notes.mkdir()
        (self.notes/'Example.txt').write_text('Bonjour    |    سلام    |    Hello\n','utf-8')
        self.h=await Harness(self.notes,self.root/'state').start();self.addAsyncCleanup(self.h.close)
        # Explicitly model first launch, before a real owner has paired.
        with self.h.state.transaction() as db:
            db.execute('DELETE FROM users');db.execute('DELETE FROM invitations')
        self.h.state.bind_bot(123456)

    async def test_first_start_gives_setup_instructions_and_no_material(self):
        await self.h.message('/start',42)
        replies=[r['params'].get('text','') for r in self.h.request.calls if r['method']=='sendmessage']
        self.assertIn('Owner setup is not finished yet.',replies[-1])
        self.assertIn('PAIR_OWNER.bat',replies[-1]);self.assertNotIn('Bonjour',replies[-1])
        self.assertFalse(self.h.state.allowed(42));self.assertFalse(self.h.state.has_owner())

    async def test_local_link_pairs_account_without_stopping_running_handlers(self):
        old=owner_pairing_link(self.h.state).split('?start=')[1]
        peer=State(self.root/'state',self.notes)
        try:fresh=owner_pairing_link(peer).split('?start=')[1]
        finally:peer.close()
        self.assertFalse(self.h.state.redeem(old,99,'Other'))
        await self.h.message('/start '+fresh,42)
        self.assertTrue(self.h.state.allowed(42,owner=True))
        self.assertIn('Browse material',[b['text'] for b in self.h.buttons(42)])
        await self.h.message('/start '+fresh,99)
        self.assertFalse(self.h.state.allowed(99))
        with self.assertRaises(StartupError):owner_pairing_link(self.h.state)

    async def test_cli_recovery_does_not_prompt_for_token_or_rebind_owner(self):
        output=io.StringIO()
        guard=ReadOnlyGuard(self.notes)
        # Run the real CLI against a peer connection while the harness is alive.
        try:
            with patch('sys.argv',['telegram_bot','--pair-owner','--data-dir',str(self.notes),'--state-dir',str(self.root/'state')]),patch('french_notes.telegram_bot.__main__.hidden_token',side_effect=AssertionError('Must not request the token')),patch('french_notes.telegram_bot.__main__.ReadOnlyGuard',return_value=guard),redirect_stdout(output):
                main()
        finally:guard.active=False
        link=next(line for line in output.getvalue().splitlines() if line.startswith('https://t.me/'))
        await self.h.message('/start '+link.split('?start=')[1],42)
        self.assertTrue(self.h.state.allowed(42,owner=True))

    async def test_existing_owner_cannot_be_replaced_by_recovery(self):
        link=owner_pairing_link(self.h.state)
        self.h.state.redeem(link.split('?start=')[1],42,'Owner')
        with self.assertRaises(StartupError):owner_pairing_link(self.h.state)
        self.assertEqual([u['id'] for u in self.h.state.readers() if u['owner']],[42])


if __name__=='__main__':unittest.main()
