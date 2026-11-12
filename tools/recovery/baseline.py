"""Fresh consistent original-state baseline; no private record values in reports."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recovery_implementation', ROOT / 'core/src/bot_center/recovery.py')
recovery = importlib.util.module_from_spec(spec); spec.loader.exec_module(recovery)


def baseline(root=ROOT):
    root = Path(root).resolve()
    output = root / 'work/migration' / ('baseline-' + uuid.uuid4().hex[:12])
    recovery.private_directory(output)
    local = Path(os.environ['LOCALAPPDATA'])
    sources = {'home': root / 'Our_Home_Telegram_Bot/data/tasks.sqlite3', 'french_reader': local / 'French Notes Telegram/reader.sqlite3'}
    databases = {}
    for owner, source in sources.items():
        if not source.is_file():
            raise recovery.RecoveryError('Expected original application database is missing.')
        snapshot = output / (owner + '.sqlite3')
        semantics = recovery.sqlite_snapshot(source, snapshot)
        sealed = snapshot.with_suffix('.dpapi')
        recovery.seal(snapshot, sealed)
        snapshot.unlink()
        databases[owner] = {'source': str(source), 'snapshot': str(sealed), 'semantics': semantics}
    directories = {'french_notes': root / 'French Notes/french', 'french_reader_assets': local / 'French Notes Telegram',
                   'french_editor': local / 'French Notes/afc704eab9aa', 'home_assets': root / 'Our_Home_Telegram_Bot/data'}
    files = []
    for owner, directory in directories.items():
        if not directory.is_dir():
            raise recovery.RecoveryError('Expected original state directory is missing.')
        for path in recovery.regular_files(directory):
            if path.name.endswith(('-wal', '-shm', '.lock')) or path.suffix in ('.sqlite3', '.db', '.sqlite') or path.name in ('bot.log', 'app.log', 'server.json'):
                continue
            files.append({'owner': owner, 'relative': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size, 'sha256': recovery.sha256(path)})
    report = {'schema': 1, 'created': time.time(), 'status': 'Passed', 'databases': databases, 'files': files,
              'snapshot_method': 'sqlite3.Connection.backup includes committed WAL', 'no_private_record_values': True}
    recovery.atomic_json(output / 'baseline.json', report)
    return {'status': 'Passed', 'path': str(output / 'baseline.json'), 'databases': len(databases), 'files': len(files)}


if __name__ == '__main__':
    print(json.dumps(baseline()))
