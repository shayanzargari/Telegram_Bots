"""Independent source/desktop parity audit and protected-byte baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from .library import SourceReader, build, digest

ROOT = Path(__file__).resolve().parents[1]


def desktop_state(source):
    # Canonical configuration explicitly preserves the original editor history
    # namespace across source folder relocation. Isolated fixtures keep their
    # own deterministic historical behavior.
    try:
        from bot_center_runtime.paths import Installation
        manifest=Installation().bot('french')
        if Path(source).resolve()==Path(manifest['notes_dir']).resolve():
            return Path(manifest['editor_state_dir'])
    except (RuntimeError,OSError,ValueError,KeyError):pass
    key = hashlib.sha256(str(Path(source).resolve()).casefold().encode()).hexdigest()[:12]
    return Path(os.environ.get('LOCALAPPDATA', ROOT)) / 'French Notes' / key


def protect(source, work):
    """Copy protected bytes; never initialize or change live desktop state."""
    source = Path(source).resolve()
    state = desktop_state(source)
    selected = [('notes', source)]
    files = [(label, root, p) for label, root in selected for p in root.rglob('*') if p.is_file() and not p.is_symlink()]
    if state.exists():
        for name in ('history.json', 'editor-drafts.json', 'backups', 'window-profile/Default/Local Storage'):
            p = state / name
            files.extend(('desktop', state, q) for q in ([p] if p.is_file() else p.rglob('*') if p.exists() else []) if q.is_file() and not q.is_symlink())
    manifest, skipped = [], []
    for label, root, p in files:
        try:
            data = p.read_bytes()
            target = work / 'protected' / label / p.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest.append(dict(path=str(p), sha256=digest(data), bytes=len(data), copy=str(target.relative_to(work))))
        except PermissionError:
            skipped.append(str(p))
    report = dict(files=manifest, locked_files_not_copied=skipped)
    (work / 'protected-before.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    return report


def integrity(work):
    before = json.loads((work / 'protected-before.json').read_text('utf-8'))
    changed, missing = [], []
    for row in before['files']:
        p = Path(row['path'])
        if not p.exists():
            missing.append(row['path'])
        elif digest(p.read_bytes()) != row['sha256']:
            changed.append(row['path'])
    report = dict(checked=len(before['files']), changed=changed, missing=missing,
                  locked_files_not_copied=before['locked_files_not_copied'])
    (work / 'protected-after.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    return report


def parity(snapshot, work):
    """Use the existing Python store AND original JS parser as separate oracles."""
    from ..editor_store import EditorStore
    notes, state = work / 'oracle-notes', work / 'oracle-state'
    notes.mkdir(parents=True, exist_ok=True)
    for s in snapshot.sources:
        (notes / s.name).write_bytes(s.raw)
    store = EditorStore(notes, state)  # Only isolated copies; never the live root.
    actual = store.categories()['categories']
    differences = dict(missing=[], duplicated=[], reordered=[], altered=[])
    if [r['name'] for r in actual] != [d.id for d in snapshot.documents]:
        differences['reordered'].append('Desktop category order differs.')
    payload = []
    for d in snapshot.documents:
        doc = store.read_document(d.id)
        if doc['title'] != d.title:
            differences['altered'].append(d.id + ': title')
        raw = ''.join(snapshot.sources[[s.name for s in snapshot.sources].index(d.source)].raw[r.start:r.end].decode(
            'utf-16-le' if snapshot.sources[[s.name for s in snapshot.sources].index(d.source)].raw.startswith(b'\xff\xfe') else
            'utf-16-be' if snapshot.sources[[s.name for s in snapshot.sources].index(d.source)].raw.startswith(b'\xfe\xff') else 'utf-8') for r in d.records)
        if raw.replace('\r\n', '\n').replace('\r', '\n') != doc['text']:
            differences['altered'].append(d.id + ': complete view text')
        payload.append(dict(id=d.id, text=doc['text']))
    script = r"""
const fs=require('fs'),vm=require('vm');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync('ui/columns.js','utf8')+';globalThis.C=NotesColumns;',context);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const output=input.map(d=>{const c=new context.C({}, {}, {}, {});c.documentKey=d.id;
return {id:d.id,records:c.parse(d.text).map(r=>({parts:r.parts,fragments:r.fragments,heading:r.heading,section:r.sectionHeader,secondary:r.secondary,text:r.text}))};});
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run(['node', '-e', script], input=json.dumps(payload), cwd=ROOT,
                            encoding='utf-8', capture_output=True, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    oracle = json.loads(result.stdout)
    presentation = []
    for d, reference in zip(snapshot.documents, oracle):
        rows = reference['records']
        if len(rows) == len(d.records) + 1 and not any(rows[-1].values()):
            rows = rows[:-1]
        if len(rows) != len(d.records):
            differences['missing'].append(d.id + ': line count')
            continue
        for r, expected in zip(d.records, rows):
            location = f'{d.id}:{r.line}'
            if r.kind == 'row' or (r.kind == 'heading' and r.cells):
                if list(r.cells) != expected.get('parts') or (r.secondary or '') != (expected.get('secondary') or ''):
                    differences['altered'].append(location + ': language fields')
            elif expected.get('fragments') is not None:
                displayed = [re.sub('مفر\s+د', 'مفرد', c.strip()) for c in r.cells]
                if displayed != expected['fragments']:
                    differences['altered'].append(location + ': table cells')
                if list(r.cells) != displayed:
                    presentation.append(location + ': desktop trims table padding / display spelling; bot retains source')
            elif expected.get('parts') is not None and r.kind == 'heading':
                presentation.append(location + ': desktop translates a source heading; bot retains original heading')
            elif expected.get('text', r.raw) != r.raw:
                differences['altered'].append(location + ': source text')
    report = dict(source_files=len(snapshot.sources), app_views=len(snapshot.documents), entries=len(snapshot.entries),
                  records=sum(len(d.records) for d in snapshot.documents), version=snapshot.version,
                  differences=differences, explained_presentation= presentation,
                  views=[dict(id=d.id,title=d.title,entries=len(d.entries),sections=list(d.sections)) for d in snapshot.documents])
    report['passed'] = not any(differences.values())
    (work / 'coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    if not report['passed']:
        raise ValueError('Independent coverage audit failed: ' + json.dumps(differences))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir')
    parser.add_argument('--work-dir')
    args = parser.parse_args()
    from bot_center_runtime.paths import Installation
    if args.data_dir is None:args.data_dir=Installation().require_state('french')['notes_dir']
    work = Path(args.work_dir) if args.work_dir else Installation().root / 'work/audit' / ('telegram-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    work.mkdir(parents=True, exist_ok=True)
    protect(args.data_dir, work)
    reader = SourceReader(args.data_dir)
    sources = reader.capture()
    if [(s.name,s.hash) for s in sources] != [(s.name,s.hash) for s in reader.capture()]:
        raise ValueError('Source changed during baseline capture.')
    snapshot = build(sources)
    report = parity(snapshot, work)
    (work / 'baseline-snapshot.json').write_text(snapshot.dumps(), 'utf-8')
    print(json.dumps(dict(work=str(work), **report), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
