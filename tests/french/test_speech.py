import concurrent.futures
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from french_notes.speech import FrenchSpeech, VOICES
from french_notes.store import NotesError, sha


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(__file__).resolve().parents[1]
        self.speech = FrenchSpeech(self.temp.name, self.root)
        self.addCleanup(self.speech.close)
        self.audio = (Path(__file__).parent / 'fixtures' / 'denise.mp3').read_bytes()
        self.assertTrue(FrenchSpeech.valid(self.audio))

    def generated(self, request, output):
        Path(output).write_bytes(self.audio)

    def test_cached_audio_works_without_worker_or_internet(self):
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated) as run:
            self.assertEqual(self.speech.synthesize('Oreille'), self.audio)
            cached = next(self.speech.cache.glob('*.mp3'))
            stamp = cached.stat().st_mtime_ns
            self.speech.python = Path(self.temp.name) / 'missing-python.exe'
            self.assertEqual(self.speech.synthesize(' Oreille '), self.audio)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(cached.stat().st_mtime_ns, stamp)

    def test_voice_and_speed_have_separate_caches(self):
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated) as run:
            for voice, speed in [('denise', '1'), ('denise', '0.8'), ('henri', '1'), ('vivienne', '1')]:
                self.speech.synthesize('Bonjour', voice, speed)
            self.assertEqual(run.call_count, 4)
            self.assertEqual([call.args[0]['rate'] for call in run.call_args_list], ['+0%', '-20%', '+0%', '+0%'])
            self.assertEqual(len(list(self.speech.cache.glob('*.mp3'))), 4)
            self.assertFalse(list(self.speech.cache.glob('*.json')))

    def test_audio_from_the_old_encoding_worker_is_regenerated(self):
        self.speech.cache.mkdir()
        key = sha(('edge-neural-v1\0' + VOICES['denise'] + '\0+0%\0éplucher').encode('utf-8'))
        legacy = self.speech.cache / (key + '.mp3')
        legacy.write_bytes(self.audio)
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated) as run:
            self.assertEqual(self.speech.synthesize('éplucher'), self.audio)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0]['text'], 'éplucher')
            self.assertEqual(legacy.read_bytes(), self.audio)
            self.assertEqual(len(list(self.speech.cache.glob('*.mp3'))), 2)

    def test_invalid_inputs_do_not_contact_service(self):
        with patch.object(self.speech.worker, 'generate') as run:
            for text in [None, [], '', ' ', 'a' * 4001, 'a\0b']:
                with self.subTest(text=type(text).__name__), self.assertRaises(NotesError):
                    self.speech.synthesize(text)
            for voice, speed in [('invalid', '1'), ([], '1'), ('denise', None), ('denise', 0.8)]:
                with self.subTest(voice=voice, speed=speed), self.assertRaises(NotesError):
                    self.speech.synthesize('Bonjour', voice, speed)
            run.assert_not_called()

    def test_invalid_cache_is_replaced_and_legacy_wav_ignored(self):
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated) as run:
            self.speech.synthesize('Bonjour')
            cached = next(self.speech.cache.glob('*.mp3'))
            cached.write_bytes(b'<html>blocked</html>')
            legacy = self.speech.cache / 'previous.wav'
            legacy.write_bytes(b'RIFF previous voice')
            self.assertEqual(self.speech.synthesize('Bonjour'), self.audio)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(legacy.read_bytes(), b'RIFF previous voice')

    def test_failure_and_timeout_never_cache_partial_audio(self):
        def fail(request, output):
            Path(output).write_bytes(self.audio[:50])
            raise RuntimeError('Service unavailable')

        for effect in [fail, TimeoutError('Voice service timed out')]:
            with self.subTest(effect=type(effect).__name__), patch.object(self.speech.worker, 'generate', side_effect=effect):
                with self.assertRaises(NotesError) as raised:
                    self.speech.synthesize('Bonjour')
                self.assertEqual(raised.exception.status, 503)
                self.assertEqual(list(self.speech.cache.iterdir()), [])

    def test_invalid_service_audio_does_not_become_a_cache_entry(self):
        def invalid(request, output):
            Path(output).write_bytes(b'not an MP3')

        with patch.object(self.speech.worker, 'generate', side_effect=invalid), self.assertRaises(NotesError):
            self.speech.synthesize('Bonjour')
        self.assertEqual(list(self.speech.cache.iterdir()), [])

    def test_concurrent_same_phrase_is_generated_once(self):
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated) as run:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(self.speech.synthesize, ['Bonjour'] * 3))
            self.assertEqual(results, [self.audio] * 3)
            self.assertEqual(run.call_count, 1)

    def test_cached_phrase_does_not_wait_for_an_unrelated_generation(self):
        with patch.object(self.speech.worker, 'generate', side_effect=self.generated):
            self.speech.synthesize('Bonjour')
        started, release = threading.Event(), threading.Event()

        def slow(request, output):
            started.set()
            self.assertTrue(release.wait(3))
            self.generated(request, output)

        with patch.object(self.speech.worker, 'generate', side_effect=slow):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(self.speech.synthesize, 'Au revoir')
                self.assertTrue(started.wait(2))
                try:
                    cached = pool.submit(self.speech.synthesize, 'Bonjour')
                    self.assertEqual(cached.result(timeout=.5), self.audio)
                finally:
                    release.set()
                self.assertEqual(pending.result(timeout=2), self.audio)

    def test_unrelated_phrases_can_generate_concurrently(self):
        barrier = threading.Barrier(2)

        def generated(request, output):
            barrier.wait(timeout=2)
            self.generated(request, output)

        with patch.object(self.speech.worker, 'generate', side_effect=generated):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(self.speech.synthesize, ['Bonjour', 'Au revoir'])), [self.audio] * 2)


if __name__ == '__main__':
    unittest.main()
