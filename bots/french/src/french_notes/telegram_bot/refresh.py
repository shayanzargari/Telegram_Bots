"""Validated candidate snapshots and journal-verified publication."""
from __future__ import annotations

import json
from pathlib import Path
import time

from ..tense_notes import validate_sections
from ..store import decode
from .audit import desktop_state
from .library import SourceReader, Snapshot, build


class LibraryManager:
    def __init__(self, source, state, approved=None, journal=None):
        self.reader = SourceReader(source)
        self.state = state
        self.journal = Path(journal) if journal else desktop_state(source) / 'history.json'
        self.status = 'Saved material'
        self.last_checked = 0
        self.candidate_seen = None
        self.approved = approved
        with state.lock:
            state.db.execute('CREATE TABLE IF NOT EXISTS snapshots(version TEXT PRIMARY KEY,payload TEXT NOT NULL,accepted REAL NOT NULL)')
        self.current = self.load()
        if self.current is None and approved:
            sources = self.reader.capture()
            snapshot = build(sources)
            expected = json.loads(Path(approved).read_text('utf-8'))
            if snapshot.manifest() == expected['sources']:
                self.publish(snapshot)
            else:
                self.status = 'Initial source review required'

    def load(self):
        version = self.state.meta('active_snapshot')
        if not version:
            return None
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM snapshots WHERE version=?', (version,)).fetchone()
        if not row:
            raise ValueError('Accepted library is missing. Restore bot state; do not reset it.')
        return Snapshot.loads(row[0])

    def publish(self, snapshot):
        # Validate/serialize before opening a short SQLite write transaction.
        payload = snapshot.dumps()
        _ = snapshot.search_index
        with self.state.transaction() as db:
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)', (snapshot.version,payload,time.time()))
            db.execute("INSERT OR REPLACE INTO meta VALUES('active_snapshot',?)", (snapshot.version,))
            db.execute('DELETE FROM snapshots WHERE version NOT IN (SELECT version FROM snapshots ORDER BY accepted DESC LIMIT 2)')
        self.current = snapshot
        self.status = 'Saved material'

    def candidate(self):
        sources = self.reader.capture()
        for source in sources:
            validate_sections(source.name, decode(source.raw)[0])
        return build(sources, self.current)

    def journal_verified(self, candidate, journal_bytes):
        try:
            journal = json.loads(journal_bytes)
            if not isinstance(journal, list):
                return False
            if any(r.get('status') in ('pending','undo_pending') for r in journal):
                return False
            old = self.current.manifest() if self.current else {}
            new = candidate.manifest()
            if set(old) != set(new):
                return False  # Additions and removals require explicit local review.
            for name, hashed in new.items():
                if old.get(name) == hashed:
                    continue
                latest = next((r for r in reversed(journal) if r.get('name') == name and r.get('status') == 'committed'), None)
                if not latest or latest.get('after') != hashed:
                    return False
            return True
        except (ValueError,TypeError,AttributeError):
            return False

    def scan(self):
        try:
            # A local reviewer may have published while this process is running.
            accepted = self.state.meta('active_snapshot')
            if accepted and (not self.current or accepted != self.current.version):
                self.current = self.load()
                _ = self.current.search_index
            before = self.journal.read_bytes() if self.journal.exists() else b'[]'
            candidate = self.candidate()
            self.last_checked = time.time()
            if self.current and candidate.version == self.current.version:
                self.status = 'Saved material'
                self.candidate_seen = None
                return False
            if self.candidate_seen != candidate.version:
                self.candidate_seen = candidate.version
                self.status = 'Checking saved changes'
                return False
            after = self.journal.read_bytes() if self.journal.exists() else b'[]'
            if before != after or not self.journal_verified(candidate, after):
                self.status = 'Update awaiting local review'
                return False
            check = self.reader.capture()
            if {s.name:s.hash for s in check} != candidate.manifest() or (self.journal.exists() and self.journal.read_bytes() != after):
                self.status = 'Checking saved changes'
                return False
            self.publish(candidate)
            return True
        except Exception:
            self.status = 'Source unavailable or incomplete; using verified copy'
            return False

    def review(self, confirm=input):
        candidate = self.candidate()
        old = self.current.manifest() if self.current else {}
        new = candidate.manifest()
        print('Current accepted version:', self.current.version if self.current else 'none')
        print('Candidate version:', candidate.version)
        for name in sorted(set(old)|set(new)):
            if old.get(name) != new.get(name):
                label = 'ADDED' if name not in old else 'REMOVED' if name not in new else 'CHANGED'
                print(label, name, (old.get(name) or '-')[:12], '->', (new.get(name) or '-')[:12])
        # The exact full diff is a local report, never sent through Telegram.
        import difflib
        previous_sources = {s.name:decode(s.raw)[0] for s in self.current.sources} if self.current else {}
        current_sources = {s.name:decode(s.raw)[0] for s in candidate.sources}
        diff = ''.join(line for name in sorted(set(previous_sources)|set(current_sources))
                       for line in difflib.unified_diff(previous_sources.get(name,'').splitlines(True),
                                                       current_sources.get(name,'').splitlines(True),
                                                       fromfile='accepted/'+name,tofile='candidate/'+name))
        report = self.state.root / 'source-review.diff'
        report.write_text(diff,'utf-8')
        print('Full UTF-8 diff:', report)
        answer = confirm('After reviewing the diff, type ACCEPT ' + candidate.version[:12] + ': ')
        if answer.strip() != 'ACCEPT ' + candidate.version[:12]:
            print('Not accepted. Existing verified material remains active.')
            return False
        if {s.name:s.hash for s in self.reader.capture()} != candidate.manifest():
            raise ValueError('Source changed during review. Review the new candidate instead.')
        self.publish(candidate)
        print('Accepted:', candidate.version)
        return True
