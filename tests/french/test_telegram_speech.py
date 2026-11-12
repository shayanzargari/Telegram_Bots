import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from french_notes.speech import FrenchSpeech
from french_notes.telegram_bot.service import Service

ROOT=Path(__file__).resolve().parents[1]


class BotSpeechTests(unittest.TestCase):
    def test_bot_interpreter_actual_worker_unicode_and_local_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);package=folder/'edge_tts';package.mkdir()
            fixture=Path(__file__).parent/'fixtures/denise.mp3';capture=folder/'captured.json'
            (package/'__init__.py').write_text(
                'import json,os\nfrom pathlib import Path\nclass Communicate:\n'
                ' def __init__(self,text,voice,**kw): self.data=dict(text=text,voice=voice,rate=kw["rate"])\n'
                ' async def save(self,output):\n'
                '  Path(os.environ["BOT_CAPTURE"]).write_text(json.dumps(self.data),encoding="ascii")\n'
                '  Path(output).write_bytes(Path(os.environ["BOT_FIXTURE"]).read_bytes())\n','utf-8')
            (package/'communicate.py').write_text('import ssl\n_SSL_CTX=ssl.create_default_context()\n','utf-8')
            (package/'exceptions.py').write_text('class NoAudioReceived(Exception): pass\n','utf-8')
            env=dict(os.environ,PYTHONPATH=str(folder),BOT_FIXTURE=str(fixture),BOT_CAPTURE=str(capture),PYTHONIOENCODING='cp1252',PYTHONUTF8='0')
            original=subprocess.Popen
            def launch(*args,**kwargs):return original(*args,**dict(kwargs,env=env))
            speech=FrenchSpeech(folder/'été-فارسی',ROOT,python_executable=sys.executable)
            try:
                with patch('french_notes.speech_process.subprocess.Popen',side_effect=launch):
                    text='« L’élève prépare une crème brûlée. »'
                    data=speech.synthesize(text,'henri','0.8')
                    self.assertTrue(FrenchSpeech.valid(data))
                    self.assertEqual(json.loads(capture.read_text('ascii')),{'text':text,'voice':'fr-FR-HenriNeural','rate':'-20%'})
                    self.assertEqual(speech.synthesize(text,'henri','0.8'),data)
            finally:speech.close()

    def test_cache_limit_preserves_pending_files_and_evicts_oldest_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            class Speech:cache=folder
            service=Service(None,None,Speech());service.cache_limit=10
            old=folder/('a'*64+'.mp3');new=folder/('b'*64+'.mp3');pending=folder/('c'*32+'.mp3')
            for p in (old,new,pending):p.write_bytes(b'12345678')
            os.utime(old,(1,1));os.utime(new,(2,2))
            service.prune_cache()
            self.assertFalse(old.exists());self.assertTrue(new.exists());self.assertTrue(pending.exists())


if __name__=='__main__':unittest.main()
