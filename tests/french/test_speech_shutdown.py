"""Native speech-worker shutdown and the close-versus-late-start race."""
import os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from french_notes.speech_process import SpeechPool,SpeechProcess

class SpeechShutdownTests(unittest.TestCase):
    def test_native_worker_close_unblocks_generation_and_prevents_new_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'edge_tts';package.mkdir();started=root/'started'
            (package/'__init__.py').write_text('import asyncio,os\nfrom pathlib import Path\nclass Communicate:\n def __init__(self,*args,**kwargs):pass\n async def save(self,output):\n  Path(os.environ["SPEECH_STARTED"]).write_text("ready")\n  await asyncio.sleep(60)\n',encoding='utf-8')
            (package/'communicate.py').write_text('import ssl\n_SSL_CTX=ssl.create_default_context()\n',encoding='utf-8');(package/'exceptions.py').write_text('class NoAudioReceived(Exception):pass\n',encoding='utf-8')
            worker=SpeechProcess(root,python_executable=sys.executable);errors=[];launched=[]
            original=subprocess.Popen
            def launch(*args,**kwargs):
                process=original(*args,**dict(kwargs,env=dict(os.environ,PYTHONPATH=str(root),SPEECH_STARTED=str(started))))
                launched.append(process);return process
            def generate():
                try:worker.generate({'text':'Été préservé','voice':'fr-FR-DeniseNeural','rate':'+0%'},root/'output.mp3')
                except Exception as error:errors.append(error)
            with patch('french_notes.speech_process.subprocess.Popen',side_effect=launch):
                thread=threading.Thread(target=generate);thread.start()
                try:
                    deadline=time.monotonic()+8
                    while not started.exists() and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(started.exists());worker.close();thread.join(3)
                    self.assertFalse(thread.is_alive());self.assertTrue(errors);self.assertIsNotNone(launched[0].poll())
                    with self.assertRaises(RuntimeError):worker.generate({},root/'late.mp3')
                    self.assertEqual(len(launched),1);self.assertFalse((root/'output.mp3').exists())
                finally:worker.close();thread.join(5)
    def test_pool_close_wins_against_generation_paused_before_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            pool=SpeechPool(temporary,python_executable=sys.executable);entered=threading.Event();release=threading.Event();errors=[]
            for worker in pool.workers:
                original=worker.start
                def paused_start(original=original):entered.set();release.wait(3);return original()
                worker.start=paused_start
            def generate():
                try:pool.generate({},Path(temporary)/'late.mp3')
                except Exception as error:errors.append(error)
            with patch('french_notes.speech_process.subprocess.Popen') as launch:
                thread=threading.Thread(target=generate);thread.start()
                try:
                    self.assertTrue(entered.wait(2));pool.close();release.set();thread.join(2)
                    self.assertFalse(thread.is_alive());self.assertTrue(errors);launch.assert_not_called()
                finally:release.set();pool.close();thread.join(4)

if __name__=='__main__':unittest.main()
