import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from french_notes.speech_process import SpeechProcess


class SpeechEncodingTests(unittest.TestCase):
    def test_real_worker_preserves_unicode_with_windows_legacy_encoding(self):
        root = Path(__file__).resolve().parents[1]
        worker = SpeechProcess(root)
        self.addCleanup(worker.close)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            package = folder / 'edge_tts'
            package.mkdir()
            (package / '__init__.py').write_text(
                'import json\nfrom pathlib import Path\n'
                'class Communicate:\n'
                ' def __init__(self, text, voice, **options):\n'
                '  self.request = dict(text=text, voice=voice, rate=options["rate"])\n'
                ' async def save(self, output):\n'
                '  Path(output).write_text(json.dumps(self.request), encoding="ascii")\n',
                encoding='utf-8')
            (package / 'communicate.py').write_text(
                'import ssl\n_SSL_CTX = ssl.create_default_context()\n', encoding='utf-8')
            (package / 'exceptions.py').write_text(
                'class NoAudioReceived(Exception): pass\n', encoding='utf-8')
            # Launch the actual worker and use its real JSON pipes. Only the
            # remote speech service is replaced with an exact text capture.
            env = dict(os.environ, PYTHONPATH=str(folder),
                       PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
            original_popen = subprocess.Popen

            def launch(*args, **options):
                return original_popen(*args, **dict(options, env=env))

            with patch('french_notes.speech_process.subprocess.Popen', side_effect=launch):
                for text in ['éplucher', 'Été, très, fête, Noël, déjà, où, garçon, cœur, œuf.',
                             '« L’élève prépare une crème brûlée. »']:
                    with self.subTest(text=text):
                        output = folder / 'captured.json'
                        request = dict(text=text, voice='fr-FR-DeniseNeural', rate='+0%')
                        worker.generate(request, output)
                        self.assertEqual(json.loads(output.read_text('ascii')), request)
                destination = folder / 'été-فارسی'
                destination.mkdir()
                output = destination / 'éplucher.json'
                worker.generate(request, output)
                self.assertEqual(json.loads(output.read_text('ascii')), request)
            worker.close()


if __name__ == '__main__':
    unittest.main()
