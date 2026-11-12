"""Standard-library suite coordinator with explicit environments and bounded evidence."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
OUTCOMES = {'Passed', 'Failed', 'Blocked', 'Skipped', 'Not applicable', 'Reused evidence'}
TOKEN = re.compile(r'\d{5,}:[A-Za-z0-9_-]{20,}')
EXCLUDED = {'.git', '.secrets', '.venvs', 'node_modules', '__pycache__', 'work', 'backups', 'archive', '.publish', '.local-telegram', 'checks', '.venv', '.venv-bot'}


def redact(value):
    return TOKEN.sub('[REDACTED]', str(value))


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def load_catalog(root=ROOT):
    record = json.loads((Path(root) / 'config/suites.json').read_text('utf-8'))
    suites = record.get('suites')
    if record.get('schema') != 1 or not isinstance(suites, list): raise ValueError('Unsupported suite catalog.')
    seen = set()
    for suite in suites:
        identity = suite['id']
        if identity in seen or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', identity): raise ValueError('Invalid or duplicate suite ID.')
        seen.add(identity)
        if not isinstance(suite.get('command'), list) or not suite['command']: raise ValueError('Suite requires an executable argument list.')
        if suite.get('category') == 'live' and not suite.get('resources'): raise ValueError('Live suite must declare exclusive resources.')
    return suites


def select_suites(catalog, requested=(), profiles=(), changed_paths=()):
    known = {suite['id'] for suite in catalog}
    unknown = set(requested) - known
    if unknown: raise ValueError('Unknown suite IDs: ' + ', '.join(sorted(unknown)))
    normal = [str(path).replace('\\', '/').strip('/').casefold() for path in changed_paths]
    selected = []
    for suite in catalog:
        include = suite['id'] in requested or any(profile in suite.get('profiles', []) for profile in profiles if profile != 'affected')
        if 'affected' in profiles:
            if not normal: raise ValueError('The affected profile requires --changed-path.')
            for change in normal:
                for value in suite.get('inputs', []):
                    value = value.replace('\\', '/').casefold().rstrip('/')
                    if change.startswith(value + '/') or value.startswith(change + '/') or change == value or fnmatch.fnmatch(change, value): include = True
        if include and suite['id'] not in {entry['id'] for entry in selected}: selected.append(suite)
    if 'affected' in profiles:
        unmatched = []
        for change in normal:
            matched = False
            for suite in catalog:
                for value in suite.get('inputs', []):
                    value = value.replace('\\', '/').casefold().rstrip('/')
                    if change.startswith(value + '/') or value.startswith(change + '/') or change == value or fnmatch.fnmatch(change, value):
                        matched = True
            if not matched:
                unmatched.append(change)
        if unmatched:
            # Unknown impact broadens offline coverage; documentation-only paths
            # keep the inexpensive context route.
            documentation_only = all(value.endswith('.md') or '/.agents/skills/' in '/' + value for value in unmatched)
            broaden = [suite for suite in catalog if suite.get('category') != 'live' and
                       (suite.get('category') == 'context' if documentation_only else suite.get('category') != 'performance')]
            for suite in broaden:
                if suite['id'] not in {entry['id'] for entry in selected}:
                    selected.append(suite)
    return selected


@contextmanager
def reserve(root, resources):
    streams = []
    try:
        for resource in sorted(set(resources)):
            name = hashlib.sha256(resource.encode()).hexdigest()[:32] + '.lock'
            path = root / 'work/leases' / name; path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open('a+b'); stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close(); raise RuntimeError('An exclusive test resource is busy: ' + resource) from None
            streams.append(stream)
        yield
    finally:
        for stream in reversed(streams): stream.close()


def resolve_environment(root, tag):
    tag = {'qa': 'core', 'core/qa': 'core'}.get(tag, tag)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', tag): raise ValueError('Invalid environment tag.')
    python = root / '.venvs' / tag / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return python


def environment_fingerprint(python):
    if not python.is_file(): return {'status': 'Missing', 'path': str(python)}
    probe = """import hashlib,importlib.metadata,json,sys
packages=[]
for distribution in importlib.metadata.distributions():
    observed=hashlib.sha256()
    missing=0
    for relative in sorted(distribution.files or [],key=str):
        if '__pycache__' in relative.parts or str(relative).endswith(('.pyc','.pyo')):continue
        path=distribution.locate_file(relative)
        observed.update(str(relative).encode())
        if path.is_file():observed.update(hashlib.sha256(path.read_bytes()).digest())
        else:missing+=1
    packages.append({'name':distribution.metadata.get('Name',''),'version':distribution.version,'installed_files_sha256':observed.hexdigest(),'missing_files':missing})
print(json.dumps({'python':sys.version,'executable':sys.executable,'packages':sorted(packages,key=lambda value:value['name'])},sort_keys=True))
"""
    result = subprocess.run([str(python), '-c', probe], capture_output=True, text=True, timeout=30)
    if result.returncode: return {'status': 'Failed', 'path': str(python), 'exit_code': result.returncode}
    return json.loads(result.stdout)


def input_fingerprint(root, suite, environment):
    entries = {}
    for value in suite.get('inputs', []):
        paths = list(root.glob(value)) if any(c in value for c in '*?[') else [root / value]
        for path in paths:
            if not path.exists(): entries[value] = 'MISSING'; continue
            candidates = [path] if path.is_file() else path.rglob('*')
            for candidate in candidates:
                if candidate.is_file() and not candidate.is_symlink() and not any(part in EXCLUDED for part in candidate.relative_to(root).parts):
                    entries[candidate.relative_to(root).as_posix()] = sha256(candidate)
    harness = Path(__file__).resolve()
    record = {'inputs': entries, 'environment': environment, 'suite': suite, 'harness_sha256': sha256(harness),
              'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()}}
    record['sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return record


def terminate_owned(process):
    if process.poll() is not None: return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill(); process.wait()


def execute(command, cwd, environment, output, timeout):
    output.mkdir(parents=True, exist_ok=True)
    options = {'cwd': str(cwd), 'env': environment, 'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
               'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0}
    if os.name != 'nt': options['start_new_session'] = True
    process = subprocess.Popen(command, **options)
    totals = {}
    def drain(name, pipe):
        count = 0; kept = 0
        with (output / (name + '.log')).open('w', encoding='utf-8', newline='\n') as destination:
            # Read bounded binary chunks, carrying token-sized overlap to avoid
            # leaking a credential split at a pipe chunk boundary.
            pending = b''
            while chunk := pipe.read(8192):
                count += len(chunk); pending += chunk
                if len(pending) <= 4096: continue
                boundary = pending.rfind(b'\n', 0, len(pending) - 2048)
                if boundary < 0:
                    if len(pending) > 131072:
                        # Long unstructured payloads are summarized without echo.
                        pending = pending[-2048:]
                    continue
                text = redact(pending[:boundary + 1].decode('utf-8', 'replace')); pending = pending[boundary + 1:]
                if kept < 1024 * 1024:
                    destination.write(text[:1024 * 1024 - kept]); kept += len(text[:1024 * 1024 - kept])
            text = redact(pending.decode('utf-8', 'replace'))
            if kept < 1024 * 1024: destination.write(text[:1024 * 1024 - kept])
            if count > 1024 * 1024: destination.write('\n[Diagnostic output truncated at the routine limit.]\n')
        totals[name] = count
        pipe.close()
    threads = [threading.Thread(target=drain, args=(name, pipe), daemon=True) for name, pipe in [('stdout', process.stdout), ('stderr', process.stderr)]]
    for thread in threads: thread.start()
    timed_out = False
    try: process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True; terminate_owned(process)
    for thread in threads: thread.join(timeout=15)
    if any(thread.is_alive() for thread in threads):
        terminate_owned(process)
        raise RuntimeError('Fixture output pipes did not drain after process completion.')
    return {'exit_code': process.returncode, 'timed_out': timed_out, 'diagnostic_bytes': totals}


def retained_index(root):
    path = root / 'work/tests/index.json'
    return json.loads(path.read_text('utf-8')) if path.exists() else {'schema': 1, 'runs': []}


def run_suite(root, suite, run_dir, fingerprint, *, live=False):
    started = time.time(); output = run_dir / suite['id']; output.mkdir(parents=True, exist_ok=True)
    base = {'suite': suite['id'], 'owner': suite['owner'], 'environment': suite['environment'], 'fingerprint': fingerprint['sha256'],
            'started': started, 'output': output.relative_to(root).as_posix(), 'retention': 'routine', 'resources': suite.get('resources', [])}
    required = suite.get('platform', 'any')
    if required == 'windows' and os.name != 'nt': return {**base, 'outcome': 'Not applicable', 'reason': 'Native Windows required.', 'duration_seconds': 0}
    if suite.get('category') == 'live' and not live: return {**base, 'outcome': 'Blocked', 'reason': 'Live checks require explicit --profile live.', 'duration_seconds': 0}
    python = resolve_environment(root, suite['environment'])
    if not python.is_file(): return {**base, 'outcome': 'Blocked', 'reason': 'Declared environment is not installed.', 'duration_seconds': 0}
    node = shutil.which('node')
    if '{node}' in suite['command'] and not node: return {**base, 'outcome': 'Blocked', 'reason': 'Node browser QA prerequisite is missing.', 'duration_seconds': 0}
    mapping = {'root': str(root), 'run_dir': str(run_dir), 'suite_dir': str(output), 'python': str(python), 'node': node or 'node'}
    command = [part.format_map(mapping) for part in suite['command']]
    env = os.environ.copy()
    # Offline suites cannot accidentally consume production credential variables.
    if suite.get('category') != 'live':
        for key in list(env):
            if 'TOKEN' in key.upper() or 'SECRET' in key.upper() or 'API_KEY' in key.upper(): env.pop(key)
    env.update(BOT_CENTER_TEST_OUTPUT=str(output), BOT_CENTER_TEST_ROOT=str(output / 'isolated'), FRENCH_NOTES_CHECKS=str(output),
               FRENCH_NOTES_SPEECH_CHECKS=str(output), PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env.update({key: value.format_map(mapping) for key, value in suite.get('env', {}).items()})
    if suite.get('category') != 'live':
        temporary = output / 'temp'; temporary.mkdir(exist_ok=True)
        env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    atomic_json(output / 'inputs.json', fingerprint)
    try:
        with reserve(root, suite.get('resources', [])):
            cwd = Path(suite.get('working_directory', '{root}').format_map(mapping))
            cwd.mkdir(parents=True, exist_ok=True)
            result = execute(command, cwd, env, output, suite.get('timeout_seconds', 180))
        outcome = 'Passed' if result['exit_code'] == 0 and not result['timed_out'] else 'Failed'
        return {**base, **result, 'command': command, 'outcome': outcome, 'duration_seconds': round(time.time() - started, 3)}
    except (OSError, RuntimeError) as error:
        return {**base, 'command': command, 'outcome': 'Blocked', 'reason': redact(error), 'duration_seconds': round(time.time() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT); parser.add_argument('--list', action='store_true')
    parser.add_argument('--suite', action='append', default=[]); parser.add_argument('--profile', action='append', default=[])
    parser.add_argument('--changed-path', action='append', default=[]); parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--reuse', action='store_true', help='Reuse matching successful evidence, explicitly labeled')
    args = parser.parse_args(argv); root = args.root.resolve()
    catalog = load_catalog(root)
    if args.list:
        print(json.dumps({'schema': 1, 'suites': catalog}, indent=2)); return 0
    profiles = args.profile or ([] if args.suite else ['smoke'])
    selected = select_suites(catalog, args.suite, profiles, args.changed_path)
    if not selected: raise ValueError('No suites match this selection.')
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    run_dir = root / 'work/tests' / run_id; run_dir.mkdir(parents=True)
    index = retained_index(root); environments = {}; results = []
    for suite in selected:
        tag = suite['environment']
        if tag not in environments: environments[tag] = environment_fingerprint(resolve_environment(root, tag))
        fingerprint = input_fingerprint(root, suite, environments[tag])
        reusable = None
        if args.reuse and not args.fresh:
            for run in reversed(index['runs']):
                for old in run.get('results', []):
                    if old['suite'] == suite['id'] and old.get('fingerprint') == fingerprint['sha256'] and old['outcome'] == 'Passed' and not old.get('bulk_expired'):
                        reusable = old
                if reusable: break
        if reusable:
            result = {**reusable, 'outcome': 'Reused evidence', 'original_outcome': 'Passed', 'original_output': reusable['output'], 'reused_at': time.time()}
        else:
            result = run_suite(root, suite, run_dir, fingerprint, live='live' in profiles)
        results.append(result)
        print(suite['id'] + ': ' + result['outcome'], flush=True)
    summary = {'schema': 1, 'id': run_id, 'created': time.time(), 'command': [str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])],
               'profiles': profiles, 'selection': [s['id'] for s in selected], 'results': results,
               'counts': {outcome: sum(result['outcome'] == outcome for result in results) for outcome in sorted(OUTCOMES)},
               'duration_seconds': round(sum(result.get('duration_seconds', 0) for result in results), 3)}
    summary['logical_bytes'] = sum(path.stat().st_size for path in run_dir.rglob('*') if path.is_file())
    atomic_json(run_dir / 'summary.json', summary)
    with reserve(root, ['test-evidence-index']):
        index = retained_index(root); index['runs'].append(summary); atomic_json(root / 'work/tests/index.json', index)
    atomic_json(root / 'work/tests/latest.json', {'run_id': run_id, 'summary': (run_dir / 'summary.json').relative_to(root).as_posix()})
    if 'acceptance' in profiles and all(result['outcome'] in ('Passed', 'Not applicable') for result in results):
        atomic_json(root / 'work/tests/latest-acceptance.json', {'run_id': run_id, 'summary': (run_dir / 'summary.json').relative_to(root).as_posix(), 'pin': True})
    print(json.dumps({'run_id': run_id, 'counts': summary['counts'], 'summary': str(run_dir / 'summary.json'), 'logical_bytes': summary['logical_bytes']}))
    return 1 if any(result['outcome'] in ('Failed', 'Blocked') for result in results) else 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(json.dumps({'outcome': 'Blocked', 'error': redact(error)})); raise SystemExit(2)
