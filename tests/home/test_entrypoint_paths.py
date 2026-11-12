"""Compatibility options cannot create empty state or bypass polling ownership."""
import tempfile,unittest
from pathlib import Path
from home_bot.bot import main
from home_bot.storage import Store

class EntrypointPathTests(unittest.TestCase):
    def test_missing_explicit_state_blocks_without_creating_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'missing'
            with self.assertRaisesRegex(RuntimeError,'missing'):main(['--data-dir',str(target),'--backup'])
            self.assertFalse(target.exists())
    def test_explicit_unregistered_state_cannot_start_legacy_poller(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary);store=Store(target);store.close();before=(target/'tasks.sqlite3').read_bytes()
            with self.assertRaisesRegex(RuntimeError,'registered canonical'):main(['--data-dir',str(target),'--background'])
            self.assertEqual((target/'tasks.sqlite3').read_bytes(),before)

if __name__=='__main__':unittest.main()
