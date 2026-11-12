"""Prepare a local, credential-free wheel release; performs no remote operation."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TOKEN = re.compile(rb'\d{5,}:[A-Za-z0-9_-]{20,}')
FIXTURE_VALUES = {b'123456:offline_fixture_012345678901234567890', b'000000:local-simulator-only'}


def prepare(output, root=ROOT):
    root = Path(root).resolve(); output = Path(output).resolve()
    if root / 'work/releases' not in output.parents or output.exists():
        raise ValueError('Choose a new release directory under work/releases.')
    wheelhouse = root / 'work/build/wheelhouse'
    wheels = sorted(wheelhouse.glob('*.whl'))
    if not wheels:
        raise ValueError('Build locked installed wheels before preparing a release.')
    record = {'schema': 1, 'kind': 'local-wheel-release', 'created': time.time(), 'wheels': [], 'remote_operations': 0,
              'runtime_state_included': False, 'credentials_included': False, 'status': 'Prepared'}
    for path in wheels:
        with zipfile.ZipFile(path) as source:
            if source.testzip() is not None:
                raise ValueError('A wheel failed its archive integrity check.')
            for name in source.namelist():
                if name.endswith(('.py', '.json', '.toml', '.txt', '.md', '.cfg', '.ini', 'METADATA')):
                    if any(match.group() not in FIXTURE_VALUES for match in TOKEN.finditer(source.read(name))):
                        raise ValueError('Credential-like material found in a release input; no archive was published.')
        record['wheels'].append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    output.mkdir(parents=True)
    archive = output / 'release.zip'
    temporary = output / ('release-' + uuid.uuid4().hex + '.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as target:
        for path in wheels:
            target.write(path, 'wheelhouse/' + path.name)
        for path in sorted((root / 'tools/environments').glob('*.lock.json')):
            target.write(path, 'locks/' + path.name)
        target.writestr('manifest.json', json.dumps(record, indent=2, sort_keys=True))
    with zipfile.ZipFile(temporary) as check:
        if check.testzip() is not None:
            raise ValueError('Local release validation failed.')
    temporary.replace(archive)
    record.update(status='Passed', archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), archive_bytes=archive.stat().st_size)
    (output / 'manifest.json').write_text(json.dumps(record, indent=2, sort_keys=True), 'utf-8')
    return {'status': 'Passed', 'output': str(output), 'wheels': len(wheels), 'archive_bytes': record['archive_bytes'], 'remote_operations': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
    print(json.dumps(prepare(args.output)))
