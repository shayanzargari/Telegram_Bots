"""Immutable, provenance-preserving library. No source-writing operations."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

from ..store import CATALOG, decode
from ..editor_store import COMBINED, SECTIONS, section_spans

PARSER_VERSION = 1
DELIMITER = '    |    '


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize(value: str) -> str:
    value = value.casefold().translate(str.maketrans({'ي': 'ی', 'ى': 'ی', 'ك': 'ک', '\u200c': ' ', '’': "'"}))
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value)
                            if not unicodedata.combining(c)).split())


@dataclass(frozen=True)
class Source:
    name: str
    raw: bytes

    @property
    def hash(self):
        return digest(self.raw)


class SourceReader:
    """Only regular top-level note files, opened rb. Never constructs a store."""
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Notes folder is not a directory.')

    def capture(self) -> tuple[Source, ...]:
        def paths():
            found = []
            for p in self.root.iterdir():
                if p.suffix.lower() != '.txt':
                    continue
                if p.is_symlink() or p.resolve().parent != self.root or not p.is_file():
                    raise ValueError('A note path is not a regular file inside the notes folder.')
                # Windows junction/reparse paths are not trusted as sources.
                if getattr(p.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Reparse note paths are not supported.')
                found.append(p)
            order = list(CATALOG)
            return sorted(found, key=lambda p: (order.index(p.name) if p.name in CATALOG else len(order), p.name.casefold()))
        before = paths()
        sources = []
        stamps = []
        for p in before:
            a = p.stat()
            with p.open('rb') as stream:
                raw = stream.read()
            b = p.stat()
            stamp = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino)
            if stamp(a) != stamp(b) or len(raw) != b.st_size:
                raise ValueError('Notes are changing; retrying later.')
            decode(raw)  # Reject incomplete/bad encodings instead of replacement characters.
            if b'\x00' in raw and decode(raw)[1] == 'utf-8':
                raise ValueError('A note contains invalid NUL characters.')
            sources.append(Source(p.name, raw))
            stamps.append(stamp(b))
        after = paths()
        if [p.name for p in before] != [p.name for p in after]:
            raise ValueError('Notes directory changed during reading.')
        if stamps != [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in after]:
            raise ValueError('Notes changed during reading.')
        if not sources:
            raise ValueError('No note files are available.')
        return tuple(sources)


@dataclass(frozen=True)
class Record:
    raw: str
    start: int  # byte offsets in the original file, including its BOM
    end: int
    line: int
    kind: str
    cells: tuple[str, ...] = ()
    secondary: str = ''
    section: str = ''


@dataclass(frozen=True)
class Entry:
    id: str
    document: str
    section: str
    records: tuple[Record, ...]
    heading: Record | None = None

    @property
    def text(self):
        return '\n'.join(r.raw for r in self.records)

    @property
    def title(self):
        for r in self.records:
            if r.raw.strip() and r.kind not in ('blank', 'separator'):
                return (r.cells[0] if r.cells and r.cells[0].strip() else r.raw).strip()
        return 'Notes'

    @property
    def speech(self):
        # Match the desktop's structured French-cell semantics. Never infer a language.
        phrases=[]
        for record in self.records:
            if record.kind=='row':
                cells=record.cells
                phrase=(cells[3].strip() or cells[0].strip()) if len(cells)==6 else cells[0].strip()
                if phrase:phrases.append(phrase)
        return '\n'.join(phrases)

    @property
    def signature(self):
        return self.document, self.section, self.text


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    source: str
    start: int
    end: int
    records: tuple[Record, ...]
    entries: tuple[Entry, ...]
    sections: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    version: str
    sources: tuple[Source, ...]
    documents: tuple[Document, ...]

    @cached_property
    def entries(self):
        return tuple(e for d in self.documents for e in d.entries)

    @cached_property
    def entry_map(self):
        return MappingProxyType({e.id: e for e in self.entries})

    def document(self, key):
        return next((d for d in self.documents if d.id == key), None)

    def entry(self, key):
        return self.entry_map.get(key)

    @cached_property
    def search_index(self):
        return tuple((e, tuple(normalize(c) for r in e.records for c in (r.cells or (r.raw,))), normalize(e.section)) for e in self.entries)

    def manifest(self):
        return {s.name: s.hash for s in self.sources}

    def search(self, query):
        query = normalize(query)
        if not query:
            return ()
        matches = []
        for e, fields, section in self.search_index:
            if any(query in f for f in fields) or query in section:
                rank = 0 if query in fields else 1 if any(f.startswith(query) for f in fields) else 2
                matches.append((rank, e))
        return tuple(e for _, e in sorted(matches, key=lambda pair: pair[0]))

    def dumps(self):
        return json.dumps({'parser': PARSER_VERSION, 'version': self.version,
                           'sources': {s.name: base64.b64encode(s.raw).decode('ascii') for s in self.sources},
                           'ids': [[e.document, e.section, e.text, e.id] for e in self.entries]}, ensure_ascii=False)

    @staticmethod
    def loads(text):
        data = json.loads(text)
        if data['parser'] != PARSER_VERSION:
            raise ValueError('Library parser changed; source review is required.')
        sources = tuple(Source(k, base64.b64decode(v, validate=True)) for k, v in data['sources'].items())
        result = build(sources)
        if result.version != data['version']:
            raise ValueError('Accepted snapshot hash failed.')
        saved = defaultdict(list)
        for document, section, raw, key in data['ids']:
            saved[(document, section, raw)].append(key)
        docs = []
        for d in result.documents:
            entries = []
            for e in d.entries:
                if not saved[e.signature]:
                    raise ValueError('Accepted entry identity is missing.')
                entries.append(replace(e, id=saved[e.signature].pop(0)))
            docs.append(replace(d, entries=tuple(entries)))
        if any(saved.values()) or len({e.id for d in docs for e in d.entries}) != len(result.entries):
            raise ValueError('Accepted entry identities are inconsistent.')
        return replace(result, documents=tuple(docs))


def _records(text, start, full_text, encoding, bom, document):
    records, pos, section = [], start, ''
    first_line = len(full_text[:start].splitlines())
    for i, line in enumerate(text.splitlines(keepends=True)):
        raw = line.rstrip('\r\n')
        cells = tuple(raw.split(DELIMITER))
        kind, secondary = 'prose', ''
        if not raw.strip():
            kind, cells = 'blank', ()
        elif re.fullmatch(r'[-_=\s]{3,}', raw):
            kind, cells = 'separator', ()
        elif raw.startswith(('## ', '### ')):
            kind, cells, section = 'heading', (), raw.lstrip('#').strip()
        elif len(cells) == 3 and (re.match(r'^[1-6]\.\s*(Contraste|Cause|Conséquence|Addition|Condition|Ordre / Temps)$', cells[0]) or cells[0].startswith('Verbes avec ')):
            kind, section = 'heading', cells[0]
        elif re.match(r'^\d+\. (Contrast|Cause|Result|Addition|Condition|Order / Time) — ', raw):
            kind, cells, section = 'heading', (), raw
        elif document == 'Expressions.txt' and len(cells) in (3, 6):
            kind = 'row'
            if len(cells) == 3:
                cells = ('', '', '') + cells
        elif document in ('Indicatif.txt', 'Conditionnel.txt') and len(cells) == 4:
            kind, secondary, cells = 'row', cells[3], cells[:3]
        elif len(cells) == 3:
            kind = 'row'
        elif '\t' in raw:
            kind, cells = 'table', tuple(raw.split('\t'))
        else:
            cells = ()
        byte_start = bom + len(full_text[:pos].encode(encoding))
        byte_end = byte_start + len(line.encode(encoding))
        records.append(Record(raw, byte_start, byte_end, first_line + i + 1, kind, cells, secondary, section))
        pos += len(line)
    return tuple(records)


def _entries(document, records):
    result, pending = [], []

    def flush():
        nonlocal pending
        if any(r.kind not in ('blank', 'separator', 'heading') for r in pending):
            result.append(Entry(uuid.uuid4().hex, document, pending[0].section, tuple(pending)))
        pending = []

    for r in records:
        if document == '@additional':
            if r.kind == 'separator':
                flush()
            else:
                pending.append(r)
        elif r.kind == 'heading':
            flush()
        elif r.kind in ('blank', 'separator'):
            flush()
        elif r.kind == 'row':
            flush()
            result.append(Entry(uuid.uuid4().hex, document, r.section, (r,)))
        else:
            pending.append(r)
    flush()
    contextual=[]
    for entry in result:
        heading=next((r for r in reversed(records) if r.kind=='heading' and r.section==entry.section and r.start<=entry.records[0].start),None)
        contextual.append(replace(entry,heading=heading))
    return tuple(contextual)


def build(sources, previous=None):
    if not sources or len({s.name for s in sources}) != len(sources):
        raise ValueError('Invalid source inventory.')
    documents = []
    for source in sources:
        if Path(source.name).name != source.name or '/' in source.name or '\\' in source.name or ':' in source.name:
            raise ValueError('Invalid source name.')
        text, encoding = decode(source.raw)
        bom = 3 if source.raw.startswith(b'\xef\xbb\xbf') else 2 if source.raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 0
        spans = {source.name: (0, len(text))}
        if source.name == COMBINED:
            try:
                spans = section_spans(text)
            except Exception:
                pass  # Same safe Combined Notes fallback as the desktop.
        for name, (start, end) in spans.items():
            title = SECTIONS[name][0] if name in SECTIONS else 'Combined Notes' if name == COMBINED else CATALOG.get(name, (Path(name).stem,))[0]
            records = _records(text[start:end], start, text, encoding, bom, name)
            sections = tuple(dict.fromkeys(r.section for r in records if r.kind == 'heading'))
            documents.append(Document(name, title, source.name,
                                      bom + len(text[:start].encode(encoding)), bom + len(text[:end].encode(encoding)),
                                      records, _entries(name, records), sections))
    version = digest(json.dumps([PARSER_VERSION, [(s.name, s.hash) for s in sources]], ensure_ascii=False).encode())
    result = Snapshot(version, tuple(sources), tuple(documents))
    if previous:
        old = defaultdict(list)
        counts = Counter(e.signature for e in result.entries)
        old_counts = Counter(e.signature for e in previous.entries)
        for e in previous.entries:
            old[e.signature].append(e)
        docs = []
        for d in result.documents:
            entries = []
            for e in d.entries:
                matches = old[e.signature]
                # Identical snapshots retain duplicate identities; changed snapshots
                # never guess which indistinguishable duplicate survived an edit.
                if matches and ((counts[e.signature] == old_counts[e.signature] == 1) or result.version == previous.version):
                    e = replace(e, id=matches.pop(0).id)
                entries.append(e)
            docs.append(replace(d, entries=tuple(entries)))
        result = replace(result, documents=tuple(docs))
    validate(result)
    return result


def validate(snapshot):
    """Every source span and every material record must be represented exactly."""
    for source in snapshot.sources:
        text, encoding = decode(source.raw)
        docs = [d for d in snapshot.documents if d.source == source.name]
        cursor = 3 if source.raw.startswith(b'\xef\xbb\xbf') else 2 if source.raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 0
        for d in sorted(docs, key=lambda d: d.start):
            gap = source.raw[cursor:d.start].decode(encoding)
            if gap.strip() and not all(not l.strip() or re.fullmatch(r'\s*[-_]{3,}\s*(Words|Verbs)?\s*[-_]*\s*', l, re.I) for l in gap.splitlines()):
                raise ValueError('Unrepresented material outside app view boundaries: ' + source.name)
            pos = d.start
            represented = Counter((r.start, r.end) for e in d.entries for r in e.records)
            for r in d.records:
                if r.start != pos or source.raw[r.start:r.end].decode(encoding).rstrip('\r\n') != r.raw:
                    raise ValueError('Source provenance mismatch.')
                if r.kind not in ('blank', 'separator', 'heading') and represented[(r.start, r.end)] != 1:
                    raise ValueError('A material record is missing or duplicated.')
                pos = r.end
            if pos != d.end:
                raise ValueError('Incomplete document coverage.')
            cursor = d.end
        if cursor != len(source.raw):
            raise ValueError('Unrepresented source tail.')
