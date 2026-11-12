"""Restore split source files after cloning; verify every part before writing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile


def contained(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(p in ('', '.', '..') for p in relative.parts) or ':' in name or '\\' in name:
        raise ValueError('Unsafe manifest path')
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Manifest path escapes the selected directory')
    return target


def restore(source, output, check=False):
    source, output = source.resolve(), output.resolve()
    manifest = json.loads((source / 'TRANSFER_MANIFEST.json').read_text('utf-8'))
    prepared = []
    for entry in manifest['split_files']:
        content = bytearray()
        for part in entry['parts']:
            raw = contained(source, part['path']).read_bytes()
            if len(raw) != part['bytes'] or hashlib.sha256(raw).hexdigest() != part['sha256']:
                raise ValueError('Part checksum mismatch: ' + part['path'])
            content.extend(raw)
        if len(content) != entry['bytes'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('Reconstructed checksum mismatch: ' + entry['path'])
        target = contained(output, entry['path'])
        if target.exists() and target.read_bytes() != content:
            raise ValueError('Existing destination differs; preserved: ' + entry['path'])
        prepared.append((target, content))
    if not check:
        for target, content in prepared:
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix='.restore-', dir=target.parent)
            try:
                with os.fdopen(handle, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Exclusive creation preserves a destination created since validation.
                with target.open('xb') as destination, open(temporary, 'rb') as stream:
                    destination.write(stream.read())
            finally:
                Path(temporary).unlink(missing_ok=True)
    return {'status': 'Passed', 'split_files': len(prepared), 'check_only': check}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(restore(args.source_root, args.output_root or args.source_root, args.check)))
