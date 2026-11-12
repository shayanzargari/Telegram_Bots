"""Atomic selected-task removal/restoration and durable confirmation receipts."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from home_bot.storage import Store


class BatchStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='home-batch-storage-')
        self.folder = Path(self.temporary.name)
        self.store = Store(self.folder)
        self.open_id = self.store.add_task('shopping', 'Milk', 101)
        self.done_id = self.store.add_task('shopping', 'Bananas', 101)
        self.store.change_task(self.done_id, 'complete', 1, 101)
        self.trash_id = self.store.add_task('shopping', 'Old pears', 101)
        self.store.change_task(self.trash_id, 'delete', 1, 101)
        self.other_id = self.store.add_task('home', 'Mirror', 101)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def versions(self, *ids):
        return {tid: self.store.task(tid)['revision'] for tid in ids}

    def tasks(self):
        return [dict(row) for row in self.store.conn.execute('SELECT * FROM tasks ORDER BY id')]

    def receipt(self, source='batch:1'):
        key = 'batch_source_' + hashlib.sha256(source.encode()).hexdigest()
        return self.store.get(key)

    def assert_no_backups(self):
        self.assertEqual(list(self.folder.glob('backups/*.zip')), [])

    def test_delete_mixed_completion_preserves_ids_and_bumps_data_revision_once(self):
        expected = self.versions(self.done_id, self.open_id)
        before = {task['id']: task for task in self.tasks()}
        revision = self.store.revision()
        result = self.store.change_tasks('shopping', 'delete', expected, 202, 'batch:1')
        self.assertEqual(result, [{'id': tid, 'revision': expected[tid] + 1}
                                  for tid in sorted(expected)])
        self.assertEqual(self.store.revision(), revision + 1)
        timestamps = set()
        for row in self.tasks():
            if row['id'] in expected:
                self.assertEqual(row['deleted'], 1)
                self.assertEqual(row['revision'], expected[row['id']] + 1)
                self.assertEqual(row['updated_by'], 202)
                timestamps.add(row['updated_at'])
                for field in ('id', 'title', 'category', 'done', 'created_at', 'created_by', 'source_key'):
                    self.assertEqual(row[field], before[row['id']][field])
            else:
                self.assertEqual(row, before[row['id']])
        self.assertEqual(len(timestamps), 1)
        self.assertIsNotNone(self.receipt())
        self.assert_no_backups()

    def test_restore_selection_preserves_checks_and_leaves_other_trash(self):
        deleted = self.store.change_tasks('shopping', 'delete', self.versions(self.open_id, self.done_id),
                                          101, 'batch:1')
        revision = self.store.revision()
        restored = self.store.change_tasks('shopping', 'restore',
            {row['id']: row['revision'] for row in deleted}, 202, 'undo-batch:1')
        self.assertEqual([row['id'] for row in restored], [self.open_id, self.done_id])
        self.assertEqual(self.store.revision(), revision + 1)
        self.assertFalse(self.store.task(self.open_id)['done'])
        self.assertTrue(self.store.task(self.done_id)['done'])
        self.assertFalse(self.store.task(self.open_id)['deleted'])
        self.assertFalse(self.store.task(self.done_id)['deleted'])
        self.assertTrue(self.store.task(self.trash_id)['deleted'])
        self.assert_no_backups()

    def test_mixed_category_restore_preserves_each_category_and_checked_state(self):
        self.store.change_task(self.other_id, 'complete', 1, 101)
        ids = (self.open_id, self.done_id, self.other_id)
        expected = self.versions(*ids)
        deleted = self.store.change_tasks(None, 'delete', expected, 101, 'batch:mixed-delete')
        revision = self.store.revision()
        restored = self.store.change_tasks(None, 'restore',
            {row['id']: row['revision'] for row in deleted}, 202, 'batch:mixed-restore')
        self.assertEqual([row['id'] for row in restored], sorted(ids))
        self.assertEqual(self.store.revision(), revision + 1)
        self.assertEqual([self.store.task(tid)['category'] for tid in ids],
                         ['shopping', 'shopping', 'home'])
        self.assertEqual([self.store.task(tid)['done'] for tid in ids], [False, True, True])
        self.assertTrue(all(not self.store.task(tid)['deleted'] for tid in ids))
        self.assertTrue(self.store.task(self.trash_id)['deleted'])
        self.assert_no_backups()

    def test_one_archived_category_rejects_the_entire_mixed_restore(self):
        self.store.change_task(self.other_id, 'delete', 1, 101)
        archived = self.store.remove_category('home', 101)
        expected = self.versions(self.trash_id, self.other_id)
        before, revision = self.tasks(), self.store.revision()
        self.assertIsNone(self.store.change_tasks(None, 'restore', expected, 101, 'batch:mixed-restore'))
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertIsNone(self.receipt('batch:mixed-restore'))
        self.store.restore_category('home', archived['token'], 101)
        self.assertIsNotNone(self.store.change_tasks(None, 'restore', expected, 101, 'batch:mixed-restore'))
        self.assertFalse(self.store.task(self.trash_id)['deleted'])
        self.assertFalse(self.store.task(self.other_id)['deleted'])

    def test_mixed_restore_can_be_undone_once_without_losing_categories_or_checks(self):
        self.store.change_task(self.other_id, 'complete', 1, 101)
        self.store.change_task(self.other_id, 'delete', 2, 101)
        expected = self.versions(self.trash_id, self.other_id)
        restored = self.store.change_tasks(None, 'restore', expected, 101, 'batch:mixed-restore')
        undo_expected = {row['id']: row['revision'] for row in restored}
        revision = self.store.revision()
        undone = self.store.change_tasks(None, 'delete', undo_expected, 202, 'undo-batch:mixed-restore')
        self.assertEqual(self.store.revision(), revision + 1)
        self.store.close()
        self.store = Store(self.folder)
        before, revision = self.tasks(), self.store.revision()
        self.assertEqual(self.store.change_tasks(None, 'delete', undo_expected, 202,
                                                 'undo-batch:mixed-restore'), undone)
        self.assertEqual(self.store.change_tasks(None, 'restore', expected, 101,
                                                 'batch:mixed-restore'), restored)
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertTrue(self.store.task(self.other_id)['done'])
        self.assertTrue(self.store.task(self.other_id)['deleted'])
        self.assertEqual(self.store.task(self.other_id)['category'], 'home')
        self.assertEqual(self.store.task(self.trash_id)['category'], 'shopping')
        self.assert_no_backups()

    def test_mixed_and_named_scope_cannot_share_one_receipt_key(self):
        expected = self.versions(self.open_id)
        self.store.change_tasks(None, 'delete', expected, 101, 'batch:1')
        before, revision = self.tasks(), self.store.revision()
        with self.assertRaises(ValueError):
            self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)

    def test_wrong_category_missing_or_stale_task_rejects_entire_batch(self):
        cases = [self.versions(self.open_id, self.other_id),
                 {self.open_id: 1, 999999: 1},
                 {self.open_id: 1, self.done_id: 1}]
        before, revision = self.tasks(), self.store.revision()
        for expected in cases:
            with self.subTest(expected=expected):
                self.assertIsNone(self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1'))
                self.assertEqual(self.tasks(), before)
                self.assertEqual(self.store.revision(), revision)
                self.assertIsNone(self.receipt())
                self.assertFalse(self.store.conn.in_transaction)
        self.assertIsNotNone(self.store.change_tasks('shopping', 'delete',
            self.versions(self.open_id, self.done_id), 101, 'batch:1'))

    def test_wrong_deleted_state_rejects_delete_and_restore_without_partial_changes(self):
        before, revision = self.tasks(), self.store.revision()
        for action in ('delete', 'restore'):
            with self.subTest(action=action):
                self.assertIsNone(self.store.change_tasks('shopping', action,
                    self.versions(self.open_id, self.trash_id), 101, 'batch:' + action))
                self.assertEqual(self.tasks(), before)
                self.assertEqual(self.store.revision(), revision)
                self.assertIsNone(self.receipt('batch:' + action))

    def test_archived_or_missing_category_cannot_restore_selected_tasks(self):
        self.store.remove_category('shopping', 101)
        before, revision = self.tasks(), self.store.revision()
        for category in ('shopping', 'custom_missing'):
            with self.subTest(category=category):
                self.assertIsNone(self.store.change_tasks(category, 'restore',
                    self.versions(self.open_id, self.done_id), 101, 'batch:1'))
                self.assertEqual(self.tasks(), before)
                self.assertEqual(self.store.revision(), revision)
                self.assertIsNone(self.receipt())

    def test_duplicate_confirmation_is_order_independent_and_persists_after_restart(self):
        expected = self.versions(self.done_id, self.open_id)
        result = self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        before, revision = self.tasks(), self.store.revision()
        self.store.close()
        self.store = Store(self.folder)
        for _ in range(3):
            replay = self.store.change_tasks('shopping', 'delete', dict(reversed(list(expected.items()))),
                                            101, 'batch:1')
            self.assertEqual(replay, result)
            self.assertEqual(self.tasks(), before)
            self.assertEqual(self.store.revision(), revision)
        self.assert_no_backups()

    def test_replayed_delete_after_restore_returns_receipt_without_deleting_again(self):
        expected = self.versions(self.open_id, self.done_id)
        deleted = self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        restore_expected = {row['id']: row['revision'] for row in deleted}
        restored = self.store.change_tasks('shopping', 'restore', restore_expected, 202, 'undo-batch:1')
        self.store.close()
        self.store = Store(self.folder)
        before, revision = self.tasks(), self.store.revision()
        self.assertEqual(self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1'), deleted)
        self.assertEqual(self.store.change_tasks('shopping', 'restore', restore_expected,
                                                 202, 'undo-batch:1'), restored)
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertFalse(self.store.task(self.open_id)['deleted'])

    def test_receipt_replay_precedes_current_category_archive_state(self):
        expected = self.versions(self.open_id, self.done_id)
        result = self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        self.store.remove_category('shopping', 101)
        before, revision = self.tasks(), self.store.revision()
        self.assertEqual(self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1'), result)
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)

    def test_receipt_key_reuse_for_a_different_request_is_rejected(self):
        expected = self.versions(self.open_id, self.done_id)
        self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        before, revision = self.tasks(), self.store.revision()
        requests = [('home', 'delete', expected, 101),
                    ('shopping', 'restore', expected, 101),
                    ('shopping', 'delete', expected, 202),
                    ('shopping', 'delete', {self.open_id: expected[self.open_id]}, 101),
                    ('shopping', 'delete', {**expected, self.done_id: expected[self.done_id] + 1}, 101)]
        for category, action, versions, actor in requests:
            with self.subTest(request=(category, action, versions, actor)), self.assertRaises(ValueError):
                self.store.change_tasks(category, action, versions, actor, 'batch:1')
            self.assertEqual(self.tasks(), before)
            self.assertEqual(self.store.revision(), revision)

    def test_mid_batch_trigger_failure_rolls_back_tasks_revision_and_receipt(self):
        self.store.conn.execute(
            f"CREATE TRIGGER reject_second BEFORE UPDATE ON tasks WHEN OLD.id={self.done_id} "
            "AND NEW.deleted=1 BEGIN SELECT RAISE(ABORT,'injected batch failure'); END")
        expected = self.versions(self.open_id, self.done_id)
        before, revision = self.tasks(), self.store.revision()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1')
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertIsNone(self.receipt())
        self.assertFalse(self.store.conn.in_transaction)
        self.store.conn.execute('DROP TRIGGER reject_second')
        self.assertIsNotNone(self.store.change_tasks('shopping', 'delete', expected, 101, 'batch:1'))
        self.assertEqual(self.store.revision(), revision + 1)
        self.assert_no_backups()

    def test_trigger_induced_late_conflict_rolls_back_the_first_update(self):
        self.store.conn.execute(
            f"CREATE TRIGGER change_second AFTER UPDATE ON tasks WHEN OLD.id={self.open_id} "
            f"BEGIN UPDATE tasks SET revision=revision+1 WHERE id={self.done_id}; END")
        before, revision = self.tasks(), self.store.revision()
        self.assertIsNone(self.store.change_tasks('shopping', 'delete',
            self.versions(self.open_id, self.done_id), 101, 'batch:1'))
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertIsNone(self.receipt())
        self.assertFalse(self.store.conn.in_transaction)

    def test_receipt_write_failure_rolls_back_the_whole_batch(self):
        set_value = self.store._set_value
        before, revision = self.tasks(), self.store.revision()

        def fail_receipt(key, value):
            if key.startswith('batch_source_'):
                raise sqlite3.OperationalError('injected receipt write failure')
            return set_value(key, value)

        with mock.patch.object(self.store, '_set_value', side_effect=fail_receipt):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.change_tasks('shopping', 'delete', self.versions(self.open_id, self.done_id),
                                        101, 'batch:1')
        self.assertEqual(self.tasks(), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertIsNone(self.receipt())

    def test_invalid_arguments_are_rejected_without_changes(self):
        valid = ['shopping', 'delete', {self.open_id: 1}, 101, 'batch:1']
        replacements = [(0, 0), (0, []), (0, ''), (0, '   '), (0, 'bad\x00name'),
                        (1, 'complete'), (1, []), (2, {}), (2, []), (2, None),
                        (2, {True: 1}), (2, {'1': 1}), (2, {0: 1}),
                        (2, {1: True}), (2, {1: 0}), (2, {1: '1'}),
                        (3, True), (3, -1), (3, '101'),
                        (4, None), (4, ''), (4, '   '), (4, 123), (4, 'x' * 513)]
        before, revision = self.tasks(), self.store.revision()
        for index, value in replacements:
            args = list(valid)
            args[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                self.store.change_tasks(*args)
            self.assertEqual(self.tasks(), before)
            self.assertEqual(self.store.revision(), revision)
        self.assert_no_backups()


if __name__ == '__main__':
    unittest.main()
