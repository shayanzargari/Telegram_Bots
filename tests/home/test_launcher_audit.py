"""Isolated Windows launcher and cross-process singleton checks."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from home_bot.bot import process_lock

ROOT = Path(__file__).resolve().parents[1]


class LauncherAuditTests(unittest.TestCase):
    def test_background_confirmation_uses_registered_menu_command(self):
        text = (Path(__file__).parent / 'fixtures/RUN_BACKGROUND.vbs').read_text('utf-8')
        self.assertIn('Check /menu in your Telegram group', text)
        self.assertNotIn('Check /list', text)

    def test_singleton_lock_blocks_another_process_then_releases(self):
        with tempfile.TemporaryDirectory(prefix='home-lock-audit-') as directory:
            lock = Path(directory) / 'bot.lock'
            code = 'from pathlib import Path; from home_bot.bot import process_lock\nwith process_lock(Path(__import__("sys").argv[1])): print("acquired")'
            options = {'cwd':str(ROOT),'capture_output':True,'text':True,'timeout':8}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW
            with process_lock(lock):
                blocked = subprocess.run([sys.executable, '-c', code, str(lock)], **options)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertNotIn('acquired', blocked.stdout)
            released = subprocess.run([sys.executable, '-c', code, str(lock)], **options)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertIn('acquired', released.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('cscript'), 'Windows Script Host required')
    def test_launcher_unknown_mode_fails_in_isolated_folder(self):
        with tempfile.TemporaryDirectory(prefix='home launcher unicode-') as directory:
            path = Path(directory) / 'RUN_BACKGROUND.vbs'
            shutil.copy2(Path(__file__).parent / 'fixtures/RUN_BACKGROUND.vbs', path)
            result = subprocess.run(['cscript.exe', '//nologo', str(path), '--audit-invalid-mode'],
                cwd=directory, capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Unknown option', result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
