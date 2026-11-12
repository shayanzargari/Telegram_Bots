"""Local notes server and Windows app-window launcher with neural speech."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import NotesError, atomic_json
from .editor_store import EditorStore as NotesStore
from .speech import DEFAULT_VOICE, FrenchSpeech

ROOT = Path(__file__).resolve().parent


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store):
        super().__init__(address, Handler)
        self.store = store
        self.speech = FrenchSpeech(store.state, ROOT)
        self.speech.worker.warm()
        self.token = secrets.token_urlsafe(32)
        self.last_seen = time.monotonic()
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.browser_state_lock = threading.RLock()

    def read_browser_state(self):
        path=self.store.state/'browser-state.json'
        with self.browser_state_lock:
            return json.loads(path.read_text('utf-8')) if path.exists() else {'schema':1,'source_folder':str(self.store.root),'values':{}}

    def save_browser_state(self,values):
        if not isinstance(values,dict) or len(values)>128:
            raise NotesError('Browser preferences could not be read.')
        if any(not isinstance(key,str) or not key.startswith('french-notes.') or len(key)>2048 or not isinstance(value,str) or len(value)>600000 for key,value in values.items()):
            raise NotesError('Browser preferences could not be read.')
        with self.browser_state_lock:
            current=self.read_browser_state();merged=dict(current.get('values',{}));merged.update(values)
            result={'schema':1,'source_folder':str(self.store.root),'values':merged}
            atomic_json(self.store.state/'browser-state.json',result)
        return {'saved':True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, content_type='application/json; charset=utf-8'):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if isinstance(body, (dict, list)) else body
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def check(self, api=False):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            raise NotesError('Invalid app address.', 403)
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            raise NotesError('Open French Notes from its shortcut.', 403)
        if api and not secrets.compare_digest(self.headers.get('X-Notes-Token', ''), self.server.token):
            raise NotesError('Please reopen French Notes from its shortcut.', 403)

    def do_GET(self):
        try:
            self.check()
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path == '/health':
                return self.reply(200, dict(app='French Notes', folder=str(self.server.store.root)))
            if url.path.startswith('/api/'):
                self.check(api=True)
                if url.path == '/api/ping':
                    self.server.last_seen = time.monotonic()
                    return self.reply(200, dict(ok=True))
                if url.path == '/api/categories':
                    return self.reply(200, self.server.store.categories())
                if url.path == '/api/file':
                    return self.reply(200, self.server.store.read(query.get('name', [''])[0]))
                if url.path == '/api/document':
                    return self.reply(200, self.server.store.read_document(query.get('name', [''])[0]))
                if url.path == '/api/drafts':
                    return self.reply(200, self.server.store.read_drafts())
                if url.path == '/api/browser-state':
                    return self.reply(200, self.server.read_browser_state())
                if url.path == '/api/search':
                    return self.reply(200, self.server.store.search(query.get('q', [''])[0][:200]))
                raise NotesError('Not found.', 404)
            files = {'/': ('editor.html', 'text/html; charset=utf-8'),
                     '/columns.js': ('columns.js', 'text/javascript; charset=utf-8'),
                     '/speech.js': ('speech.js', 'text/javascript; charset=utf-8'),
                     '/editor.js': ('editor.js', 'text/javascript; charset=utf-8'),
                     '/editor.css': ('editor.css', 'text/css; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8'),
                     '/icon.svg': ('icon.svg', 'image/svg+xml')}
            if url.path not in files:
                raise NotesError('Not found.', 404)
            filename, mime = files[url.path]
            data = (ROOT / 'ui' / filename).read_bytes()
            if filename in ('index.html', 'editor.html'):
                data = data.replace(b'__APP_TOKEN__', self.server.token.encode('ascii'))
            self.server.last_seen = time.monotonic()
            self.reply(200, data, mime)
        except NotesError as error:
            self.reply(error.status, dict(error=str(error), code=error.code))
        except (OSError, UnicodeError):
            logging.exception('Read failed')
            self.reply(500, dict(error='Could not read your notes. Check that the folder is available.'))

    def do_POST(self):
        try:
            self.check(api=True)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1_100_000:
                raise NotesError('Paste up to 1 MB of text at a time.', 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise NotesError('Invalid request.')
            if self.path == '/api/speech':
                audio = self.server.speech.synthesize(data.get('text'), data.get('voice', DEFAULT_VOICE), data.get('speed', '1'))
                return self.reply(200, audio, 'audio/mpeg')
            if self.path == '/api/browser-state':
                return self.reply(200,self.server.save_browser_state(data.get('values')))
            name, revision = data.get('name'), data.get('revision')
            if self.path == '/api/save':
                result = self.server.store.save_document(name, data.get('text'), revision, data.get('section_revision'), data.get('request_id'))
            elif self.path == '/api/undo-save':
                result = self.server.store.undo_document(name, revision, data.get('section_revision'), data.get('operation_id'))
            elif self.path == '/api/draft':
                result = self.server.store.write_draft(name, data.get('draft'))
            elif self.path == '/api/preview':
                result = self.server.store.preview(name, data.get('text'), revision)
            elif self.path == '/api/append':
                result = self.server.store.append(name, data.get('text'), revision, data.get('request_id'), data.get('allow_duplicates') is True)
            elif self.path == '/api/undo':
                result = self.server.store.undo(name, revision, data.get('operation_id'))
            else:
                raise NotesError('Not found.', 404)
            self.reply(200, result)
        except NotesError as error:
            self.reply(error.status, dict(error=str(error), code=error.code))
        except (ValueError, TypeError):
            self.reply(400, dict(error='The request could not be read. Please try again.'))
        except OSError:
            logging.exception('Write failed')
            self.reply(500, dict(error='Could not finish saving. Your draft is still here. Refresh to check the file before trying again.'))


def browser_window(url, state):
    candidates = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe']
    browser = next((p for p in candidates if p.exists()), None)
    if browser:
        subprocess.Popen([str(browser), '--app=' + url, '--user-data-dir=' + str(state / 'window-profile'),
                          '--no-first-run', '--no-default-browser-check', '--disable-background-mode',
                          '--window-size=1420,960', '--window-position=90,40'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import webbrowser
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', help='Canonical Bot Control Center installation root')
    parser.add_argument('--data-dir', help='Explicit isolated notes folder; production uses canonical configuration')
    parser.add_argument('--state-dir')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--idle-seconds', type=int, default=150)
    args = parser.parse_args()
    installation = None
    if args.data_dir is None or args.root:
        from bot_center_runtime.paths import Installation
        installation=Installation(args.root)
        manifest=installation.require_state('french')
        args.data_dir=manifest['notes_dir']
        if args.state_dir and Path(args.state_dir).resolve()!=Path(manifest['editor_state_dir']).resolve():
            raise RuntimeError('Canonical notes require the preserved canonical editor state; use copied notes for an isolated editor.')
        if args.state_dir is None:args.state_dir=manifest['editor_state_dir']
    folder = Path(args.data_dir).resolve(strict=True)
    if installation is None:
        from bot_center_runtime.paths import Installation,ConfigurationError
        try:possible=Installation();declared=possible.bot('french')
        except (ConfigurationError,OSError):possible=None
        if possible and folder==Path(declared['notes_dir']).resolve():
            manifest=possible.require_state('french');installation=possible
            if args.state_dir and Path(args.state_dir).resolve()!=Path(manifest['editor_state_dir']).resolve():
                raise RuntimeError('Canonical notes require the preserved canonical editor state; use copied notes for an isolated editor.')
            args.state_dir=manifest['editor_state_dir']
    folder_key = hashlib.sha256(str(folder).casefold().encode()).hexdigest()[:12]
    state = Path(args.state_dir) if args.state_dir else Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'French Notes' / folder_key
    if installation:
        if not state.is_dir() or any(not (state/name).is_file() for name in manifest.get('required_editor_state',[])):
            raise RuntimeError('Expected French editor state is missing; restore the verified set before opening it.')
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=state / 'app.log', level=logging.WARNING, encoding='utf-8')
    # Keep a single writer per notes folder; release automatically when the process exits.
    instance_handle = None
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        instance_handle = kernel.CreateMutexW(None, True, 'Local\\FrenchNotes-' + folder_key)
        if kernel.GetLastError() == 183:
            for _ in range(25):
                try:
                    saved = json.loads((state / 'server.json').read_text('utf-8'))
                    url = saved['url']
                    if not url.startswith('http://127.0.0.1:') or urllib.parse.urlsplit(url).path not in ('', '/'):
                        raise ValueError('Invalid local address')
                    local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with local_http.open(url + '/health', timeout=1) as response:
                        health = json.load(response)
                    if health.get('app') == 'French Notes' and health.get('folder') == str(folder):
                        if not args.no_browser:browser_window(url, state)
                        return
                except (OSError, ValueError, KeyError):
                    time.sleep(.2)
            raise RuntimeError('French Notes is already opening. Please try its shortcut again in a moment.')
    # This shared lease lives with the independent editor, outside any reader
    # or core Job Object. Capture/recovery must wait for the editor to close.
    maintenance_lease=None
    if installation:
        from bot_center_runtime.locks import FileLease
        maintenance_lease=FileLease(installation.root/'data/maintenance.lock')
        maintenance_lease.__enter__()
    store = NotesStore(folder, state)
    port = args.port
    if not port and not args.no_browser and (state / 'server.json').exists():
        try:
            saved_origin = json.loads((state / 'server.json').read_text('utf-8'))['url']
            port = urllib.parse.urlsplit(saved_origin).port or 0
        except (ValueError, KeyError):
            pass
    try:
        server = Server(('127.0.0.1', port), store)
    except OSError:
        if args.port:
            raise
        server = Server(('127.0.0.1', 0), store)
    atomic_json(state / 'server.json', dict(url=server.origin, pid=os.getpid(), folder=str(folder)))
    if not args.no_browser:
        browser_window(server.origin, state)
    if sys.stdout:
        print(server.origin, flush=True)

    def watch_idle():
        while True:
            time.sleep(5)
            if args.idle_seconds and time.monotonic() - server.last_seen > args.idle_seconds:
                server.shutdown()
                return

    threading.Thread(target=watch_idle, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        server.speech.close()
        if maintenance_lease:maintenance_lease.__exit__(None,None,None)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if sys.stderr:
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(error), 'French Notes could not open', 0x10)
