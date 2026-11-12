"""Plain-text notes with explicit append, byte-exact backups and guarded undo."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path


CATALOG = {
    'Indicatif.txt': ('Indicatif', 'Grammar & usage', 'Examples arranged by tense.', 'notebook'),
    'Conditionnel.txt': ('Conditionnel', 'Grammar & usage', 'Conditional examples and formulas.', 'flower'),
    'wrods, verbs.txt': ('Words & Verbs', 'Everyday French', 'Build your vocabulary, one discovery at a time.', 'letters'),
    'Expressions.txt': ('Expressions', 'Everyday French', 'The little phrases that make French feel natural.', 'spark'),
    'Questions.txt': ('Questions', 'Everyday French', 'Find the right words to ask.', 'question'),
    'Directions.txt': ('Directions', 'Everyday French', 'Words to help you find your way.', 'compass'),
    'COD-COI.txt': ('COD / COI', 'Grammar & usage', 'Direct and indirect objects, made clearer.', 'link'),
    'Faire.txt': ('Faire', 'Grammar & usage', 'One useful verb, so many possibilities.', 'pen'),
    'Avoir.txt': ('Avoir', 'Grammar & usage', 'Expressions with avoir and their examples.', 'pen'),
    'phrases impératives.txt': ('Impératif', 'Grammar & usage', 'Instructions, invitations, and encouragement.', 'arrow'),
    'Subjonctif.txt': ('Subjonctif', 'Grammar & usage', 'Wishes, possibilities, and what matters.', 'flower'),
    'Gérondif.txt': ('Gérondif', 'Grammar & usage', 'Connect actions with en + participe présent.', 'flow'),
    'Adverbs.txt': ('Adverbs', 'Grammar & usage', 'Add a little more detail to your French.', 'sliders'),
    'Les connecteurs logiques.txt': ('Connectors', 'Grammar & usage', 'Bring your ideas together.', 'connect'),
}


class NotesError(Exception):
    def __init__(self, message, status=400, code='invalid'):
        super().__init__(message)
        self.status, self.code = status, code


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    if raw.startswith(b'\xff\xfe'):
        return raw[2:].decode('utf-16-le'), 'utf-16-le'
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be'), 'utf-16-be'
    try:
        return raw.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError as exc:
        raise NotesError('This file is not UTF-8 or UTF-16. Convert a copy before using it here.') from exc


def identity(text):
    text = unicodedata.normalize('NFC', text).casefold()
    return ' '.join(re.findall(r'\w+', text, flags=re.UNICODE))


def replace_with_retry(source, target):
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(.01 * 2 ** attempt)


def atomic_json(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def exclusive_file(path):
    """On Windows deny other writers/deletes while checking and changing the file."""
    if os.name == 'nt':
        import msvcrt
        from ctypes import wintypes
        create = ctypes.windll.kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0x80000000 | 0x40000000, 1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise NotesError('This file is busy in another app. Close its editor, then try again.', 409, 'busy')
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        with os.fdopen(fd, 'r+b') as stream:
            yield stream
    else:
        import fcntl
        with path.open('r+b') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield stream


class NotesStore:
    def __init__(self, root, state):
        self.root = Path(root).resolve(strict=True)
        self.state = Path(state).resolve()
        if self.state == self.root or self.root in self.state.parents:
            raise ValueError('Application state must be outside the notes folder.')
        self.state.mkdir(parents=True, exist_ok=True)
        self.backups = self.state / 'backups'
        self.backups.mkdir(exist_ok=True)
        self.journal_path = self.state / 'history.json'
        self.lock = threading.RLock()
        self.journal = json.loads(self.journal_path.read_text('utf-8')) if self.journal_path.exists() else []
        changed = False
        for item in self.journal:
            if item['status'] not in ('pending', 'undo_pending'):
                continue
            try:
                current = sha(self.path(item['name']).read_bytes())
                undoing = item['status'] == 'undo_pending'
                if current == item['after']:
                    item['status'] = 'committed'
                elif current == item['before']:
                    item['status'] = 'undone' if undoing else 'cancelled'
                else:
                    item['status'] = 'interrupted'
                changed = True
            except (OSError, NotesError):
                pass
        if changed:
            self.save_journal()

    def path(self, name):
        if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name or ':' in name:
            raise NotesError('Choose a text file from your categories.', 400, 'path')
        p = self.root / name
        if p.suffix.lower() != '.txt' or p.is_symlink() or not p.is_file() or p.resolve().parent != self.root:
            raise NotesError('That category is no longer available. Refresh your notes.', 404, 'missing')
        return p

    def metadata(self, name):
        title, group, description, icon = CATALOG.get(name, (Path(name).stem, 'More notes', 'A space for your French discoveries.', 'notebook'))
        return dict(name=name, title=title, group=group, description=description, icon=icon)

    def categories(self):
        with self.lock:
            names = [p.name for p in self.root.glob('*.txt') if p.is_file() and not p.is_symlink() and p.resolve().parent == self.root]
            order = list(CATALOG)
            names.sort(key=lambda n: (order.index(n) if n in CATALOG else len(order), n.casefold()))
            result = []
            for name in names:
                row = self.metadata(name)
                try:
                    text, _ = decode(self.path(name).read_bytes())
                    row['lines'] = sum(bool(s.strip()) and not re.fullmatch(r'[-_=\s]+', s) for s in text.splitlines())
                except (OSError, NotesError, UnicodeError):
                    row['lines'] = None
                result.append(row)
            return dict(folder=str(self.root), categories=result)

    def latest(self, name):
        return next((r for r in reversed(self.journal) if r['name'] == name and r['status'] == 'committed'), None)

    def read(self, name):
        with self.lock:
            raw = self.path(name).read_bytes()
            text, _ = decode(raw)
            revision = sha(raw)
            latest = self.latest(name)
            return dict(**self.metadata(name), text=text, revision=revision,
                        undo_id=latest['id'] if latest and latest['after'] == revision else None,
                        modified=self.path(name).stat().st_mtime)

    def search(self, query):
        query = unicodedata.normalize('NFC', str(query)).casefold().strip()
        if not query:
            return dict(results=[], total=0)
        results, total = [], 0
        for entry in self.categories()['categories']:
            try:
                lines = self.read(entry['name'])['text'].splitlines()
            except (NotesError, OSError, UnicodeError):
                continue
            for index, line in enumerate(lines):
                if query in unicodedata.normalize('NFC', line).casefold():
                    total += 1
                    if len(results) < 60:
                        results.append(dict(name=entry['name'], title=entry['title'], line=index, text=line))
        return dict(results=results, total=total)

    def preview(self, name, text, revision):
        if not isinstance(text, str) or not text.strip():
            raise NotesError('Paste some text before adding it.')
        if len(text.encode('utf-8')) > 1_000_000 or '\x00' in text:
            raise NotesError('Paste plain text up to 1 MB at a time.')
        current = self.read(name)
        if current['revision'] != revision:
            raise NotesError('This file changed outside the app. Refresh it, then add your text again. Your draft is safe.', 409, 'changed')
        existing = {identity(s) for s in current['text'].splitlines() if identity(s)}
        pasted = set()
        duplicates = []
        for number, line in enumerate(text.splitlines(), 1):
            key = identity(line)
            if key and (key in existing or key in pasted):
                duplicates.append(dict(line=number, text=line, where='file' if key in existing else 'paste'))
            if key:
                pasted.add(key)
        return dict(duplicates=duplicates, lines=sum(bool(s.strip()) for s in text.splitlines()), revision=revision)

    def save_journal(self):
        atomic_json(self.journal_path, self.journal)

    def append(self, name, text, revision, request_id, allow_duplicates=False):
        with self.lock:
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
                raise NotesError('Please try adding your text again.')
            fingerprint = sha((name + '\0' + text).encode('utf-8')) if isinstance(text, str) else ''
            previous = next((r for r in self.journal if r['id'] == request_id), None)
            if previous:
                if previous.get('fingerprint') == fingerprint and previous['status'] == 'committed':
                    return dict(file=self.read(name), added_lines=previous['lines'], already_added=True)
                raise NotesError('That addition was already handled. Refresh before trying again.', 409)
            preview = self.preview(name, text, revision)
            if preview['duplicates'] and not allow_duplicates:
                raise NotesError('Some pasted lines already exist. Review them before adding.', 409, 'duplicates')
            path = self.path(name)
            with exclusive_file(path) as stream:
                before = stream.read()
                if sha(before) != revision:
                    raise NotesError('This file just changed. Refresh it before adding. Your draft is safe.', 409, 'changed')
                original, encoding = decode(before)
                newline = '\r\n' if '\r\n' in original else ('\r' if '\r' in original and '\n' not in original else '\n')
                normalized = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', newline)
                separator = '' if not original or original.endswith(('\r', '\n')) else newline
                addition = separator + normalized + ('' if normalized.endswith(('\r', '\n')) else newline)
                after = before + addition.encode(encoding)
                backup = self.backups / (request_id + '.bin')
                with backup.open('xb') as saved:
                    saved.write(before)
                    saved.flush()
                    os.fsync(saved.fileno())
                operation = dict(id=request_id, name=name, before=sha(before), after=sha(after),
                                 fingerprint=fingerprint, lines=preview['lines'], status='pending',
                                 created=datetime.now(timezone.utc).isoformat())
                self.journal.append(operation)
                self.save_journal()
                try:
                    stream.seek(0, 2)
                    stream.write(addition.encode(encoding))
                    stream.flush()
                    os.fsync(stream.fileno())
                except OSError:
                    stream.seek(0)
                    stream.write(before)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    operation['status'] = 'cancelled'
                    self.save_journal()
                    raise
                operation['status'] = 'committed'
                self.save_journal()
            return dict(file=self.read(name), added_lines=preview['lines'], already_added=False)

    def undo(self, name, revision, operation_id):
        with self.lock:
            operation = self.latest(name)
            if not operation or operation['id'] != operation_id or operation['after'] != revision:
                raise NotesError('This addition can no longer be undone safely. Refresh your notes.', 409, 'changed')
            original = (self.backups / (operation['id'] + '.bin')).read_bytes()
            if sha(original) != operation['before']:
                raise NotesError('The backup could not be verified. Your notes have not been changed.', 409)
            with exclusive_file(self.path(name)) as stream:
                after = stream.read()
                if sha(after) != operation['after']:
                    raise NotesError('This file changed after the addition. Undo is paused to keep those changes safe.', 409, 'changed')
                operation['status'] = 'undo_pending'
                self.save_journal()
                try:
                    stream.seek(0)
                    stream.write(original)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                except OSError:
                    stream.seek(0)
                    stream.write(after)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    operation['status'] = 'committed'
                    self.save_journal()
                    raise
                operation['status'] = 'undone'
                self.save_journal()
            return dict(file=self.read(name))
