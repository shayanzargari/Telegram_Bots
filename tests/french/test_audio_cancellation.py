"""Finite audio cancellation drains workers and validates assets again on resume."""
import json,tempfile,threading,unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from french_notes.speech import FrenchSpeech
from french_notes.telegram_bot.prepare_audio import prepare
from french_notes.telegram_bot.state import State
from french_notes.telegram_bot.refresh import LibraryManager

FIXTURE=(Path(__file__).parent/'fixtures/denise.mp3').read_bytes()

class AudioCancellationTests(unittest.TestCase):
    def test_cancel_drains_active_work_and_resume_revalidates_every_cached_asset(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as cleanup:
            root=Path(temporary);notes=root/'notes';notes.mkdir();source=notes/'Practice.txt';source.write_text(''.join(f'Bonjour {i}    |    سلام    |    Hello\n' for i in range(6)),encoding='utf-8')
            before=source.read_bytes();state=State(root/'reader',notes);cleanup.callback(state.close);manager=LibraryManager(notes,state);manager.publish(manager.candidate())
            stop=threading.Event();started=threading.Event();release=threading.Event();lock=threading.Lock()
            class Speech:
                def __init__(self):self.saved={};self.running=0;self.calls=[];self.block=True
                def cached(self,text,voice,speed):
                    data=self.saved.get((text,voice,speed));return data if data and FrenchSpeech.valid(data) else None
                def synthesize(self,text,voice,speed):
                    with lock:
                        self.running+=1;self.calls.append(text)
                        if self.running==2:started.set()
                    try:
                        if self.block:release.wait(3)
                        self.saved[text,voice,speed]=FIXTURE
                        return FIXTURE
                    finally:
                        with lock:self.running-=1
                def close(self):release.set()
            speech=Speech();reports=[]
            def cancel_when_started():
                if started.wait(2):stop.set()
            cancellation=threading.Thread(target=cancel_when_started);cancellation.start()
            with patch('french_notes.telegram_bot.prepare_audio.FrenchSpeech',return_value=speech):
                first=prepare(state,notes,stop=stop,progress=lambda ready,total:reports.append((ready,total)))
            cancellation.join();self.assertTrue(first['interrupted']);self.assertEqual(speech.running,0);self.assertLessEqual(len(speech.calls),2)
            # A completion marker never makes a corrupt cache entry trustworthy.
            key=next(iter(speech.saved));speech.saved[key]=b'corrupted MP3';speech.block=False;stop.clear();calls=len(speech.calls)
            with patch('french_notes.telegram_bot.prepare_audio.FrenchSpeech',return_value=speech):second=prepare(state,notes,stop=stop)
            self.assertTrue(second['complete']);self.assertEqual(second['ready'],6);self.assertGreater(len(speech.calls),calls)
            self.assertTrue(all(FrenchSpeech.valid(data) for data in speech.saved.values()));self.assertEqual(source.read_bytes(),before)
            self.assertTrue(json.loads((state.root/'audio-preparation.json').read_text('utf-8'))['complete'])

if __name__=='__main__':unittest.main()
