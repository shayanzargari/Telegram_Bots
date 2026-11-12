"""Editable documents, including three views of the existing words/verbs file."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from .store import NotesError, NotesStore, atomic_json, decode, exclusive_file, sha
from .tense_notes import TENSE_SECTIONS, validate_sections


COMBINED = 'wrods, verbs.txt'
SECTIONS = {
    '@words': ('Words', 'letters'),
    '@verbs': ('Verbs', 'pen'),
    '@additional': ('Additional Notes', 'notebook'),
}
WORDS_HEADER = re.compile(r'(?im)^[ \t]*-{3,}[ \t]*Words[ \t]*-{3,}[ \t]*(?:\r\n|\n|\r|$)')
VERBS_HEADER = re.compile(r'(?im)^[ \t]*-{3,}[ \t]*Verbs[ \t]*-{3,}[ \t]*(?:\r\n|\n|\r|$)')
SEPARATOR = re.compile(r'(?m)^[ \t]*[-_]{10,}[ \t]*(?:\r\n|\n|\r|$)')


def normalized(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def section_spans(text):
    words, verbs = WORDS_HEADER.search(text), VERBS_HEADER.search(text)
    details = SEPARATOR.search(text, verbs.end()) if verbs else None
    if not words or not verbs or not details or words.end() > verbs.start():
        raise NotesError('The Words, Verbs, and Additional Notes boundaries could not be found. Open the combined file from the sidebar.', 409, 'structure')
    return {'@words': (words.end(), verbs.start()),
            '@verbs': (verbs.end(), details.start()),
            '@additional': (details.end(), len(text))}


class EditorStore(NotesStore):
    def __init__(self, root, state):
        super().__init__(root, state)
        self.drafts_path = self.state / 'editor-drafts.json'
        self.drafts = json.loads(self.drafts_path.read_text('utf-8')) if self.drafts_path.exists() else {}

    def source_name(self, name):
        return COMBINED if name in SECTIONS else name

    def document_metadata(self, name):
        if name in SECTIONS:
            title, icon = SECTIONS[name]
            return dict(name=name, title=title, icon=icon, source_name=COMBINED, section=True)
        result = self.metadata(name)
        result['title'] = {COMBINED: 'Combined Notes'}.get(name, result['title'])
        return dict(name=name, title=result['title'], icon=result['icon'], source_name=name, section=False)

    def unpack(self, name, raw):
        text, encoding = decode(raw)
        start, end = section_spans(text)[name] if name in SECTIONS else (0, len(text))
        bom = 3 if raw.startswith(b'\xef\xbb\xbf') else 2 if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 0
        byte_start = bom + len(text[:start].encode(encoding))
        byte_end = bom + len(text[:end].encode(encoding))
        return text[start:end], byte_start, byte_end, encoding

    def categories(self):
        with self.lock:
            base = super().categories()
            entries = []
            for row in base['categories']:
                names = [row['name']]
                if row['name'] == COMBINED:
                    try:
                        section_spans(decode(self.path(COMBINED).read_bytes())[0])
                        names = list(SECTIONS)
                    except (NotesError, UnicodeError):
                        pass
                entries.extend(self.document_metadata(name) for name in names)
            return dict(folder=base['folder'], categories=entries)

    def latest_edit(self, name):
        undone = {r.get('undo_of') for r in self.journal if r.get('kind') == 'restore' and r['status'] == 'committed'}
        return next((r for r in reversed(self.journal) if r.get('document') == name and r.get('kind') == 'edit'
                     and r['status'] == 'committed' and r['id'] not in undone), None)

    def read_document(self, name):
        with self.lock:
            raw = self.path(self.source_name(name)).read_bytes()
            text, *_ = self.unpack(name, raw)
            section_revision = sha(text.encode('utf-8'))
            latest = self.latest_edit(name)
            can_undo = latest and latest['section_after'] == section_revision
            if name not in SECTIONS:
                can_undo = can_undo and latest['after'] == sha(raw)
            return dict(**self.document_metadata(name), text=normalized(text), revision=sha(raw),
                        section_revision=section_revision, undo_id=latest['id'] if can_undo else None)

    def search(self, query):
        query = str(query).casefold().strip()
        if not query:
            return dict(results=[], total=0)
        results, total = [], 0
        for item in self.categories()['categories']:
            try:
                doc = self.read_document(item['name'])
            except (NotesError, OSError, UnicodeError):
                continue
            for line_number, line in enumerate(doc['text'].split('\n')):
                if query in line.casefold():
                    total += 1
                    if len(results) < 60:
                        results.append(dict(name=item['name'], title=item['title'], line=line_number, text=line))
        return dict(results=results, total=total)

    def read_drafts(self):
        with self.lock:
            return dict(drafts=dict(self.drafts))

    def write_draft(self, name, draft):
        with self.lock:
            self.path(self.source_name(name))
            if draft is None:
                self.drafts.pop(name, None)
            else:
                if not isinstance(draft, dict) or not isinstance(draft.get('text'), str) or len(draft['text'].encode('utf-8')) > 1_000_000:
                    raise NotesError('The draft is too large or could not be read.')
                if not all(isinstance(draft.get(k), str) and re.fullmatch(r'[a-f0-9]{64}', draft[k]) for k in ('revision', 'section_revision')):
                    raise NotesError('Refresh this note before editing it.')
                self.drafts[name] = {k: draft[k] for k in ('text', 'revision', 'section_revision')}
            atomic_json(self.drafts_path, self.drafts)
            return dict(ok=True)

    def verify_revision(self, name, raw, revision, section_revision):
        text, *_ = self.unpack(name, raw)
        if (name in SECTIONS and sha(text.encode('utf-8')) != section_revision) or (name not in SECTIONS and sha(raw) != revision):
            raise NotesError('This note changed outside the editor. Your draft is kept. Use Refresh to review the current file before saving.', 409, 'changed')

    def replace_section(self, name, raw, edited):
        current, start, end, encoding = self.unpack(name, raw)
        whole, _ = decode(raw)
        newline = '\r\n' if '\r\n' in whole else '\r' if '\r' in whole and '\n' not in whole else '\n'
        replacement = normalized(edited).replace('\n', newline)
        if name == '@words' and VERBS_HEADER.search(replacement):
            raise NotesError('Use the Verbs tab for a separate Verbs heading.')
        if name == '@verbs' and SEPARATOR.search(replacement):
            raise NotesError('Put sections separated by long dashed lines in Additional Notes.')
        if name in ('@words', '@verbs') and replacement and not replacement.endswith(('\r', '\n')):
            replacement += newline
        return raw[:start] + replacement.encode(encoding) + raw[end:]

    def commit(self, stream, name, before, after, request_id, fingerprint, kind='edit', undo_of=None):
        operation_id = str(uuid.uuid4())
        backup = self.backups / (operation_id + '.bin')
        with backup.open('xb') as saved:
            saved.write(before)
            saved.flush()
            os.fsync(saved.fileno())
        operation = dict(id=operation_id, request_id=request_id, name=self.source_name(name), document=name,
                         kind=kind, undo_of=undo_of, fingerprint=fingerprint, before=sha(before), after=sha(after),
                         section_before=sha(self.unpack(name, before)[0].encode('utf-8')),
                         section_after=sha(self.unpack(name, after)[0].encode('utf-8')),
                         status='pending', created=datetime.now(timezone.utc).isoformat())
        self.journal.append(operation)
        try:
            self.save_journal()
        except OSError:
            self.journal.remove(operation)
            raise
        try:
            stream.seek(0)
            stream.write(after)
            stream.truncate()
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

    def save_document(self, name, text, revision, section_revision, request_id):
        with self.lock:
            if not isinstance(text, str) or '\x00' in text or len(text.encode('utf-8')) > 1_000_000:
                raise NotesError('Use plain text up to 1 MB.')
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
                raise NotesError('Please try saving again.')
            validate_sections(name, text)
            fingerprint = sha((name + '\0' + text).encode('utf-8'))
            previous = next((r for r in self.journal if r.get('request_id') == request_id), None)
            if previous:
                if previous['status'] == 'committed' and previous['fingerprint'] == fingerprint:
                    return dict(file=self.read_document(name), unchanged=False)
                raise NotesError('That save was already handled. Refresh before trying again.', 409)
            with exclusive_file(self.path(self.source_name(name))) as stream:
                before = stream.read()
                self.verify_revision(name, before, revision, section_revision)
                old_text, *_ = self.unpack(name, before)
                if normalized(old_text) == normalized(text):
                    unchanged = True
                else:
                    after = self.replace_section(name, before, text)
                    self.commit(stream, name, before, after, request_id, fingerprint)
                    unchanged = False
            return dict(file=self.read_document(name), unchanged=unchanged)

    def undo_document(self, name, revision, section_revision, operation_id):
        with self.lock:
            operation = self.latest_edit(name)
            if not operation or operation['id'] != operation_id:
                raise NotesError('That save can no longer be undone. Refresh the note.', 409, 'changed')
            original = (self.backups / (operation['id'] + '.bin')).read_bytes()
            if sha(original) != operation['before']:
                raise NotesError('The saved backup could not be verified.', 409)
            with exclusive_file(self.path(self.source_name(name))) as stream:
                before = stream.read()
                self.verify_revision(name, before, revision, section_revision)
                current, start, end, encoding = self.unpack(name, before)
                if sha(current.encode('utf-8')) != operation['section_after']:
                    raise NotesError('This note changed since that save. Refresh to keep the newer changes safe.', 409, 'changed')
                if name in SECTIONS:
                    old_text, old_start, old_end, old_encoding = self.unpack(name, original)
                    restored = original[old_start:old_end] if encoding == old_encoding else old_text.encode(encoding)
                    after = before[:start] + restored + before[end:]
                else:
                    if sha(before) != operation['after']:
                        raise NotesError('This file changed since that save. Refresh before undoing.', 409, 'changed')
                    after = original
                self.commit(stream, name, before, after, str(uuid.uuid4()), '', kind='restore', undo_of=operation_id)
            return dict(file=self.read_document(name))
