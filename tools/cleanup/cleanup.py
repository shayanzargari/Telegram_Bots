"""Apply a small exact, evidenced cleanup ledger after all preservation gates pass."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = {'data', '.secrets', '.git'}
ORIGINAL_STATE = (('our_home_telegram_bot', 'data'), ('french notes', 'french'))


def assert_regular_tree(path):
    """Reject links and uniquely protected material before hashing or removal."""
    path = Path(path)
    if not path.exists():
        raise ValueError('Cleanup target is missing; refresh its disposition before mutation.')
    def inspect(entry):
        attributes = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or getattr(attributes, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Cleanup does not traverse links.')
        if entry.name.casefold() == '.git':
            raise ValueError('Git history and unique repository work are protected.')
    inspect(path)
    if path.is_file():
        return [path]
    files = []
    for directory, dirs, names in os.walk(path, followlinks=False):
        # Check every directory before os.walk can descend into a junction.
        for name in dirs:
            inspect(Path(directory) / name)
        for name in names:
            entry = Path(directory) / name
            inspect(entry)
            files.append(entry)
    return sorted(files)


def fingerprint(path):
    path = Path(path)
    files = assert_regular_tree(path)
    entries = []
    for file in files:
        if file.is_symlink() or getattr(file.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Cleanup does not traverse links.')
        if file.is_file():
            h = hashlib.sha256()
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
            entries.append({'relative': file.name if path.is_file() else file.relative_to(path).as_posix(), 'sha256': h.hexdigest(), 'bytes': file.stat().st_size})
    return {'sha256': hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(), 'bytes': sum(item['bytes'] for item in entries), 'files': len(entries)}


def validate(root, item):
    root = Path(root).resolve()
    target = Path(item['path'])
    if not target.is_absolute():
        raise ValueError('Cleanup requires an exact absolute target.')
    resolved = target.resolve()
    if root not in resolved.parents or resolved == root or target != resolved or resolved.is_symlink():
        raise ValueError('Cleanup target escapes scope or resolves through a link.')
    relative = resolved.relative_to(root)
    parts = tuple(name.casefold() for name in relative.parts)
    state_overlap = any(parts[:len(prefix)] == prefix or prefix[:len(parts)] == parts for prefix in ORIGINAL_STATE)
    if parts[0] in PROTECTED or '.git' in parts or state_overlap or any(name.endswith(('-wal', '-shm')) for name in parts):
        raise ValueError('Authoritative state and recovery material cannot be generically deleted.')
    if parts[0] == 'backups':
        # Complete/required recovery sets are never routine cleanup candidates.
        # A failed file-only partial capture is removable solely after every
        # encrypted member was privately decrypted and covered by a verified set.
        proof_path = root / item.get('partial_capture_proof', 'invalid')
        if not proof_path.is_file() or (resolved / 'manifest.json').exists():
            raise ValueError('Required or complete recovery material is protected.')
        proof = json.loads(proof_path.read_text('utf-8'))
        replacement = Path(proof.get('verified_replacement', 'invalid')) / 'manifest.json'
        if proof.get('status') != 'Passed' or Path(proof.get('partial', 'invalid')).resolve() != resolved or not proof.get('members') or not all(member.get('covered') for member in proof['members']) or not replacement.is_file():
            raise ValueError('Partial-capture redundancy was not established.')
        if not json.loads(replacement.read_text('utf-8')).get('verified'):
            raise ValueError('Replacement recovery material is not verified.')
    required = ('owner', 'classification', 'reason', 'consumers_checked', 'preservation_route', 'verification', 'expected_fingerprint')
    if any(not item.get(key) for key in required):
        raise ValueError('Cleanup entry lacks a required preservation gate.')
    if item.get('status') != 'Eligible' or not item.get('no_active_consumer') or not item.get('no_active_run'):
        raise ValueError('Cleanup entry is not eligible.')
    for value in item['verification']:
        proof = Path(value) if Path(value).is_absolute() else root / value
        if not proof.is_file():
            raise ValueError('Cleanup verification evidence is missing.')
        if proof.suffix == '.json':
            report = json.loads(proof.read_text('utf-8'))
            if report.get('status') not in ('Passed', 'Verified', 'Completed') and report.get('verified') is not True:
                raise ValueError('Cleanup evidence did not pass.')
    observed = fingerprint(resolved)
    if observed != item['expected_fingerprint']:
        raise ValueError('Cleanup target changed after inventory; mutation stopped.')
    return resolved, observed


def apply(root, ledger_path, limit=20):
    root = Path(root).resolve(); ledger_path = Path(ledger_path).resolve()
    if limit < 1:
        raise ValueError('Cleanup batch limit must be positive.')
    ledger = json.loads(ledger_path.read_text('utf-8'))
    eligible = [entry for entry in ledger['entries'] if entry.get('status') == 'Eligible'][:limit]
    # Validate the entire small batch before deleting its first member.
    checked = [(entry, *validate(root, entry)) for entry in eligible]
    before = shutil.disk_usage(root).free
    total = 0
    for entry, target, observed in checked:
        target_now, current = validate(root, entry)
        if target_now != target or current != observed:
            raise ValueError('Cleanup target changed before deletion.')
        if target.is_file(): target.unlink()
        else: shutil.rmtree(target)
        entry.update(status='Removed', removed_at=time.time(), logical_bytes_removed=observed['bytes'])
        total += observed['bytes']
        # Preserve the per-item outcome if the next member fails.
        ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), 'utf-8')
    after = shutil.disk_usage(root).free
    report = {'schema': 1, 'status': 'Passed', 'removed_items': len(checked), 'logical_bytes_removed': total,
              'observed_volume_free_delta': after - before, 'allocation_measurement': 'Volume free-space delta; concurrent unrelated writers may affect it',
              'archive_moves_count_as_reclaimed': False, 'created': time.time()}
    ledger['last_batch'] = report
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), 'utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, default=ROOT); parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--apply', action='store_true'); parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(apply(args.root, args.ledger, args.limit)))
    else:
        ledger = json.loads(args.ledger.read_text('utf-8'))
        print(json.dumps({'status': 'Inspected', 'eligible': len([entry for entry in ledger['entries'] if entry.get('status') == 'Eligible'])}))
