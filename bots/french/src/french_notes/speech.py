"""On-demand neural French speech, cached separately for each voice and speed."""
from pathlib import Path
from concurrent.futures import Future
import threading
import uuid
import sys

from .store import NotesError, replace_with_retry, sha
from .speech_process import SpeechPool


DEFAULT_VOICE = 'denise'
VOICES = {
    'denise': 'fr-FR-DeniseNeural',
    'henri': 'fr-FR-HenriNeural',
    'vivienne': 'fr-FR-VivienneMultilingualNeural',
}
RATES = {'1': '+0%', '0.8': '-20%'}
CACHE_VERSION = 'edge-neural-utf8-v2'


class FrenchSpeech:
    def __init__(self, state, root, python_executable=None):
        self.cache = Path(state) / 'speech'
        self.python = Path(python_executable) if python_executable else Path(sys.executable)
        self.worker = SpeechPool(root, python_executable)
        self.lock = threading.Lock()
        self.pending = {}

    def close(self):
        self.worker.close()

    @staticmethod
    def valid(data):
        # The service returns MPEG-2 Layer III, 24 kHz, 48 kbps: 144 bytes/frame.
        # Require two complete consecutive frames, not just an MP3-like prefix.
        return (len(data) >= 288 and data[:3] == b'\xff\xf3\x64'
                and data[144:147] == b'\xff\xf3\x64' and len(data) % 144 == 0)

    @staticmethod
    def cache_key(text,voice=DEFAULT_VOICE,speed='1'):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000 or '\x00' in text:
            raise NotesError('Choose a French entry up to 4,000 characters.')
        if not isinstance(voice, str) or voice not in VOICES:
            raise NotesError('Choose Denise, Henri, or Vivienne for the French voice.')
        if not isinstance(speed, str) or speed not in RATES:
            raise NotesError('Choose Normal or Slow for the French speed.')
        return sha((CACHE_VERSION+'\0'+VOICES[voice]+'\0'+RATES[speed]+'\0'+text.strip()).encode('utf-8'))

    def cached(self,text,voice=DEFAULT_VOICE,speed='1'):
        """Read an existing recording immediately; never start a worker/network call."""
        saved=self.cache/(self.cache_key(text,voice,speed)+'.mp3')
        try:
            data=saved.read_bytes()
            return data if self.valid(data) else None
        except OSError:return None

    def synthesize(self, text, voice=DEFAULT_VOICE, speed='1'):
        key=self.cache_key(text,voice,speed)
        text = text.strip()
        # Old workers could pronounce mojibake while caching it under the
        # correct text. Preserve that cache on disk, but never play it again.
        self.cache.mkdir(parents=True, exist_ok=True)
        saved = self.cache / (key + '.mp3')
        # A cached phrase must never wait behind an unrelated network request.
        if saved.exists():
            data = saved.read_bytes()
            if self.valid(data):
                return data
        with self.lock:
            owner = key not in self.pending
            future = self.pending.setdefault(key, Future())
        if not owner:
            try:
                return future.result(timeout=110)
            except TimeoutError:
                raise NotesError('French audio took too long. You can save this row and try again later.', 503, 'speech') from None
        try:
            if saved.exists():
                data = saved.read_bytes()
                if self.valid(data):
                    future.set_result(data)
                    return data
            if not self.python.exists():
                raise NotesError('French speech needs its local voice package. See the setup in README.md.', 503, 'speech')
            temporary = self.cache / (uuid.uuid4().hex + '.mp3')
            try:
                self.worker.generate(dict(text=text, voice=VOICES[voice], rate=RATES[speed]), temporary)
                data = temporary.read_bytes()
                if not self.valid(data):
                    raise NotesError('The French voice service returned no playable audio. Please try again.', 503, 'speech')
                replace_with_retry(temporary, saved)
                future.set_result(data)
                return data
            except TimeoutError:
                raise NotesError('French audio took too long. Check your connection or try a shorter entry.', 503, 'speech') from None
            except (OSError, RuntimeError):
                raise NotesError('The French voice service is unavailable. You can keep editing and save this row; try its speaker again later.', 503, 'speech') from None
            finally:
                temporary.unlink(missing_ok=True)
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
