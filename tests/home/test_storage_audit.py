"""Independent storage audit: snapshot races, transaction failures and recovery."""
import contextlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from home_bot.storage import Store, _atomic_write


class StorageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='home-storage-audit-')
        self.folder = Path(self.temporary.name)
        self.store = Store(self.folder)
        self.store.add_member(101, 'Owner', 'owner')
        self.task_id = self.store.add_task('shopping', 'Milk', 101)

    def tearDown(self):
        self.doCleanups()
        self.store.close()
        self.temporary.cleanup()

    def other(self):
        store = Store(self.folder)
        store.conn.execute('PRAGMA busy_timeout=20')
        self.addCleanup(store.close)
        return store

    def test_backup_json_and_sqlite_describe_the_same_revision(self):
        render = Store._render
        changed = False

        def during_render(snapshot):
            nonlocal changed
            if not changed:
                changed = True
                self.store.change_task(self.task_id, 'complete', 1, 101)
            return render(snapshot)

        with mock.patch.object(Store, '_render', side_effect=during_render):
            path = self.store.create_backup()
        with zipfile.ZipFile(path) as archive:
            data = json.loads(archive.read('tasks.json'))
            database = self.folder / 'snapshot.sqlite3'
            database.write_bytes(archive.read('tasks.sqlite3'))
        with contextlib.closing(sqlite3.connect(database)) as db:
            revision = json.loads(db.execute("SELECT value FROM meta WHERE key='_data_revision'").fetchone()[0])
            row = db.execute('SELECT done,revision FROM tasks WHERE id=?', (self.task_id,)).fetchone()
        self.assertEqual(data['revision'], revision)
        self.assertEqual((data['tasks'][0]['done'], data['tasks'][0]['revision']), tuple(row))

    def test_export_snapshot_is_consistent_during_another_connection_write(self):
        other = self.other()
        initial_revision = self.store.revision()
        changed = False

        def race(sql):
            nonlocal changed
            if 'SELECT * FROM tasks ORDER BY id' in sql and not changed:
                changed = True
                other.change_task(self.task_id, 'complete', 1, 101)

        self.store.conn.set_trace_callback(race)
        try:
            snapshot = self.store._snapshot()
        finally:
            self.store.conn.set_trace_callback(None)
        self.assertTrue(changed)
        self.assertEqual(snapshot['revision'], initial_revision)
        self.assertFalse(snapshot['tasks'][0]['done'])
        self.assertTrue(self.store.task(self.task_id)['done'])

    def test_concurrent_category_creation_does_not_overwrite_a_category(self):
        other = self.other()
        set_value = self.store._set_value
        attempted = False
        deferred = False

        def race(key, value):
            nonlocal attempted, deferred
            if key == 'custom_categories' and not attempted:
                attempted = True
                try:
                    other.add_category('Garden', 101)
                except sqlite3.OperationalError as exc:
                    self.assertIn('locked', str(exc))
                    deferred = True
            return set_value(key, value)

        with mock.patch.object(self.store, '_set_value', side_effect=race):
            self.store.add_category('Travel', 101)
        if deferred:
            other.add_category('Garden', 101)
        self.assertEqual(set(self.store.categories().values()) & {'Garden', 'Travel'}, {'Garden', 'Travel'})

    def test_concurrent_members_never_exceed_two(self):
        other = self.other()
        attempted = False
        deferred = False

        def race(sql):
            nonlocal attempted, deferred
            if 'INSERT INTO members' in sql and not attempted:
                attempted = True
                try:
                    other.add_member(303, 'Third')
                except sqlite3.OperationalError:
                    deferred = True

        self.store.conn.set_trace_callback(race)
        try:
            self.store.add_member(202, 'Partner')
        finally:
            self.store.conn.set_trace_callback(None)
        if deferred:
            with self.assertRaises(ValueError):
                other.add_member(303, 'Third')
        self.assertEqual(len(self.store.list_members()), 2)

    def category_race(self, operation):
        other = self.other()
        categories = self.store.categories
        attempted = False
        deferred = False

        def race(*args, **kwargs):
            nonlocal attempted, deferred
            result = categories(*args, **kwargs)
            if not attempted:
                attempted = True
                try:
                    other.remove_category('shopping', 101)
                except sqlite3.OperationalError as exc:
                    self.assertIn('locked', str(exc))
                    deferred = True
            return result

        with mock.patch.object(self.store, 'categories', side_effect=race):
            operation()
        if deferred:
            other.remove_category('shopping', 101)
        self.assertNotIn('shopping', self.store.categories())
        self.assertFalse(any(t['category'] == 'shopping' for t in self.store.list_tasks(mode='all')))

    def test_category_removal_cannot_race_a_task_insertion(self):
        self.category_race(lambda: self.store.add_task('shopping', 'Apples', 101))

    def test_category_removal_cannot_race_an_individual_restore(self):
        self.store.change_task(self.task_id, 'delete', 1, 101)
        self.category_race(lambda: self.store.change_task(self.task_id, 'restore', 2, 101))

    def test_multiline_add_is_atomic_on_mid_batch_failure(self):
        before = self.store._snapshot()
        self.store.conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON tasks WHEN NEW.title='Fail' "
                                "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_tasks('shopping', ['Apples', 'Fail', 'Pears'], 101, 'batch:1')
        after = self.store._snapshot()
        self.assertEqual(after['tasks'], before['tasks'])
        self.assertEqual(after['revision'], before['revision'])
        self.assertFalse(self.store.conn.in_transaction)
        self.assertEqual(list(self.store.backups_dir.glob('*.zip')), [])

    def test_multiline_retry_keeps_ids_and_existing_source_keys(self):
        first = self.store.add_task('shopping', 'Apples', 101, 'update:2:0')
        ids = self.store.add_tasks('shopping', ['Apples', 'Pears'], 101, 'update:2')
        self.assertEqual(ids[0], first)
        revision = self.store.revision()
        self.assertEqual(self.store.add_tasks('shopping', ['Apples', 'Pears'], 101, 'update:2'), ids)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(len(self.store.list_tasks(mode='all')), 3)

    def test_unicode_category_names_whitespace_casefold_and_length_boundaries(self):
        expected = ['خانه ما', '🧗 🫧 🛒', 'Français ÉTÉ', 'Week End', '🧗' * 24]
        inputs = ['  خانه ما  ', ' 🧗 🫧 🛒 ', ' Français ÉTÉ ', ' Week   End ', '🧗' * 24]
        keys = [self.store.add_category(label, 101) for label in inputs]
        self.assertEqual([self.store.categories()[key] for key in keys], expected)
        for duplicate in ('خانه ما', '🧗 🫧 🛒', 'français été', 'week end', '🧗' * 24):
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'already exists'):
                self.store.add_category(duplicate, 101)
        revision = self.store.revision()
        with self.assertRaises(ValueError):
            self.store.add_category('🧗' * 25, 101)
        self.assertEqual(self.store.revision(), revision)
        exported = json.loads(self.store.export_latest()['json'].read_text('utf-8'))
        self.assertEqual([entry['label'] for entry in exported['settings']['custom_categories']], expected)

    def test_45_custom_category_cap_includes_archived_categories(self):
        keys = [self.store.add_category(f'Category {index:02d}', 101) for index in range(45)]
        self.assertEqual(len(self.store.categories()), 50)
        revision = self.store.revision()
        with self.assertRaisesRegex(ValueError, '45 custom categories'):
            self.store.add_category('Category 46', 101)
        self.assertEqual(self.store.revision(), revision)
        removed = self.store.remove_category(keys[0], 101)
        with self.assertRaisesRegex(ValueError, '45 custom categories'):
            self.store.add_category('Replacement name', 101)
        self.assertEqual(len(self.store.categories(include_removed=True)), 50)
        self.assertTrue(self.store.restore_category(keys[0], removed['token'], 101))
        self.assertEqual(len(self.store.categories()), 50)
        self.assertEqual(list(self.store.backups_dir.glob('*.zip')), [])

    def test_archived_category_name_conflict_is_rejected_without_merging_tasks(self):
        category = self.store.add_category('Garden', 101)
        task_id = self.store.add_task(category, 'Plant flowers', 101)
        self.store.change_task(task_id, 'complete', 1, 101)
        removed = self.store.remove_category(category, 101)
        revision = self.store.revision()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.store.add_category(' garden ', 101)
        self.assertEqual(self.store.revision(), revision)
        self.assertTrue(self.store.restore_category(category, removed['token'], 101))
        restored = self.store.task(task_id)
        self.assertEqual(restored['category'], category)
        self.assertEqual(restored['title'], 'Plant flowers')
        self.assertTrue(restored['done'])
        self.assertFalse(restored['deleted'])
        self.assertEqual(list(self.store.categories().values()).count('Garden'), 1)

    def test_duplicate_titles_are_distinct_inputs_but_delivery_keys_are_idempotent(self):
        ids = self.store.add_tasks('shopping', ['Milk', 'Milk'], 101, 'update:duplicates')
        self.assertEqual(len(set(ids)), 2)
        revision = self.store.revision()
        self.assertEqual(self.store.add_tasks('shopping', ['Milk', 'Milk'], 101, 'update:duplicates'), ids)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual([task['title'] for task in self.store.list_tasks('shopping', 'all')],
                         ['Milk', 'Milk', 'Milk'])

    def test_restore_does_not_create_an_unsolicited_backup(self):
        backup = self.store.create_backup()
        inventory = set(self.store.backups_dir.glob('*.zip'))
        self.store.add_task('shopping', 'Later', 101)
        self.store.restore_backup(backup)
        self.assertEqual(set(self.store.backups_dir.glob('*.zip')), inventory)
        self.assertEqual([t['title'] for t in self.store.list_tasks(mode='all')], ['Milk'])

    def test_same_chat_restore_reuses_panel_but_discards_pending_actions(self):
        self.store.set('chat_id', -100111)
        backup = self.store.create_backup()
        self.store.set('list_id', 987)
        self.store.set('pending_101', {'category': 'shopping'})
        self.store.set('panel_state_list_id', {'callbacks': ['dangerous:old']})
        self.store.restore_backup(backup)
        self.assertEqual(self.store.get('list_id'), 987)
        self.assertIsNone(self.store.get('pending_101'))
        self.assertIsNone(self.store.get('panel_state_list_id'))

    def test_cross_chat_restore_cannot_reuse_foreign_panel(self):
        self.store.set('chat_id', -100111)
        backup = self.store.create_backup()
        self.store.set('chat_id', -100222)
        self.store.set('list_id', 987)
        self.store.restore_backup(backup)
        self.assertIsNone(self.store.get('list_id'))

    def test_category_generation_guards_remove_after_restore_and_backup_restore(self):
        original = self.store.category_revision('shopping')
        entry = self.store.remove_category('shopping', 101, expected_revision=original)
        self.assertTrue(self.store.restore_category('shopping', entry['token'], 101))
        self.assertNotEqual(self.store.category_revision('shopping'), original)
        self.assertIsNone(self.store.remove_category('shopping', 101, expected_revision=original))
        backup = self.store.create_backup()
        current = self.store.category_revision('shopping')
        self.store.restore_backup(backup)
        self.assertGreater(self.store.category_revision('shopping'), current)
        self.assertIsNone(self.store.remove_category('shopping', 101, expected_revision=current))

    def test_backup_restore_never_reuses_a_historical_task_revision(self):
        snapshot_revision = self.store.task(self.task_id)['revision']
        backup = self.store.create_backup()
        self.store.change_task(self.task_id, 'complete', snapshot_revision, 101)
        self.store.change_task(self.task_id, 'reopen', snapshot_revision + 1, 101)
        live_revision = self.store.task(self.task_id)['revision']
        self.store.restore_backup(backup)
        restored = self.store.task(self.task_id)
        self.assertGreater(restored['revision'], live_revision)
        self.assertFalse(restored['done'])
        self.assertFalse(self.store.change_task(self.task_id, 'delete', snapshot_revision, 101))
        self.assertFalse(self.store.change_task(self.task_id, 'complete', live_revision, 101))

    def test_backup_restore_invalidates_archived_category_restore_buttons(self):
        removed = self.store.remove_category('shopping', 101)
        backup = self.store.create_backup()
        self.store.restore_category('shopping', removed['token'], 101)
        self.store.restore_backup(backup)
        current = next(c for c in self.store.get('removed_categories') if c['key'] == 'shopping')
        self.assertNotEqual(current['token'], removed['token'])
        self.assertFalse(self.store.restore_category('shopping', removed['token'], 101))
        self.assertTrue(self.store.restore_category('shopping', current['token'], 101))
        self.assertFalse(self.store.task(self.task_id)['deleted'])

    def test_same_chat_restore_cleans_abandoned_prompts_without_deleting_old_user_input(self):
        self.store.set('chat_id', -100111)
        backup = self.store.create_backup()
        self.store.set('pending_101', {'prompt_key': 'category_prompt_101_a', 'chat_id': -100111})
        self.store.set('category_prompt_101_a', 123)
        self.store.set('prompt_deletions', [{'key': None, 'chat_id': -100111, 'message_id': 124}])
        self.store.restore_backup(backup)
        queued = self.store.get('prompt_deletions', [])
        self.assertEqual([entry['message_id'] for entry in queued], [123])
        self.assertIsNone(self.store.get('pending_101'))

    def test_restore_write_failure_rolls_back_all_records_without_creating_zip(self):
        snapshot = self.store.export_latest()['json']
        self.store.add_task('home', 'Preserve after failed restore', 101)
        before = self.store._snapshot()
        set_value = self.store._set_value

        def fail_revision(key, value):
            if key == '_data_revision':
                raise sqlite3.OperationalError('injected restore failure')
            return set_value(key, value)

        with mock.patch.object(self.store, '_set_value', side_effect=fail_revision):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.restore_backup(snapshot)
        after = self.store._snapshot()
        for key in ('members', 'settings', 'tasks', 'revision'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(list(self.store.backups_dir.glob('*.zip')), [])
        self.assertEqual(self.store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_migration_failure_after_drop_rolls_back_then_restarts_safely(self):
        before = self.store.task(self.task_id)
        vanished = self.store.add_task('home', 'Reserve this ID', 101)
        with self.store.conn:
            self.store.conn.execute('DELETE FROM tasks WHERE id=?', (vanished,))
            definition = self.store.conn.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
            legacy = definition.replace('category TEXT NOT NULL',
                "category TEXT NOT NULL CHECK(category IN ('shopping','household','home','admin','study'))")
            self.store.conn.execute('PRAGMA writable_schema=ON')
            self.store.conn.execute("UPDATE sqlite_master SET sql=? WHERE name='tasks'", (legacy,))
            self.store.conn.execute('PRAGMA writable_schema=OFF')
        self.store.close()
        connect = sqlite3.connect
        opened = []

        class FailingConnection(__import__('home_bot.storage',fromlist=['AtomicUpdateConnection']).AtomicUpdateConnection):
            def execute(connection, sql, *args, **kwargs):
                if sql.startswith('ALTER TABLE tasks_expanded'):
                    raise sqlite3.OperationalError('injected migration failure after DROP')
                return super().execute(sql, *args, **kwargs)

        def failing_connection(*args, **kwargs):
            kwargs['factory']=FailingConnection
            connection = connect(*args, **kwargs)
            opened.append(connection)
            return connection

        try:
            with mock.patch('home_bot.storage.sqlite3.connect', side_effect=failing_connection):
                with self.assertRaises(sqlite3.OperationalError):
                    Store(self.folder)
        finally:
            for connection in opened:
                connection.close()
            self.store = Store(self.folder)
        self.assertEqual(self.store.task(self.task_id), before)
        self.assertGreater(self.store.add_task('home', 'Next task', 101), vanished)
        self.assertEqual(self.store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(list(self.store.backups_dir.glob('*.zip')), [])

    def test_locked_database_fails_without_partial_change_then_recovers(self):
        other = self.other()
        self.store.conn.execute('PRAGMA busy_timeout=20')
        before = self.store.task(self.task_id)
        other.conn.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.change_task(self.task_id, 'complete', 1, 101)
        finally:
            other.conn.rollback()
        self.assertEqual(self.store.task(self.task_id), before)
        self.assertTrue(self.store.change_task(self.task_id, 'complete', 1, 101))

    def test_interrupted_export_keeps_old_complete_file_and_retry_repairs_it(self):
        path = self.store.export_latest()['json']
        before = path.read_bytes()
        self.store.add_task('shopping', 'Apples', 101)
        with mock.patch('home_bot.storage._replace_with_retry', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.export_latest()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('.tasks-latest.*')), [])
        self.store.export_latest()
        self.assertEqual(len(json.loads(path.read_text('utf-8'))['tasks']), 2)

    def test_failed_backup_does_not_publish_a_partial_zip(self):
        with mock.patch('home_bot.storage.zipfile.ZipFile.writestr', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.create_backup()
        self.assertEqual(list(self.store.backups_dir.iterdir()), [])
        self.assertEqual(self.store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_process_termination_rolls_back_an_uncommitted_transaction(self):
        ready = self.folder / 'transaction-ready'
        script = """
import sqlite3,sys,time
from pathlib import Path
folder=Path(sys.argv[1]); db=sqlite3.connect(folder/'tasks.sqlite3')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE tasks SET done=1,revision=revision+1')
(folder/'transaction-ready').write_text('ready')
time.sleep(30)
"""
        process = subprocess.Popen([sys.executable, '-c', script, str(self.folder)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), 'Child did not reach its uncommitted transaction')
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.assertFalse(self.store.task(self.task_id)['done'])
        self.assertEqual(self.store.task(self.task_id)['revision'], 1)
        self.assertEqual(self.store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(list(self.store.backups_dir.glob('*.zip')), [])


if __name__ == '__main__':
    unittest.main()
