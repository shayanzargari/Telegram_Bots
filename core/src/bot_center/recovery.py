"""Consistent encrypted captures and journaled, stopped recovery.

No dashboard, supervisor or application import is required for offline recovery.
The caller owns quiescence; the service owns the installation maintenance gate.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
import uuid

SCHEMA = 1
MAX_FRAME = 4 * 1024 * 1024 + 64 * 1024
TOKEN = re.compile(r'\d{5,}:[A-Za-z0-9_-]{20,}')


class RecoveryError(RuntimeError):
    pass


def redact(value):
    return TOKEN.sub('[REDACTED]', str(value))


def replace_with_retry(source, destination):
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(.01 * 2 ** attempt)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def dpapi(value, decrypt=False):
    if os.name != 'nt':
        raise RecoveryError('Recovery credentials require the original Windows user profile.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RecoveryError('Windows could not decrypt protected recovery material.' if decrypt else 'Windows recovery encryption failed.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        ctypes.windll.kernel32.LocalFree(target.data)


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        sid = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True).strip().split(',')[-1].strip('"')
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + sid + ':(OI)(CI)F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise RecoveryError('Could not apply private recovery directory permissions.')
    else:
        path.chmod(0o700)


def regular_files(path):
    path = Path(path)
    if path.is_file():
        if path.is_symlink():
            raise RecoveryError('Recovery does not traverse file links.')
        yield path
        return
    for directory, dirs, names in os.walk(path, followlinks=False):
        for name in list(dirs):
            item = Path(directory) / name
            if item.is_symlink() or getattr(item.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                raise RecoveryError('Recovery does not traverse directory links.')
        dirs.sort()
        for name in sorted(names):
            item = Path(directory) / name
            if item.is_symlink():
                raise RecoveryError('Recovery does not traverse file links.')
            yield item


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise RecoveryError('Invalid recovery member path.')
    path = Path(value)
    if path.is_absolute() or ':' in value or any(part in ('', '.', '..') for part in value.split('/')):
        raise RecoveryError('Invalid recovery member path.')
    return path


def contained(base, value):
    base = Path(base).resolve()
    candidate = (base / safe_relative(value)).resolve()
    if base not in candidate.parents:
        raise RecoveryError('Recovery path escapes its declared root.')
    return candidate


def seal(source, target):
    with Path(source).open('rb') as incoming, Path(target).open('xb') as outgoing:
        for chunk in iter(lambda: incoming.read(4 * 1024 * 1024), b''):
            protected = dpapi(chunk)
            outgoing.write(struct.pack('<I', len(protected)))
            outgoing.write(protected)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def unseal(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Path(source).open('rb') as incoming, target.open('xb') as outgoing:
        while header := incoming.read(4):
            if len(header) != 4:
                raise RecoveryError('Truncated protected recovery header.')
            length = struct.unpack('<I', header)[0]
            if not 0 < length <= MAX_FRAME:
                raise RecoveryError('Invalid protected recovery frame.')
            protected = incoming.read(length)
            if len(protected) != length:
                raise RecoveryError('Truncated protected recovery member.')
            outgoing.write(dpapi(protected, True))
        outgoing.flush()
        os.fsync(outgoing.fileno())


def sqlite_semantics(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.text_factory = bytes
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0].decode()
        if integrity != 'ok':
            raise RecoveryError('SQLite integrity check failed.')
        tables = [row[0].decode() for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result = {}
        for table in tables:
            columns = [row[1].decode() for row in connection.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')]
            rows = connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
            encoded = sorted(json.dumps(row, ensure_ascii=True, default=lambda value: {'bytes': value.hex()}) for row in rows)
            result[table] = {'columns': columns, 'count': len(rows), 'sha256': hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
        return {'integrity': integrity, 'tables': result}
    finally:
        connection.close()


def sqlite_snapshot(source, destination, deadline_seconds=30):
    source = Path(source).resolve()
    incoming = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    outgoing = sqlite3.connect(destination)
    try:
        deadline = time.monotonic() + deadline_seconds
        def progress(status, remaining, total):
            if time.monotonic() >= deadline:
                raise RecoveryError('SQLite snapshot could not finish within its reservation deadline; source state was left unchanged.')
        incoming.backup(outgoing, pages=128, progress=progress, sleep=.05)
        outgoing.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        outgoing.commit()
    finally:
        outgoing.close()
        incoming.close()
    return sqlite_semantics(destination)


@contextmanager
def maintenance_gate(root):
    root = Path(root).resolve()
    path = root / 'work/leases/recovery.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    stream.seek(0)
    stream.write(b'0')
    stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RecoveryError('Another migration, capture or restore owns this installation.') from None
    try:
        yield
    finally:
        stream.close()


@contextmanager
def offline_exclusive(root, allow_missing_state=False):
    """Native interop with core singleton and every canonical application lease.

    Kept self-contained so recovery still works before supervisor imports or a
    wheel repair. A --quiesced assertion never bypasses these actual handles.
    """
    root = Path(root).resolve()
    if not (root / 'data').is_dir() and not allow_missing_state:
        raise RecoveryError('Expected installation state is missing.')
    if os.name != 'nt':
        raise RecoveryError('Exclusive installation recovery requires native Windows.')
    sid = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True, creationflags=0x08000000).strip().split(',')[-1].strip('"')
    directory = Path(os.environ['LOCALAPPDATA']) / 'Bot Control Center Locks' / sid
    if not directory.exists():
        private_directory(directory)
    singleton = directory / (hashlib.sha256(('core:' + str(root).casefold()).encode()).hexdigest() + '.lock')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        paths = [singleton]
        if (root / 'data').is_dir():
            paths.append(root / 'data/maintenance.lock')
        for path in paths:
            handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 4, 0x80, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise RecoveryError('Core, reader, editor or maintenance work is active. Close it normally before exclusive offline recovery.')
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


class Journal:
    """Recoverable publication of config/launchers and generation.

    The open journal is a durable startup barrier. Callers retain the maintenance
    gate across staging, publication and verification. Rollback refuses to erase
    subsequent accepted work unless a new protected preservation set is supplied.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'config/activation-journal.json'

    def read(self):
        return json.loads(self.path.read_text('utf-8')) if self.path.exists() else None

    def begin(self, kind, source_generation, target_generation, publication_paths=()):
        old = self.read()
        if old and old['phase'] not in ('complete', 'rolled_back'):
            raise RecoveryError('An unfinished activation requires recovery inspection first.')
        operation_id = uuid.uuid4().hex
        snapshots = self.root / 'backups/journals' / operation_id
        private_directory(snapshots)
        publications = []
        for index, value in enumerate(publication_paths):
            path = Path(value)
            if not path.is_absolute():
                path = contained(self.root, path.as_posix())
            path = path.resolve()
            if path.is_dir() or path.is_symlink():
                raise RecoveryError('Journal publication targets must be ordinary files.')
            record = {'path': str(path), 'existed': path.exists()}
            if path.exists():
                record.update(member=f'{index:06d}.dpapi', sha256=sha256(path))
                seal(path, snapshots / record['member'])
            publications.append(record)
        record = {'schema': SCHEMA, 'operation_id': operation_id, 'kind': kind, 'source_generation': source_generation,
                  'target_generation': target_generation, 'phase': 'prepared', 'validated': False,
                  'started': time.time(), 'updated': time.time(), 'publications': publications,
                  'snapshot_directory': str(snapshots), 'checkpoints': []}
        atomic_json(self.path, record)
        return record

    def checkpoint(self, phase, **fields):
        record = self.read()
        if not record or record['phase'] in ('complete', 'rolled_back'):
            raise RecoveryError('No active journal transaction.')
        record.update(fields)
        record.update(phase=phase, updated=time.time())
        record['checkpoints'].append({'phase': phase, 'at': record['updated']})
        atomic_json(self.path, record)
        return record

    def publish_generation(self, config_relative='config/installation.json', generation=None):
        record = self.read()
        if not record or not record.get('validated'):
            raise RecoveryError('Generation publication requires recorded validation.')
        path = contained(self.root, config_relative)
        configuration = json.loads(path.read_text('utf-8')) if path.exists() else {'schema': 1}
        configuration['generation'] = generation or record['target_generation']
        self.checkpoint('before_generation_switch', generation_configuration=config_relative)
        atomic_json(path, configuration)
        self.checkpoint('generation_switched')
        return configuration

    def complete(self, **fields):
        record = self.read()
        if not record or not record.get('validated'):
            raise RecoveryError('Unvalidated activation cannot complete.')
        if record.get('generation_configuration'):
            current = json.loads(contained(self.root, record['generation_configuration']).read_text('utf-8'))
            if current.get('generation') != record['target_generation']:
                raise RecoveryError('Active configuration disagrees with the journal generation.')
        return self.checkpoint('complete', completed=time.time(), **fields)

    def rollback(self, current_preservation=None):
        record = self.read()
        if not record:
            raise RecoveryError('There is no activation to roll back.')
        if record.get('accepted_new_work') and not current_preservation:
            raise RecoveryError('Preserve current state before rolling back newer accepted work.')
        if record.get('kind') == 'restore':
            preservation = current_preservation or record.get('current_preservation')
            if not preservation:
                raise RecoveryError('Interrupted restore lacks its protected previous-state set.')
            service = RecoveryService(self.root)
            protected, manifest = service._manifest(preservation)
            if manifest.get('status') != 'Verified':
                raise RecoveryError('Previous-state recovery material is not Verified.')
            service.verify(protected)
            stage = self.root / 'work/recovery' / ('rollback-' + uuid.uuid4().hex)
            private_directory(stage)
            service._restore_copy(protected, manifest, stage)
            normalize_restored_core(stage)
            previous = self.root / 'work/recovery' / ('rollback-displaced-' + uuid.uuid4().hex)
            private_directory(previous)
            replace_state_units(self.root, stage, previous, self)
        snapshot = Path(record['snapshot_directory'])
        for publication in reversed(record['publications']):
            path = Path(publication['path'])
            if publication['existed']:
                temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.restore')
                unseal(snapshot / publication['member'], temporary)
                if sha256(temporary) != publication['sha256']:
                    raise RecoveryError('Journal rollback verification failed.')
                os.replace(temporary, path)
            else:
                path.unlink(missing_ok=True)
        return self.checkpoint('rolled_back', rollback_preservation=current_preservation)


class RecoveryService:
    def __init__(self, root, command_service=None):
        self.root = Path(root).resolve()
        self.command_service = command_service

    def list_sets(self):
        result = []
        base = self.root / 'backups'
        if not base.exists():
            return result
        for path in sorted(base.iterdir(), reverse=True):
            manifest = path / 'manifest.json'
            if manifest.is_file():
                record = json.loads(manifest.read_text('utf-8'))
                result.append({'path': str(path), 'created': record.get('created'), 'status': record.get('status', 'Verified' if record.get('verified') else 'Unverified'),
                               'files': len(record.get('files', [])), 'kind': record.get('kind', 'pre-migration')})
        return result

    def build_identity(self):
        report = {'schema': 1, 'files': {}}
        for parent in ('config', 'work/builds', 'work/build', 'tools/environments'):
            directory = self.root / parent
            if not directory.exists():
                continue
            for path in regular_files(directory):
                if path.suffix in ('.whl', '.lock') or path.name in ('build-identity.json', 'installation-report.json', 'requirements.txt'):
                    report['files'][path.relative_to(self.root).as_posix()] = sha256(path)
        installation = self.root / 'config/installation.json'
        if installation.exists():
            report['installation'] = json.loads(installation.read_text('utf-8'))
        return report

    def stage_capture(self, *, quiesced=False, build_identity=None, destination=None):
        if not quiesced:
            raise RecoveryError('Pause dispatch and writable commands, stop readers, and close the French editor before capture.')
        if not (self.root / 'data').is_dir():
            raise RecoveryError('Expected installation state is missing; capture did not initialize it.')
        capture_id = 'recovery-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        destination = Path(destination).resolve() if destination else self.root / 'backups' / capture_id
        if destination.exists():
            raise RecoveryError('A recovery destination already exists.')
        private_directory(destination)
        stage = self.root / 'work/recovery' / capture_id
        private_directory(stage)
        manifest = {'schema': SCHEMA, 'kind': 'installation', 'id': capture_id, 'created': time.time(), 'status': 'Capturing',
                    'encryption': 'Windows current-user DPAPI frames', 'build_identity': build_identity or self.build_identity(), 'files': [], 'directories': [],
                    'retention': 'pinned-recovery', 'external_prerequisites': ['Windows user profile for DPAPI', 'Python 3.11', 'pinned wheels/dependency inputs']}
        atomic_json(destination / 'manifest.json', manifest)
        try:
            with maintenance_gate(self.root):
                sources = [self.root / 'data', self.root / 'config', self.root / '.secrets']
                inputs = manifest['build_identity'].get('files', {})
                sources += [contained(self.root, value) for value in inputs]
                seen = set()
                observed_sources = []
                for base in sources:
                    if not base.exists():
                        continue
                    if base.is_symlink() or getattr(base.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                        raise RecoveryError('Capture roots must not be links or junctions.')
                    if base.is_dir():
                        manifest['directories'].append(base.relative_to(self.root).as_posix())
                        for directory in base.rglob('*'):
                            if directory.is_dir():
                                if directory.is_symlink() or getattr(directory.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                                    raise RecoveryError('Capture does not traverse linked directories.')
                                manifest['directories'].append(directory.relative_to(self.root).as_posix())
                    for path in regular_files(base):
                        relative = path.relative_to(self.root).as_posix()
                        if relative in seen or path.name.endswith(('-wal', '-shm', '.lock', '.request')) or path.name in ('activation-journal.json', 'server.json'):
                            continue
                        seen.add(relative)
                        temporary = stage / f'{len(manifest["files"]):08d}.snapshot'
                        semantic = None
                        with path.open('rb') as probe:
                            header = probe.read(16)
                        if path.suffix in ('.db', '.sqlite', '.sqlite3') or header == b'SQLite format 3\0':
                            semantic = sqlite_snapshot(path, temporary)
                            observed_sources.append((path, semantic, True))
                        else:
                            before = sha256(path)
                            shutil.copyfile(path, temporary)
                            if sha256(temporary) != before or sha256(path) != before:
                                raise RecoveryError('An application asset changed during capture. Preserve the new content and retry after closing its writer.')
                            observed_sources.append((path, before, False))
                        item = {'relative': relative, 'bytes': temporary.stat().st_size, 'sha256': sha256(temporary), 'member': f'{len(manifest["files"]):08d}.dpapi'}
                        if semantic:
                            item['sqlite'] = semantic
                        seal(temporary, destination / item['member'])
                        temporary.unlink()
                        manifest['files'].append(item)
                # A noncooperating manual editor or credential writer must not
                # silently create a mixed capture merely because official apps
                # honored their leases. Recheck every captured source before
                # publishing the capture boundary.
                for path, expected, is_database in observed_sources:
                    if not path.is_file():
                        raise RecoveryError('A captured source disappeared before publication; capture remains unverified.')
                    current = sqlite_semantics(path) if is_database else sha256(path)
                    if current != expected:
                        raise RecoveryError('A source changed while the installation was reserved; capture remains unverified.')
                manifest.update(status='Captured', captured=time.time())
                atomic_json(destination / 'manifest.json', manifest)
            return {'path': str(destination), 'status': 'Captured', 'files': len(manifest['files'])}
        except BaseException as error:
            manifest.update(status='Failed', failure=redact(type(error).__name__), failed=time.time())
            atomic_json(destination / 'manifest.json', manifest)
            raise

    def _manifest(self, path):
        path = Path(path).resolve()
        manifest = json.loads((path / 'manifest.json').read_text('utf-8'))
        if manifest.get('schema') != SCHEMA or manifest.get('kind') != 'installation' or manifest.get('status') not in ('Captured', 'Verified'):
            raise RecoveryError('Unsupported or incomplete recovery set.')
        relatives = set()
        for item in manifest.get('files', []):
            relative = safe_relative(item['relative']).as_posix()
            if relative.casefold() in relatives or not re.fullmatch(r'\d{8}\.dpapi', item['member']):
                raise RecoveryError('Invalid or colliding recovery member.')
            if relative.split('/')[0] not in ('data', 'config', '.secrets', 'work', 'tools'):
                raise RecoveryError('Recovery member has an undeclared write scope.')
            relatives.add(relative.casefold())
        if not manifest.get('files'):
            raise RecoveryError('Empty recovery set.')
        for directory in manifest.get('directories', []):
            safe_relative(directory)
            if directory.split('/')[0] not in ('data', 'config', '.secrets', 'work', 'tools'):
                raise RecoveryError('Recovery directory has an undeclared write scope.')
        return path, manifest

    def _check_compatibility(self, manifest, current_build):
        if current_build is None:
            return
        # Explicit exact comparison is conservative: reject before touching the installation.
        if manifest['build_identity'] != current_build:
            raise RecoveryError('Recovery build/schema inputs differ; use its pinned compatible build or a reviewed migration.')

    def _restore_copy(self, path, manifest, destination):
        destination = Path(destination)
        for directory in manifest.get('directories', []):
            contained(destination, directory).mkdir(parents=True, exist_ok=True)
        for item in manifest['files']:
            target = contained(destination, item['relative'])
            unseal(path / item['member'], target)
            if target.stat().st_size != item['bytes'] or sha256(target) != item['sha256']:
                raise RecoveryError('Recovery member hash verification failed.')
            if item.get('sqlite') and sqlite_semantics(target) != item['sqlite']:
                raise RecoveryError('Recovery database semantic verification failed.')
        return destination

    def _application_preflight(self, restored_root):
        registry = Path(restored_root) / 'config/registry.json'
        if not registry.exists():
            return []
        record = json.loads(registry.read_text('utf-8'))
        bots = record.get('bots', [])
        if isinstance(bots, dict):
            bots = list(bots.values())
        results = []
        path_keys = ('data_dir', 'notes_dir', 'editor_state', 'editor_state_dir', 'history_path', 'log_dir', 'working_dir', 'working_directory', 'cwd')
        validator_code = """import importlib,json,sys,threading
manifest=json.loads(sys.stdin.read())
class Client:
    stop=threading.Event()
    bootstrap={'payload':{},'job':manifest['restore_validator']['job']}
    def emit(self,*args,**kwargs):pass
    def progress(self,*args,**kwargs):pass
    def ready(self,*args,**kwargs):pass
result=importlib.import_module(manifest['restore_validator']['module']).run_job(Client(),manifest,manifest['restore_validator']['job'],{})
if result not in (None,0):raise RuntimeError('Application restore preflight did not complete')
print('Validated')
"""
        for declaration in bots:
            if isinstance(declaration, str):
                declaration = json.loads(contained(restored_root, declaration).read_text('utf-8'))
            elif isinstance(declaration, dict) and 'manifest' in declaration:
                declaration = json.loads(contained(restored_root, declaration['manifest']).read_text('utf-8'))
            if not isinstance(declaration, dict) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', declaration.get('id', '')) or TOKEN.search(json.dumps(declaration)):
                raise RecoveryError('Captured application declaration is malformed or includes credential-like material.')
            validator = declaration.get('restore_validator')
            if not validator:
                continue
            if not isinstance(validator, dict) or not re.fullmatch(r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*', validator.get('module', '')) or validator.get('job') != 'integrity':
                raise RecoveryError('Unsupported application restore validator.')
            rebased = dict(declaration)
            for key in path_keys:
                if key not in rebased or not rebased[key]:
                    continue
                source = Path(rebased[key])
                if source.is_absolute():
                    try:
                        relative = source.resolve().relative_to(self.root)
                    except ValueError:
                        raise RecoveryError('Application restore path escapes the installation.') from None
                else:
                    relative = safe_relative(source.as_posix())
                rebased[key] = str(Path(restored_root) / relative)
            python = declaration.get('python')
            if not python:
                environment = declaration.get('environment', declaration['id'])
                if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', environment):
                    raise RecoveryError('Invalid restore-validation environment.')
                python = str(self.root / '.venvs' / environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
            python = Path(python)
            python = (self.root / python).resolve() if not python.is_absolute() else python.resolve()
            if self.root / '.venvs' not in python.parents or not python.is_file():
                raise RecoveryError('Pinned application validator environment is unavailable.')
            environment = os.environ.copy()
            for key in list(environment):
                if 'TOKEN' in key.upper() or 'SECRET' in key.upper() or 'API_KEY' in key.upper():
                    environment.pop(key)
            environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', BOT_CENTER_ROOT=str(restored_root), BOT_CENTER_RECOVERY_VALIDATION='1')
            result = subprocess.run([str(python), '-c', validator_code], input=json.dumps(rebased), capture_output=True, text=True,
                                    timeout=90, cwd=str(restored_root), env=environment,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                # Application errors can carry source text or credentials. Keep
                # the typed failure without echoing child diagnostics.
                raise RecoveryError('Application restore preflight failed for ' + str(declaration.get('id', 'registered bot')) + '.')
            results.append({'bot': declaration['id'], 'validator': validator, 'status': 'Passed'})
        return results

    def verify(self, set_path, current_build=None):
        path, manifest = self._manifest(set_path)
        self._check_compatibility(manifest, current_build)
        stage = self.root / 'work/recovery' / ('verify-' + uuid.uuid4().hex)
        private_directory(stage)
        try:
            self._restore_copy(path, manifest, stage)
            application_checks = self._application_preflight(stage)
            # Every declared file, including drafts/history/audio and wheels, was
            # reconstructed and hashed above. SQLite snapshots were independently opened.
            report = {'schema': 1, 'status': 'Passed', 'files': len(manifest['files']),
                      'bytes': sum(item['bytes'] for item in manifest['files']), 'database_count': sum(bool(item.get('sqlite')) for item in manifest['files']),
                      'verified_at': time.time(), 'isolated_preflight': True, 'build_identity': manifest['build_identity']}
            report['application_checks'] = application_checks
            atomic_json(path / 'verification.json', report)
            manifest.update(status='Verified', verified=True, verification='verification.json', verified_at=report['verified_at'])
            atomic_json(path / 'manifest.json', manifest)
            atomic_json(self.root / 'backups/latest-verified.json', {'path': str(path), 'manifest_sha256': sha256(path / 'manifest.json'), 'verified_at': report['verified_at']})
            return {'path': str(path), **report}
        except BaseException as error:
            atomic_json(path / ('verification-failed-' + uuid.uuid4().hex[:8] + '.json'), {'status': 'Failed', 'failure': type(error).__name__, 'at': time.time()})
            raise
        finally:
            # Only this method's explicitly created, private isolated copy is removed.
            if stage.parent.resolve() == (self.root / 'work/recovery').resolve() and not stage.is_symlink():
                shutil.rmtree(stage)

    def capture(self, **kwargs):
        result = self.stage_capture(**kwargs)
        return self.verify(result['path'])

    def restore(self, set_path, *, quiesced=False, current_build=None):
        if not quiesced:
            raise RecoveryError('Exclusive recovery requires closed applications and core database connections.')
        path, manifest = self._manifest(set_path)
        if manifest.get('status') != 'Verified':
            raise RecoveryError('Restore requires a Verified recovery set.')
        self._check_compatibility(manifest, current_build or self.build_identity())
        self.verify(path, current_build or self.build_identity())
        # Protect current accepted work before any replacement.
        preservation = self.capture(quiesced=True, build_identity=current_build or self.build_identity())
        restore_id = 'restore-' + uuid.uuid4().hex
        stage = self.root / 'work/recovery' / restore_id
        private_directory(stage)
        self._restore_copy(path, manifest, stage)
        journal = Journal(self.root)
        existing = self.root / 'config/installation.json'
        current_generation = json.loads(existing.read_text('utf-8')).get('generation') if existing.exists() else None
        publications = {p for p in (self.root / 'config').rglob('*') if p.is_file() and p.name != 'activation-journal.json'}
        publications.update(self.root / p.relative_to(stage) for p in (stage / 'config').rglob('*') if p.is_file() and p.name != 'activation-journal.json')
        with maintenance_gate(self.root):
            journal.begin('restore', current_generation, restore_id, publication_paths=sorted(publications))
            journal.checkpoint('validated', validated=True, current_preservation=preservation['path'], recovery_set=str(path), replacement_directory=str(stage))
            normalize_restored_core(stage)
            previous = self.root / 'work/recovery' / (restore_id + '-previous')
            private_directory(previous)
            replace_state_units(self.root, stage, previous, journal)
            for configuration in (stage / 'config').rglob('*'):
                if configuration.is_file() and configuration.name != 'activation-journal.json':
                    target = self.root / configuration.relative_to(stage)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(configuration, target)
            journal.publish_generation(generation=restore_id)
            journal.complete(recovery_mode='stopped-no-replay', restored_at=time.time())
        return {'status': 'Completed', 'generation': restore_id, 'preserved_current': preservation['path'], 'bots': 'stopped', 'schedules': 'disabled'}


def replace_state_units(root, stage, previous, journal):
    """Keep the held data/maintenance.lock in place while switching child units."""
    root, stage, previous = Path(root), Path(stage), Path(previous)
    units = [path.relative_to(stage) for path in (stage / 'data').iterdir() if path.name != 'maintenance.lock']
    if (stage / '.secrets').exists():
        units.append(Path('.secrets'))
    for relative in sorted(units):
        incoming, target, displaced = stage / relative, root / relative, previous / relative
        if target.is_symlink() or (target.exists() and getattr(target.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400):
            raise RecoveryError('Restore target changed into a link; activation remains blocked.')
        displaced.parent.mkdir(parents=True, exist_ok=True)
        journal.checkpoint('before_replace', replacing=relative.as_posix(), rollback_directory=str(previous))
        if target.exists():
            os.replace(target, displaced)
        os.replace(incoming, target)
        journal.checkpoint('replaced', replaced=relative.as_posix())


def normalize_restored_core(root):
    """Remove runnable intent/leases in the isolated copy before activation."""
    root = Path(root)
    for path in (root / 'data/core').glob('*.sqlite*') if (root / 'data/core').exists() else ():
        if path.name.endswith(('-wal', '-shm')):
            continue
        connection = sqlite3.connect(path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                columns = {row[1] for row in connection.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}
                updates = {}
                if table == 'bots':
                    for key in ('desired', 'desired_state'):
                        if key in columns:
                            updates[key] = 'stopped'
                    for key in ('external_hold', 'hold'):
                        if key in columns:
                            updates[key] = 0
                    for key in ('attention', 'run_id', 'retry_at'):
                        if key in columns:
                            updates[key] = None
                    for key in ('observed', 'observed_state'):
                        if key in columns:
                            updates[key] = 'Stopped'
                elif table == 'schedules':
                    if 'enabled' in columns:
                        updates['enabled'] = 0
                    for key in ('boundary', 'activation_boundary'):
                        if key in columns:
                            connection.execute('UPDATE schedules SET "' + key + '"=MAX(COALESCE("' + key + '",0),?)', (time.time(),))
                    if 'revision' in columns:
                        connection.execute('UPDATE schedules SET revision=revision+1')
                elif table == 'scheduler_runtime':
                    connection.execute('DELETE FROM scheduler_runtime')
                elif table in ('leases', 'sessions', 'processes', 'ownership'):
                    connection.execute('DELETE FROM "' + table + '"')
                elif table == 'operations' and 'status' in columns:
                    connection.execute("UPDATE operations SET status='Indeterminate' WHERE lower(status) IN ('accepted','running','pending','active')")
                elif table in ('occurrences', 'schedule_occurrences') and 'status' in columns:
                    reason = ",reason='Restored; no replay'" if 'reason' in columns else ''
                    connection.execute("UPDATE " + table + " SET status='Indeterminate'" + reason + " WHERE lower(status) IN ('claimed','accepted','running')")
                if updates:
                    fields = ','.join('"' + key + '"=?' for key in updates)
                    connection.execute('UPDATE "' + table + '" SET ' + fields, tuple(updates.values()))
            connection.commit()
        finally:
            connection.close()
    atomic_json(root / 'data/core/recovery-mode.json', {'schema': 1, 'mode': 'stopped', 'no_replay_boundary': time.time(), 'explicit_resumption_required': True})


def cli(argv=None):
    parser = argparse.ArgumentParser(description='Protected manual recovery; never accepts credentials as arguments')
    parser.add_argument('--root', type=Path, required=True)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('list')
    verify = commands.add_parser('verify'); verify.add_argument('set', type=Path)
    capture = commands.add_parser('capture'); capture.add_argument('--quiesced', action='store_true'); capture.add_argument('--destination', type=Path)
    restore = commands.add_parser('restore'); restore.add_argument('set', type=Path); restore.add_argument('--quiesced', action='store_true')
    commands.add_parser('journal')
    rollback = commands.add_parser('rollback-publication'); rollback.add_argument('--current-preservation')
    args = parser.parse_args(argv)
    service = RecoveryService(args.root)
    if args.action == 'list':
        result = service.list_sets()
    elif args.action == 'verify':
        result = service.verify(args.set)
    elif args.action == 'capture':
        with offline_exclusive(args.root):
            captured = service.stage_capture(quiesced=args.quiesced, destination=args.destination)
        result = service.verify(captured['path'])
    elif args.action == 'restore':
        with offline_exclusive(args.root):
            result = service.restore(args.set, quiesced=args.quiesced)
    elif args.action == 'journal':
        result = Journal(args.root).read()
    else:
        with offline_exclusive(args.root, allow_missing_state=True):
            result = Journal(args.root).rollback(args.current_preservation)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        cli()
    except (RecoveryError, OSError, ValueError) as error:
        print(json.dumps({'status': 'Failed', 'error': redact(error)}))
        raise SystemExit(1)
