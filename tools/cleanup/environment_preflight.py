"""Read-only reconstruction proof for obsolete French dependency directories."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PROBE = """import hashlib,importlib.metadata,json,sys
items={}
for distribution in importlib.metadata.distributions():
    name=distribution.metadata.get('Name','').lower().replace('_','-')
    value=hashlib.sha256()
    files=0
    for relative in sorted(distribution.files or [],key=str):
        if '..' in relative.parts or '__pycache__' in relative.parts or str(relative).endswith(('.pyc','.pyo')) or relative.name in {'RECORD','INSTALLER','REQUESTED','direct_url.json'}:continue
        path=distribution.locate_file(relative)
        value.update(str(relative).encode())
        if path.is_file():value.update(hashlib.sha256(path.read_bytes()).digest());files+=1
        else:value.update(b'MISSING')
    direct=distribution.read_text('direct_url.json')
    items[name]={'version':distribution.version,'code_sha256':value.hexdigest(),'files':files,'local_or_editable_install':bool(direct)}
print(json.dumps({'python':sys.version,'base_executable_sha256':hashlib.sha256(__import__('pathlib').Path(sys._base_executable).read_bytes()).hexdigest(),'packages':items},sort_keys=True))
"""


def environment(interpreter):
    result = subprocess.run([str(interpreter), '-c', PROBE], capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('A dependency environment could not be inspected; it remains pinned.')
    return json.loads(result.stdout)


def run(root=ROOT):
    root = Path(root).resolve()
    wheelhouse = root / 'work/build/wheelhouse'
    wheels = sorted(wheelhouse.glob('*.whl'))
    results = []
    for old_tag, new_tag in [('.venv-bot', 'french-reader'), ('.venv', 'french-editor')]:
        original = root / 'French Notes' / old_tag
        final = root / '.venvs' / new_tag
        if not original.exists():
            results.append({'original': str(original), 'status': 'Not applicable', 'reason': 'Original environment already absent; consult its prior cleanup evidence'})
            continue
        old, new = environment(original / 'Scripts/python.exe'), environment(final / 'Scripts/python.exe')
        checks = []; tooling = []
        for name, package in sorted(old['packages'].items()):
            replacement = new['packages'].get(name)
            if name in ('pip', 'setuptools', 'wheel'):
                tooling.append({'name': name, 'old': package['version'], 'new': replacement['version'] if replacement else None})
                continue
            prefix = re.sub(r'[-_.]+', '_', name).casefold() + '-' + package['version'] + '-'
            pinned = [path.name for path in wheels if path.name.casefold().startswith(prefix)]
            checks.append({'name': name, 'version': package['version'], 'same_version': bool(replacement and replacement['version'] == package['version']),
                           'same_runtime_code': bool(replacement and replacement['code_sha256'] == package['code_sha256']), 'compatible_wheels': pinned,
                           'original_code_sha256': package['code_sha256'], 'local_or_editable_install': package['local_or_editable_install']})
        passed = old['base_executable_sha256'] == new['base_executable_sha256'] and all(item['same_version'] and item['same_runtime_code'] and item['compatible_wheels'] for item in checks)
        results.append({'original': str(original), 'replacement': str(final), 'status': 'Passed' if passed else 'Blocked',
                        'same_base_interpreter': old['base_executable_sha256'] == new['base_executable_sha256'], 'runtime_dependencies': checks,
                        'build_tool_differences': tooling, 'build_tool_note': 'Final environment build inputs are separately pinned; scripts/installer metadata and pyc are regenerated, not moved'})
    original_lock = root / 'French Notes/package-lock.json'; final_lock = root / 'package-lock.json'
    if original_lock.exists() and final_lock.exists():
        old = json.loads(original_lock.read_text('utf-8')).get('packages', {})
        new = json.loads(final_lock.read_text('utf-8')).get('packages', {})
        old.pop('', None); new.pop('', None)
        checks = [{'package': name, 'same_locked_dependency': new.get(name) == value} for name, value in old.items()]
        original_modules, final_modules = root / 'French Notes/node_modules', root / 'node_modules'
        differing = []; checked_files = 0
        if original_modules.exists() and final_modules.exists():
            for path in original_modules.rglob('*'):
                relative = path.relative_to(original_modules)
                if relative.as_posix() == '.package-lock.json' or not path.is_file():
                    continue
                target = final_modules / relative
                checked_files += 1
                if path.is_symlink() or not target.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                    differing.append(relative.as_posix())
        results.append({'original': str(root / 'French Notes/node_modules'), 'replacement': str(root / 'node_modules'),
                        'status': 'Passed' if checks and checked_files and not differing and all(item['same_locked_dependency'] for item in checks) else 'Blocked',
                        'locked_dependencies': checks, 'actual_module_files_compared': checked_files, 'differing_module_files': differing,
                        'excluded_regenerated_metadata': ['node_modules/.package-lock.json']})
    report = {'schema': 1, 'status': 'Passed' if all(item['status'] in ('Passed', 'Not applicable') for item in results) else 'Blocked',
              'created': time.time(), 'results': results, 'wheel_hashes': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels},
              'cleanup_still_requires': ['Fresh final-path application/UI/contract acceptance', 'No active old consumers or writers', 'Recovery pins retain required wheels and build inputs', 'Exact absolute deletion ledger and final fingerprint recheck']}
    output = root / 'work/cleanup/environment-reconstruction.json'
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, indent=2, sort_keys=True), 'utf-8')
    return {'status': report['status'], 'environments': len(results), 'report': str(output), 'blocked': [item['original'] for item in results if item['status'] == 'Blocked']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, default=ROOT); args = parser.parse_args()
    print(json.dumps(run(args.root)))
