import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from french_notes.telegram_bot.__main__ import Instance,SafeFormatter,StartupError,hidden_token
from french_notes.telegram_bot.guard import ReadOnlyGuard
from french_notes.telegram_bot.state import State


class StartupTests(unittest.TestCase):
    def test_source_write_guard_blocks_write_delete_rename_and_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);notes=root/'notes';notes.mkdir();p=notes/'Example.txt';p.write_text('Protected')
            guard=ReadOnlyGuard(notes).install()
            try:
                self.assertEqual(p.read_text(),'Protected')
                for operation in (lambda:p.write_text('Bad'),lambda:p.unlink(),lambda:p.rename(root/'moved'),lambda:State(notes/'state',notes)):
                    with self.assertRaises((PermissionError,ValueError)):operation()
                self.assertEqual(p.read_text(),'Protected')
            finally:guard.active=False

    def test_hidden_input_never_falls_back_to_echo(self):
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as prompt:
            with self.assertRaises(StartupError):hidden_token()
            prompt.assert_not_called()
        import getpass
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=getpass.GetPassWarning()):
            with self.assertRaises(StartupError):hidden_token()

    def test_token_redaction_in_logs(self):
        token='987654:synthetic_secret_012345678901234567890'
        formatter=SafeFormatter(token)
        record=logging.LogRecord('test',logging.ERROR,'',0,'Failure at https://api.telegram.org/bot'+token+'/getMe',(),None)
        output=formatter.format(record)
        self.assertNotIn(token,output);self.assertIn('[REDACTED]',output)

    @unittest.skipUnless(os.name=='nt','Windows mutex test')
    def test_windows_mutex_blocks_duplicate_and_releases(self):
        with Instance('OfflineStartupTest'):
            with self.assertRaises(StartupError):
                with Instance('OfflineStartupTest'):pass
        with Instance('OfflineStartupTest'):pass

    def test_corrupt_database_is_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);notes=root/'notes';notes.mkdir();state=root/'state';state.mkdir()
            path=state/'reader.sqlite3';raw=b'not a database';path.write_bytes(raw)
            with self.assertRaises(Exception):State(state,notes)
            self.assertEqual(path.read_bytes(),raw)


if __name__=='__main__':unittest.main()
