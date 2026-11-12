"""Build exact disposition/cleanup ledgers from the refreshed inventory."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


recovery = load('isolated_recovery', 'core/src/bot_center/recovery.py')
cleanup = load('controlled_cleanup', 'tools/cleanup/cleanup.py')


def disposition():
    inventory = json.loads((ROOT / 'work/inventory/files.json').read_text('utf-8'))
    result = []
    for item in inventory:
        owner, relative = item['owner'], item['relative']
        first = relative.split('/')[0]
        target = None; classification = 6; state = 'Pinned unresolved'; reason = 'Require exact consumer/recovery comparison before disposition'
        if owner == 'home':
            if first in ('bot.py', 'storage.py', 'seed_tasks.json'):
                target = 'bots/home/src/home_bot/' + relative; classification = 1; state = 'Packaged replacement'; reason = 'Active Home application source'
            elif first == 'tests':
                if '__pycache__' in relative.split('/') or relative.endswith(('.pyc', '.pyo')):
                    classification = 4; state = 'Generated cache candidate'; reason = 'Regenerate bytecode from maintained tests in the recorded interpreter'
                else:
                    target = 'tests/home/' + relative[len('tests/'):]; classification = 2; state = 'Maintained regression'; reason = 'Preserve distinct existing Home assertions'
            elif first == 'data':
                target = 'data/home/' + relative[len('data/'):]; classification = 1; state = 'Staged preservation'; reason = 'Authoritative Home state; credentials separated into encrypted store'
            elif first == '.publish':
                target = 'backups/pre-migration-20261111T191326Z/manifest.json'; classification = 3; state = 'Pinned original'; reason = 'Complete nested checkout with Git index/worktree/ignored/dangling objects; independent restore passed'
            elif first in ('local_telegram.py', 'simulator.html'):
                target = 'bots/home/src/home_bot/' + ('simulator.py' if first == 'local_telegram.py' else relative); classification = 2; state = 'Maintained simulator'; reason = 'Home simulator must not load production credentials'
            elif first == '__pycache__':
                classification = 4; state = 'Generated cache candidate'; reason = 'Only remove after current source/reconstruction is verified'
            elif first == '.local-telegram':
                classification = 6; state = 'Pinned unresolved'; reason = 'Historical fixtures/fault reports may be consumed; extraction and distinct-scenario gates required'
            elif relative.endswith(('.md', '.bat', '.vbs')):
                target = 'archive/original-instructions/home/' + relative; classification = 3; state = 'Compatibility/evidence review'; reason = 'Original instructions and launchers preserved until consumers migrate'
        elif owner == 'french':
            if first == 'french':
                target = 'data/french/notes/' + relative[len('french/'):]; classification = 1; state = 'Staged preservation'; reason = 'Approved note content/names and historical note backups remain byte exact'
            elif first in ('app.py', 'editor_store.py', 'store.py', 'tense_notes.py', 'speech.py', 'speech_process.py', 'speech_worker.py', 'speak_french.ps1') or first == 'ui' or first == 'telegram_bot':
                target = 'bots/french/src/french_notes/' + relative; classification = 1; state = 'Packaged replacement'; reason = 'French application source/package assets; domain ownership preserved'
            elif first == 'tests':
                if '__pycache__' in relative.split('/') or relative.endswith(('.pyc', '.pyo')):
                    classification = 4; state = 'Generated cache candidate'; reason = 'Regenerate bytecode from maintained tests in the recorded interpreter'
                elif relative == 'tests/make_icon.cjs':
                    target = 'tools/apps/assets/make_icon.cjs'; classification = 2; state = 'Maintained asset tooling'; reason = 'Icon generation is maintained asset tooling, not an application regression'
                else:
                    target = 'tests/french/' + relative[len('tests/'):]; classification = 2; state = 'Maintained regression'; reason = 'Existing Python/CJS/audio fixture coverage'
            elif first in ('.venv', '.venv-bot', 'node_modules'):
                target = '.venvs/french-editor' if first == '.venv' else '.venvs/french-reader' if first == '.venv-bot' else 'node_modules'
                classification = 4; state = 'Pinned dependency'; reason = 'Final-path replacement and compatible recovery inputs must pass before removal'
            elif first == 'checks':
                classification = 6; state = 'Pinned unresolved'; reason = 'Required private corpus extracted to tests/french/support/private; unique scenarios and evidence still require exact review'
            elif first == '__pycache__':
                classification = 4; state = 'Generated cache candidate'; reason = 'Only remove after current source/reconstruction is verified'
            else:
                target = 'archive/original-instructions/french/' + relative; classification = 3; state = 'Pinned original'; reason = 'Preserve old instructions/shortcuts/migration evidence; do not execute date-specific content scripts'
        elif owner == 'reader':
            target = 'data/french/reader/' + relative; classification = 1; state = 'Staged preservation'; reason = 'Reader ownership/preferences/positions/snapshots and prepared audio'
        elif owner == 'editor':
            prefix = 'afc704eab9aa/'
            target = 'data/french/editor/' + (relative[len(prefix):] if relative.startswith(prefix) else relative)
            classification = 1; state = 'Staged preservation'; reason = 'Editor drafts/history/recovery audio and dedicated application browser profile'
        result.append({'old_path': item['path'], 'owner': owner, 'classification': classification, 'target': target, 'disposition': state,
                       'reason': reason, 'logical_bytes': item['bytes'], 'source_sha256': item['sha256'],
                       'consumer_check': 'Imports, dynamic loading, subprocesses, assets, launchers, test/manual/release references are owned by the component; unresolved cases are pinned'})
    record = {'schema': 1, 'created': time.time(), 'inventory': 'work/inventory/files.json', 'entries': result, 'classifications': {str(number): sum(entry['classification'] == number for entry in result) for number in range(1, 7)}}
    recovery.atomic_json(ROOT / 'docs/disposition-ledger.json', record)
    return {'path': 'docs/disposition-ledger.json', 'entries': len(result), 'classifications': record['classifications']}


def cover_partial(partial, verified):
    partial, verified = Path(partial).resolve(), Path(verified).resolve()
    manifest = json.loads((verified / 'manifest.json').read_text('utf-8'))
    known = {item['sha256'] for item in manifest['files']}
    entries = []
    for path in sorted(partial.glob('*.dpapi')):
        h = hashlib.sha256()
        with path.open('rb') as incoming:
            while header := incoming.read(4):
                if len(header) != 4:
                    raise ValueError('Truncated partial capture cannot be classified as redundant.')
                length = struct.unpack('<I', header)[0]
                if not 0 < length <= recovery.MAX_FRAME:
                    raise ValueError('Malformed partial capture cannot be classified as redundant.')
                h.update(recovery.dpapi(incoming.read(length), True))
        entries.append({'member': path.name, 'sha256': h.hexdigest(), 'covered': h.hexdigest() in known})
    report = {'schema': 1, 'status': 'Passed' if entries and all(entry['covered'] for entry in entries) else 'Blocked',
              'kind': 'Resolved failed encrypted capture', 'partial': str(partial), 'verified_replacement': str(verified),
              'members': entries, 'created': time.time(), 'reason': 'Every privately decrypted partial member hash must exist in the verified original set'}
    output = ROOT / 'work/cleanup/partial-capture-coverage.json'
    recovery.atomic_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--disposition', action='store_true')
    parser.add_argument('--partial', type=Path); parser.add_argument('--verified', type=Path)
    args = parser.parse_args()
    if args.disposition:
        print(json.dumps(disposition()))
    if args.partial:
        report = cover_partial(args.partial, args.verified)
        print(json.dumps({'status': report['status'], 'members': len(report['members']), 'uncovered': sum(not entry['covered'] for entry in report['members'])}))


if __name__ == '__main__': main()
