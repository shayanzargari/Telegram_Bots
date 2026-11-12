"""A small pool of persistent, isolated speech workers."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import sys
import time


class SpeechProcess:
    def __init__(self, root, python_executable=None):
        self.root = Path(root)
        self.python = Path(python_executable) if python_executable else Path(sys.executable)
        self.process = None
        self.responses = None
        self.lifecycle = threading.RLock()
        self.closed = False

    def close(self, permanent=True):
        with self.lifecycle:
            if permanent:self.closed=True
            process, self.process = self.process, None
            if process:
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
                for pipe in (process.stdin,process.stdout):
                    if pipe:pipe.close()

    def start(self):
        with self.lifecycle:
            if self.closed:raise RuntimeError('Speech worker is closed')
            if self.process and self.process.poll() is None:return
            self.close(permanent=False)
            self.process = subprocess.Popen(
                [str(self.python), '-u', '-m', 'french_notes.speech_worker', '--serve'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.responses = queue.Queue()
            process, responses = self.process, self.responses

        def read():
            try:
                for line in process.stdout:
                    responses.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                responses.put(None)

        threading.Thread(target=read, daemon=True).start()

    def generate(self, request, output):
        self.start()
        try:
            # Keep the pipe payload ASCII-safe even under a legacy Windows
            # locale. JSON restores every accent and Unicode path losslessly.
            with self.lifecycle:
                if self.closed or not self.process:raise RuntimeError('Speech worker is closed')
                process,responses=self.process,self.responses
                process.stdin.write(json.dumps(dict(request, output=str(output)), ensure_ascii=True) + '\n')
                process.stdin.flush()
            response = responses.get(timeout=53)
            if not response or not response.get('ok'):
                raise RuntimeError('Speech generation failed')
        except queue.Empty:
            self.close(permanent=False)
            raise TimeoutError('Speech generation timed out') from None
        except (OSError,ValueError,RuntimeError):
            self.close(permanent=False)
            raise


class SpeechPool:
    def __init__(self, root, python_executable=None):
        # Background preparation occupies at most one worker, leaving a second
        # available for a phrase the user clicks immediately.
        self.workers = [SpeechProcess(root, python_executable) for _ in range(2)]
        self.available = queue.LifoQueue()
        self.closed=threading.Event()
        for worker in self.workers:
            self.available.put(worker)

    def warm(self):
        # Start imports and certificate setup while the app window is opening.
        # Missing speech dependencies must never prevent editing notes.
        for worker in self.workers:
            if self.closed.is_set():return
            try:
                worker.start()
            except (OSError,RuntimeError):
                pass

    def generate(self, request, output):
        deadline=time.monotonic()+55
        while True:
            if self.closed.is_set():raise RuntimeError('Speech pool is closed')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Speech workers are busy')
            try:worker=self.available.get(timeout=min(.1,remaining));break
            except queue.Empty:continue
        try:
            if self.closed.is_set():raise RuntimeError('Speech pool is closed')
            worker.generate(request, output)
        finally:
            if self.closed.is_set():worker.close()
            else:self.available.put(worker)

    def close(self):
        self.closed.set()
        for worker in self.workers:
            worker.close()
