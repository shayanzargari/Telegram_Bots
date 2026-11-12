"""Isolated recovery safety, corruption, compatibility and interruption scenarios."""
from __future__ import annotations
import errno
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bot_center import recovery


@unittest.skipUnless(os.name == 'nt', 'Current-user Windows DPAPI is required')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='center-recovery-fixture-')
        self.root = Path(self.temporary.name).resolve()
        for value in ('data/home', 'data/french/notes', 'data/french/reader', 'data/french/editor', 'data/core', 'config', '.secrets'):
            (self.root / value).mkdir(parents=True)
        recovery.atomic_json(self.root / 'config/installation.json', {'schema': 1, 'generation': 'A'})
        recovery.atomic_json(self.root / 'config/registry.json', {'schema': 1, 'bots': []})
        (self.root / 'data/french/notes/Indicatif.txt').write_bytes('## Présent\nBonjour.\n'.encode())
        recovery.atomic_json(self.root / 'data/french/editor/editor-drafts.json', {'draft': 'An unsaved draft'})
        recovery.atomic_json(self.root / 'data/french/editor/history.json', [{'id': 'stable-history'}])
        self.secret = '123456789:' + 'SyntheticCredentialCanary_1234567890'
        (self.root / '.secrets/fixture.dpapi').write_bytes(recovery.dpapi(self.secret.encode()))
        database = sqlite3.connect(self.root / 'data/home/tasks.sqlite3')
        database.executescript('PRAGMA journal_mode=WAL; CREATE TABLE tasks(id INTEGER PRIMARY KEY,title TEXT); INSERT INTO tasks VALUES(1,"First");')
        database.commit(); database.close()
        self.service = recovery.RecoveryService(self.root)
        self.identity = {'schema': 1, 'version': 'A', 'files': {}}

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self):
        return self.service.capture(quiesced=True, build_identity=self.identity)

    def test_capture_requires_explicit_quiescence_and_does_not_initialize_missing_state(self):
        with self.assertRaises(recovery.RecoveryError): self.service.stage_capture()
        other = self.root / 'missing'
        with self.assertRaises(recovery.RecoveryError): recovery.RecoveryService(other).stage_capture(quiesced=True)
        self.assertFalse((other / 'data').exists())

    def test_consistent_sqlite_snapshot_includes_committed_wal(self):
        connection = sqlite3.connect(self.root / 'data/home/tasks.sqlite3')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('INSERT INTO tasks VALUES(2,"In committed WAL")'); connection.commit()
        result = self.service.stage_capture(quiesced=True, build_identity=self.identity)
        manifest = json.loads((Path(result['path']) / 'manifest.json').read_text('utf-8'))
        database = next(item for item in manifest['files'] if item['relative'].endswith('tasks.sqlite3'))
        self.assertEqual(database['sqlite']['tables']['tasks']['count'], 2)
        self.assertFalse(any(item['relative'].endswith(('-wal', '-shm')) for item in manifest['files']))
        connection.close()
        self.assertEqual(self.service.verify(result['path'])['status'], 'Passed')

    def test_suffixless_browser_sqlite_includes_committed_wal(self):
        path = self.root / 'data/french/editor/window-profile/Default/History'
        path.parent.mkdir(parents=True)
        connection = sqlite3.connect(path)
        try:
            connection.executescript('PRAGMA journal_mode=WAL;PRAGMA wal_autocheckpoint=0;CREATE TABLE history(id INTEGER PRIMARY KEY);INSERT INTO history VALUES(1);')
            connection.commit()
            result = self.service.stage_capture(quiesced=True, build_identity=self.identity)
            manifest = json.loads((Path(result['path']) / 'manifest.json').read_text('utf-8'))
            database = next(item for item in manifest['files'] if item['relative'].endswith('/History'))
            self.assertEqual(database.get('sqlite', {}).get('tables', {}).get('history', {}).get('count'), 1)
            self.assertEqual(self.service.verify(result['path'])['status'], 'Passed')
        finally:
            connection.close()

    def test_unexpected_source_change_during_copy_blocks_capture_publication(self):
        path = self.root / 'data/french/notes/Indicatif.txt'
        original_copy = recovery.shutil.copyfile
        def concurrent_edit(source, destination, *args, **kwargs):
            result = original_copy(source, destination, *args, **kwargs)
            if Path(source).resolve() == path:
                path.write_bytes(b'Concurrent manual edit')
            return result
        with patch.object(recovery.shutil, 'copyfile', side_effect=concurrent_edit):
            with self.assertRaises(recovery.RecoveryError):
                self.service.stage_capture(quiesced=True, build_identity=self.identity)
        self.assertEqual(path.read_bytes(), b'Concurrent manual edit')
        self.assertFalse((self.root / 'backups/latest-verified.json').exists())

    def test_external_sqlite_lock_has_a_bounded_snapshot_deadline(self):
        source = self.root / 'data/home/locked.sqlite3'
        connection = sqlite3.connect(source)
        try:
            connection.execute('CREATE TABLE fixture(id INTEGER)'); connection.commit()
            connection.execute('BEGIN EXCLUSIVE')
            with self.assertRaisesRegex(recovery.RecoveryError, 'deadline'):
                recovery.sqlite_snapshot(source, self.root / 'work-deadline.sqlite3', deadline_seconds=.1)
        finally:
            connection.rollback(); connection.close()

    def test_application_validator_uses_installation_relative_python_from_unrelated_cwd(self):
        environment = self.root / '.venvs/fixture'
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)], check=True, capture_output=True, timeout=30)
        package = environment / 'Lib/site-packages/fixture_validator'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('', 'utf-8')
        (package / 'managed.py').write_text('from pathlib import Path\ndef run_job(client,manifest,job,payload):\n    assert job=="integrity"\n    assert Path(manifest["data_dir"]).is_absolute()\n    assert Path(manifest["data_dir"]).is_dir()\n    return 0\n', 'utf-8')
        recovery.atomic_json(self.root / 'config/registry.json', {'schema': 1, 'bots': [{
            'id': 'fixture', 'python': '.venvs/fixture/Scripts/python.exe', 'cwd': 'data/home',
            'data_dir': 'data/home', 'restore_validator': {'module': 'fixture_validator.managed', 'job': 'integrity'}}]})
        captured = self.service.stage_capture(quiesced=True, build_identity=self.identity)
        original = Path.cwd()
        unrelated = self.root / 'unrelated-cwd'; unrelated.mkdir()
        try:
            os.chdir(unrelated)
            result = self.service.verify(captured['path'])
        finally:
            os.chdir(original)
        self.assertEqual(result['application_checks'][0]['status'], 'Passed')

    def test_isolated_verification_can_run_after_production_resumes(self):
        result = self.service.stage_capture(quiesced=True, build_identity=self.identity)
        with closing(sqlite3.connect(self.root / 'data/home/tasks.sqlite3')) as connection:
            connection.execute('INSERT INTO tasks VALUES(2,"New accepted work")')
            connection.commit()
        (self.root / 'data/french/editor/editor-drafts.json').write_text('{"new":"draft"}', 'utf-8')
        self.assertEqual(self.service.verify(result['path'])['status'], 'Passed')
        with closing(sqlite3.connect(self.root / 'data/home/tasks.sqlite3')) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 2)

    def test_corrupt_set_rejected_without_replacing_last_verified_pointer(self):
        first = self.capture()
        pointer = (self.root / 'backups/latest-verified.json').read_bytes()
        second = self.service.stage_capture(quiesced=True, build_identity=self.identity)
        manifest = json.loads((Path(second['path']) / 'manifest.json').read_text('utf-8'))
        (Path(second['path']) / manifest['files'][0]['member']).write_bytes(b'corrupt')
        with self.assertRaises(recovery.RecoveryError): self.service.verify(second['path'])
        self.assertEqual((self.root / 'backups/latest-verified.json').read_bytes(), pointer)
        self.assertEqual(json.loads((Path(first['path']) / 'manifest.json').read_text('utf-8'))['status'], 'Verified')

    def test_failed_capture_keeps_last_verified_set(self):
        self.capture()
        pointer = (self.root / 'backups/latest-verified.json').read_bytes()
        with patch.object(recovery, 'seal', side_effect=OSError(errno.ENOSPC, 'fixture disk full')):
            with self.assertRaises(OSError): self.service.stage_capture(quiesced=True, build_identity=self.identity)
        self.assertEqual((self.root / 'backups/latest-verified.json').read_bytes(), pointer)
        manifests = [json.loads(path.read_text('utf-8')) for path in (self.root / 'backups').glob('*/manifest.json')]
        self.assertIn('Failed', [value['status'] for value in manifests])

    def test_incompatible_version_rejected_before_modifying_new_installation(self):
        result = self.capture()
        (self.root / 'data/french/notes/Indicatif.txt').write_bytes(b'Version B accepted content')
        before = (self.root / 'data/french/notes/Indicatif.txt').read_bytes()
        with self.assertRaises(recovery.RecoveryError):
            self.service.restore(result['path'], quiesced=True, current_build={'schema': 1, 'version': 'B', 'files': {}})
        self.assertEqual((self.root / 'data/french/notes/Indicatif.txt').read_bytes(), before)
        self.assertFalse((self.root / 'config/activation-journal.json').exists())

    def test_restore_preserves_current_work_and_resets_runnable_intent(self):
        core = sqlite3.connect(self.root / 'data/core/core.sqlite3')
        core.executescript('''
          CREATE TABLE bots(id TEXT PRIMARY KEY, enabled INTEGER,desired TEXT,external_hold INTEGER,observed TEXT,attention TEXT,run_id TEXT,retry_at REAL);
          INSERT INTO bots VALUES('home',1,'running',1,'Running','old attention','old-run',12);
          CREATE TABLE operations(id TEXT,status TEXT); INSERT INTO operations VALUES('pending','Accepted');
          CREATE TABLE schedules(id TEXT,enabled INTEGER,boundary REAL,revision INTEGER); INSERT INTO schedules VALUES('schedule',1,9999999999,1);
          CREATE TABLE schedule_occurrences(schedule_id TEXT,due_utc TEXT,status TEXT,reason TEXT); INSERT INTO schedule_occurrences VALUES('schedule','old','Claimed',NULL);
          CREATE TABLE scheduler_runtime(id INTEGER,last_wall REAL); INSERT INTO scheduler_runtime VALUES(1,0);
        '''); core.commit(); core.close()
        result = self.capture()
        (self.root / 'data/french/notes/Indicatif.txt').write_bytes(b'New work that must be protected')
        restored = self.service.restore(result['path'], quiesced=True, current_build=self.identity)
        self.assertEqual(restored['status'], 'Completed')
        self.assertTrue(Path(restored['preserved_current']).is_dir())
        with closing(sqlite3.connect(self.root / 'data/core/core.sqlite3')) as connection:
            bot = connection.execute('SELECT enabled,desired,external_hold,observed,attention,run_id,retry_at FROM bots').fetchone()
            self.assertEqual(bot, (1, 'stopped', 0, 'Stopped', None, None, None))
            self.assertEqual(connection.execute('SELECT status FROM operations').fetchone()[0], 'Indeterminate')
            self.assertEqual(connection.execute('SELECT enabled,boundary,revision FROM schedules').fetchone(), (0, 9999999999, 2))
            self.assertEqual(connection.execute('SELECT status FROM schedule_occurrences').fetchone()[0], 'Indeterminate')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM scheduler_runtime').fetchone()[0], 0)
        self.assertTrue(json.loads((self.root / 'data/core/recovery-mode.json').read_text('utf-8'))['explicit_resumption_required'])
        self.assertEqual(recovery.Journal(self.root).read()['phase'], 'complete')

    def test_token_canary_absent_from_unencrypted_recovery_members(self):
        result = self.capture()
        for path in Path(result['path']).iterdir():
            self.assertNotIn(self.secret.encode(), path.read_bytes())
        protected = (self.root / '.secrets/fixture.dpapi').read_bytes()
        self.assertEqual(recovery.dpapi(protected, True), self.secret.encode())

    def test_backup_path_traversal_and_colliding_names_are_rejected(self):
        result = self.capture(); path = Path(result['path']) / 'manifest.json'
        manifest = json.loads(path.read_text('utf-8'))
        manifest['files'][0]['relative'] = '../escape'
        recovery.atomic_json(path, manifest)
        with self.assertRaises(recovery.RecoveryError): self.service.verify(result['path'])
        self.assertFalse((self.root / 'escape').exists())

    def test_capture_reservation_prevents_a_second_owner(self):
        with recovery.maintenance_gate(self.root):
            with self.assertRaises(recovery.RecoveryError):
                with recovery.maintenance_gate(self.root): pass

    def test_offline_exclusive_gate_conflicts_with_real_core_and_editor_locks(self):
        from bot_center_runtime.locks import ResourceLock, FileLease, LockBusy
        with ResourceLock('core:' + str(self.root).casefold()):
            with self.assertRaises(recovery.RecoveryError):
                with recovery.offline_exclusive(self.root): pass
        with FileLease(self.root / 'data/maintenance.lock'):
            with self.assertRaises(recovery.RecoveryError):
                with recovery.offline_exclusive(self.root): pass
        with recovery.offline_exclusive(self.root):
            with self.assertRaises(LockBusy):
                with ResourceLock('core:' + str(self.root).casefold()): pass

    def test_captured_empty_directories_are_reconstructed(self):
        (self.root / 'data/french/editor/window-profile/empty-cache').mkdir(parents=True)
        result = self.capture()
        path, manifest = self.service._manifest(result['path'])
        stage = self.root / 'private-test-copy'; stage.mkdir()
        self.service._restore_copy(path, manifest, stage)
        self.assertTrue((stage / 'data/french/editor/window-profile/empty-cache').is_dir())

    def test_malformed_application_validator_cannot_publish_verified_set(self):
        recovery.atomic_json(self.root / 'config/registry.json', {'schema': 1, 'bots': [{'id': 'fixture', 'restore_validator': {'module': '../unsafe', 'job': 'integrity'}}]})
        result = self.service.stage_capture(quiesced=True, build_identity=self.identity)
        with self.assertRaises(recovery.RecoveryError): self.service.verify(result['path'])
        self.assertFalse((self.root / 'backups/latest-verified.json').exists())

    def test_offline_restore_keeps_lease_and_recovers_interrupted_directory_switch(self):
        result = self.capture()
        note = self.root / 'data/french/notes/Indicatif.txt'
        note.write_bytes(b'Accepted after the backup, before restore')
        original_replace = recovery.os.replace
        def crash_during_home_switch(source, destination):
            if Path(destination).resolve() == self.root / 'data/home' and Path(source).resolve() != self.root / 'data/home':
                raise OSError(errno.EIO, 'Isolated interruption after previous unit moved')
            return original_replace(source, destination)
        with recovery.offline_exclusive(self.root):
            with patch.object(recovery.os, 'replace', side_effect=crash_during_home_switch):
                with self.assertRaises(OSError):
                    self.service.restore(result['path'], quiesced=True, current_build=self.identity)
        reopened = recovery.Journal(self.root)
        self.assertNotIn(reopened.read()['phase'], ('complete', 'rolled_back'))
        self.assertTrue(Path(reopened.read()['current_preservation']).is_dir())
        with recovery.offline_exclusive(self.root):
            reopened.rollback()
        self.assertEqual(note.read_bytes(), b'Accepted after the backup, before restore')
        self.assertTrue((self.root / 'data/home/tasks.sqlite3').is_file())
        self.assertEqual(reopened.read()['phase'], 'rolled_back')
        self.assertTrue((self.root / 'data/maintenance.lock').exists())


@unittest.skipUnless(os.name == 'nt', 'Windows encrypted publication recovery')
class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='center-journal-fixture-')
        self.root = Path(self.temporary.name).resolve(); (self.root / 'config').mkdir()
        self.path = self.root / 'config/installation.json'
        recovery.atomic_json(self.path, {'generation': 'A', 'resolver': 'old-path'})
        self.shortcut = self.root / 'START.cmd'; self.shortcut.write_bytes(b'old launcher')
        self.journal = recovery.Journal(self.root)

    def tearDown(self): self.temporary.cleanup()

    def begin(self):
        return self.journal.begin('migration', 'A', 'B', ['config/installation.json', 'START.cmd', 'new.cmd'])

    def test_crash_before_generation_switch_blocks_reentry_and_rolls_back_publications(self):
        self.begin(); self.shortcut.write_bytes(b'new launcher'); (self.root / 'new.cmd').write_bytes(b'created')
        reopened = recovery.Journal(self.root)
        self.assertEqual(reopened.read()['phase'], 'prepared')
        with self.assertRaises(recovery.RecoveryError): reopened.begin('migration', 'A', 'B')
        reopened.rollback()
        self.assertEqual(self.shortcut.read_bytes(), b'old launcher')
        self.assertFalse((self.root / 'new.cmd').exists())
        self.assertEqual(reopened.read()['phase'], 'rolled_back')

    def test_crash_after_generation_switch_before_shortcut_publication_retains_barrier(self):
        self.begin(); self.journal.checkpoint('validated', validated=True); self.journal.publish_generation()
        reopened = recovery.Journal(self.root)
        self.assertEqual(reopened.read()['phase'], 'generation_switched')
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'B')
        reopened.rollback()
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'A')
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['resolver'], 'old-path')

    def test_unvalidated_activation_cannot_publish_or_complete(self):
        self.begin()
        with self.assertRaises(recovery.RecoveryError): self.journal.publish_generation()
        with self.assertRaises(recovery.RecoveryError): self.journal.complete()
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'A')

    def test_generation_write_failure_does_not_report_completion(self):
        self.begin(); self.journal.checkpoint('validated', validated=True)
        original = recovery.atomic_json
        def deny(path, value):
            if Path(path) == self.path: raise OSError(errno.ENOSPC, 'isolated publication full')
            return original(path, value)
        with patch.object(recovery, 'atomic_json', side_effect=deny):
            with self.assertRaises(OSError): self.journal.publish_generation()
        self.assertEqual(self.journal.read()['phase'], 'before_generation_switch')
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'A')

    def test_newer_accepted_work_requires_fresh_preservation_before_rollback(self):
        self.begin(); self.journal.checkpoint('activated', accepted_new_work=True)
        with self.assertRaises(recovery.RecoveryError): self.journal.rollback()
        self.journal.rollback(current_preservation='isolated verified preservation reference')
        self.assertEqual(self.journal.read()['phase'], 'rolled_back')

    def test_transient_windows_publication_lock_retries_without_partial_json(self):
        original = recovery.os.replace
        attempts = []
        error = PermissionError(errno.EACCES, 'Fixture sharing violation'); error.winerror = 32
        def replace(source, destination):
            attempts.append(1)
            if len(attempts) == 1:
                self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'A')
                raise error
            return original(source, destination)
        with patch.object(recovery.os, 'replace', side_effect=replace), patch.object(recovery.time, 'sleep'):
            recovery.atomic_json(self.path, {'generation': 'B'})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'B')

    def test_persistent_windows_publication_denial_is_bounded_and_keeps_last_json(self):
        error = PermissionError(errno.EACCES, 'Fixture persistent denial'); error.winerror = 5
        before = self.path.read_bytes()
        with patch.object(recovery.os, 'replace', side_effect=error) as replace, patch.object(recovery.time, 'sleep'):
            with self.assertRaises(PermissionError): recovery.atomic_json(self.path, {'generation': 'B'})
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(self.path.read_bytes(), before)

    def test_fresh_installed_entry_points_block_at_each_interrupted_publication_boundary(self):
        recovery.atomic_json(self.root / 'config/registry.json', {'schema': 1, 'bots': [{
            'id': 'fixture', 'schema_version': 1, 'contract_version': 1,
            'python': sys.executable, 'module': 'missing_fixture.managed', 'cwd': 'data/fixture',
            'data_dir': 'data/fixture', 'log_dir': 'logs/fixture', 'credential_ref': 'fixture',
            'telegram_id': 123456, 'required_state': ['must-not-initialize.sqlite3']}]})
        self.begin()
        commands = [[sys.executable, '-m', 'bot_center_runtime.runner', '--root', str(self.root), '--bot', 'fixture'],
                    [sys.executable, '-m', 'bot_center', '--root', str(self.root), 'serve']]
        phases = ('prepared', 'before_generation_switch', 'generation_switched', 'before_shortcut_publication', 'installation_validated')
        for phase in phases:
            if phase == 'generation_switched':
                self.journal.checkpoint('validated', validated=True)
                self.journal.publish_generation()
            elif phase != 'prepared':
                self.journal.checkpoint(phase)
            for command in commands:
                with self.subTest(phase=phase, entry=command[2]):
                    result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=15,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('activation/recovery is incomplete', result.stdout + result.stderr)
                    self.assertFalse((self.root / 'data').exists(), 'Blocked startup must not create empty canonical state')
        self.journal.rollback()
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['generation'], 'A')


if __name__ == '__main__': unittest.main()
