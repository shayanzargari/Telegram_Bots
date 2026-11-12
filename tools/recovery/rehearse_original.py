"""Independently reconstruct protected originals and compare full nested Git state.

This is a developer/recovery tool; source loading allows its use before the first
core wheel exists. Production uses the installed bot_center.recovery module.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recovery_implementation', ROOT / 'core/src/bot_center/recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def run(snapshot, output=None, resume=False):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'manifest.json').read_text('utf-8'))
    if not manifest.get('verified'):
        raise recovery.RecoveryError('Original capture is not verified.')
    output = Path(output).resolve() if output else ROOT / 'work/recovery' / ('original-rehearsal-' + uuid.uuid4().hex[:12])
    if output.exists() and not resume:
        raise recovery.RecoveryError('Rehearsal output already exists.')
    recovery.private_directory(output)
    restored = output / 'private-restored'
    recovery.private_directory(restored)
    supplement = snapshot / 'directory-topology.json'
    if not supplement.exists() and not manifest.get('directories'):
        directories = []
        for source in manifest['sources']:
            base = Path(source['path'])
            if not base.is_dir():
                continue
            for directory, dirs, _ in os.walk(base, followlinks=False):
                dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink() and not getattr((Path(directory) / name).stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400)
                directories.append({'source': source['label'], 'relative': Path(directory).relative_to(base).as_posix()})
        recovery.atomic_json(supplement, {'schema': 1, 'created': time.time(), 'reason': 'Independent rehearsal found missing empty directory topology in original file-only capture', 'directories': directories})
    directories = manifest.get('directories') or json.loads(supplement.read_text('utf-8'))['directories']
    for item in directories:
        relative = item['source'] if item['relative'] == '.' else item['source'] + '/' + item['relative']
        recovery.contained(restored, relative).mkdir(parents=True, exist_ok=True)
    for item in manifest['files']:
        destination = recovery.contained(restored, item['source'] + '/' + item['relative'])
        if not destination.exists():
            recovery.unseal(snapshot / item['member'], destination)
        if destination.stat().st_size != item['bytes'] or recovery.sha256(destination) != item['sha256']:
            raise recovery.RecoveryError('Independent original restoration failed a file hash.')
    repository = restored / 'home-.publish/home_shopping'
    original = next(Path(item['path']) / 'home_shopping' for item in manifest['sources'] if item['label'] == 'home-.publish')
    checks = {}
    for label, arguments in [('status', ['status', '--porcelain=v1', '-z', '--untracked-files=all']), ('objects', ['fsck', '--full', '--no-reflogs'])]:
        results = []
        for path in (original, repository):
            result = subprocess.run(['git', '--no-optional-locks', '-C', str(path), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            results.append((result.returncode, result.stdout, result.stderr))
        checks[label] = {'equal': results[0] == results[1], 'bytes': len(results[0][1]), 'sha256': __import__('hashlib').sha256(results[0][1]).hexdigest()}
        if not checks[label]['equal']:
            raise recovery.RecoveryError('Restored nested Git state differs from its original.')
    db_checks = []
    for label, relative in [('home-data', 'tasks.sqlite3'), ('reader', 'reader.sqlite3')]:
        original_root = next(Path(item['path']) for item in manifest['sources'] if item['label'] == label)
        first = recovery.sqlite_semantics(original_root / relative)
        second = recovery.sqlite_semantics(restored / label / relative)
        if first != second:
            raise recovery.RecoveryError('Original state changed or restored SQLite semantics differ.')
        db_checks.append({'owner': label, 'status': 'Passed', 'semantics': second})
    report = {'schema': 1, 'status': 'Passed', 'snapshot': str(snapshot), 'file_count': len(manifest['files']), 'directory_count': len(directories),
              'nested_git': checks, 'database_checks': db_checks, 'private_decryption_verified': True,
              'independent_restoration': True, 'created': time.time()}
    recovery.atomic_json(output / 'summary.json', report)
    # The only plaintext originals were inside this explicitly private, isolated
    # rehearsal; preserve the sealed capture and compact hash evidence.
    if restored.parent.resolve() != output.resolve() or restored.is_symlink():
        raise recovery.RecoveryError('Unexpected rehearsal deletion target.')
    shutil.rmtree(restored)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('snapshot', type=Path); parser.add_argument('--output', type=Path); parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output, args.resume), ensure_ascii=True))
