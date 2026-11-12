"""Synthetic HTTP acceptance for selecting tasks before moving them to Trash."""
import copy
import secrets
import unittest
from unittest import mock

from home_bot.simulator import GROUP, OWNER, PARTNER
from home_bot.storage import Store
from test_local_telegram import HTTPScenario


class BatchRemoveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def task(self, task_id):
        return next(task for task in self.s.tasks() if task['id'] == task_id)

    def open_remove(self, category='shopping', uid=OWNER):
        self.s.send('/menu', uid)
        self.s.click('cat:' + category, uid)
        self.s.click('remove:' + category, uid)

    def picked(self):
        # desired=0 means this selected item can now be unselected. The extra
        # per-item generation is opaque; do not mirror its implementation.
        return {int(b['callback_data'].split(':')[1]) for b in self.s.buttons()
                if b['callback_data'].startswith('pick:') and b['callback_data'].split(':')[4] == '0'}

    def confirm(self, uid=OWNER):
        return self.s.click('batch:remove:', uid)

    def undo(self, uid=OWNER):
        control = next(b for b in self.s.buttons() if 'Undo remove' in b['text'])
        return self.s.click(control['callback_data'], uid, exact=True)

    def assert_quiet(self):
        self.assertEqual(len(self.s.bot_messages()), 1)
        self.assertEqual(self.s.panel()['message_id'], self.s.bot_messages()[0]['message_id'])
        self.assertEqual(self.s.state.files, {})
        self.assertEqual(list(self.s.worker.folder.glob('backups/*.zip')), [])

    def concurrent_change(self, task_id, action, uid=PARTNER):
        # Independent SQLite connection simulates a committed conflicting
        # action while the original selection remains on the shared panel.
        store = Store(self.s.worker.folder)
        try:
            revision = store.task(task_id)['revision']
            self.assertTrue(store.change_task(task_id, action, revision, uid))
        finally:
            store.close()

    def test_select_unselect_then_confirm_keeps_checks_and_all_rows_visible(self):
        s = self.s
        s.click('cat:shopping')
        s.click('task:complete:1:')
        before = copy.deepcopy(s.tasks())
        before_revision = s.meta('_data_revision')
        self.open_remove()
        self.assertTrue(any(b['callback_data'].startswith('pick:') for b in s.buttons()))
        self.assertFalse(any(b['callback_data'].startswith(('task:delete:', 'batch:remove:')) for b in s.buttons()))
        s.click('pick:1:')
        s.click('pick:2:')
        self.assertEqual(self.picked(), {1, 2})
        self.assertEqual(s.tasks(), before)
        self.assertEqual(s.meta('_data_revision'), before_revision)
        confirm = next(b for b in s.buttons() if b['callback_data'].startswith('batch:remove:'))
        self.assertIn('Move to Trash (2)', confirm['text'])
        s.click('pick:2:')
        self.assertEqual(self.picked(), {1})
        s.click('pick:2:')
        for task in before:
            if task['category'] == 'shopping':
                self.assertIn(task['title'], s.panel()['text'])
        self.confirm()
        self.assertIn('Moved 2 tasks to Trash', s.panel()['text'])
        for task_id in (1, 2):
            after = self.task(task_id)
            self.assertTrue(after['deleted'])
            self.assertEqual(after['done'], before[task_id - 1]['done'])
            self.assertEqual(after['revision'], before[task_id - 1]['revision'] + 1)
        self.assertEqual(s.tasks()[2:], before[2:])
        self.assert_quiet()

    def test_done_clears_category_for_either_member_and_menu_stays_empty(self):
        s = self.s
        for uid in (OWNER, PARTNER):
            with self.subTest(uid=uid):
                s.click('cat:shopping', uid)
                before = copy.deepcopy(s.tasks())
                s.click('finish:shopping:', uid)
                self.assertTrue(all(t['deleted'] for t in s.tasks() if t['category'] == 'shopping'))
                self.assertEqual(s.tasks()[7:], before[7:])
                self.assertIn('Groceries cleared', s.panel()['text'])
                self.assertEqual([b['text'] for b in s.buttons()], ['↩️ Undo remove tasks', '🏠 Main menu'])
                s.click('menu', uid)
                groceries = next(b for b in s.buttons() if b['callback_data'] == 'cat:shopping')
                self.assertTrue(groceries['text'].endswith(' 0'))
                s.click('cat:shopping', uid)
                self.assertIn('No tasks here', s.panel()['text'])
                revision = s.meta('_data_revision')
                s.click('finish:shopping:', uid)
                self.assertIn('Groceries is empty', s.panel()['text'])
                self.assertEqual([b['text'] for b in s.buttons()], ['🏠 Main menu'])
                self.assertEqual(s.meta('_data_revision'), revision)
                s.click('menu', uid)
                s.click('view:all:trash:0', uid)
                for tid in range(1, 8):
                    s.click(f'rpick:{tid}:', uid)
                s.click('batch:restore:', uid)
                s.click('menu', uid)
        self.assert_quiet()

    def test_done_undo_preserves_checked_states_and_existing_trash(self):
        s = self.s
        self.concurrent_change(1, 'complete')
        self.concurrent_change(7, 'delete')
        before = s.tasks()
        s.click('cat:shopping')
        s.click('finish:shopping:')
        self.assertIn('Moved 6 tasks to Trash', s.panel()['text'])
        self.assertEqual([[b['text'] for b in row] for row in s.panel()['reply_markup']['inline_keyboard']],
                         [['↩️ Undo remove tasks'], ['🏠 Main menu']])
        s.click('undo:', PARTNER)
        self.assertEqual(self.task(7), before[6])
        self.assertEqual([(t['done'], t['deleted']) for t in s.tasks()],
                         [(t['done'], t['deleted']) for t in before])
        self.assertEqual(s.meta('view')['category'], 'shopping')
        self.assertEqual(s.meta('screen'), 'list')
        s.click('finish:shopping:', PARTNER)
        self.assertIn('Moved 6 tasks to Trash', s.panel()['text'])
        self.assert_quiet()

    def test_done_rejects_old_button_after_navigation_or_new_cycle(self):
        s = self.s
        s.click('cat:shopping')
        old = s.panel()
        callback = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('finish:'))
        s.click('menu')
        s.click('cat:shopping')
        before = s.tasks()
        s.click(callback, message=old, exact=True)
        self.assertEqual(s.tasks(), before)
        s.click('finish:shopping:')
        s.click('undo:')
        self.assertEqual(s.meta('screen'), 'list')
        before = s.tasks()
        s.click(callback, message=old, exact=True)
        self.assertEqual(s.tasks(), before)
        self.assert_quiet()

    def test_done_rejects_changed_or_new_tasks_until_reviewed(self):
        s = self.s
        s.click('cat:shopping')
        self.concurrent_change(1, 'complete')
        before = s.tasks()
        s.click('finish:shopping:')
        self.assertEqual(s.tasks(), before)
        store = Store(s.worker.folder)
        try:
            store.add_task('shopping', 'Added by partner after the screen opened', PARTNER)
        finally:
            store.close()
        before = s.tasks()
        s.click('finish:shopping:')
        self.assertEqual(s.tasks(), before)
        self.assertIn('Added by partner', s.panel()['text'])
        s.click('finish:shopping:', PARTNER)
        self.assertIn('Moved 8 tasks to Trash', s.panel()['text'])
        self.assert_quiet()

    def test_done_lost_response_duplicate_and_restart_are_idempotent(self):
        s = self.s
        s.click('cat:shopping')
        before = s.tasks()
        s.state.fail_next('editMessageText', 'disconnect_after')
        update = s.click('finish:shopping:', PARTNER)
        committed = s.tasks()
        s.restart()
        s.processed(s.state.replay(update))
        self.assertEqual(s.tasks(), committed)
        self.assertTrue(all(t['revision'] == before[t['id'] - 1]['revision'] + 1 for t in committed[:7]))
        s.click('undo:', OWNER)
        self.assertFalse(any(t['deleted'] for t in s.tasks()))
        self.assert_quiet()

    def test_done_recovers_when_interrupted_immediately_after_database_commit(self):
        from home_bot.bot import NetworkError
        s = self.s
        s.click('cat:shopping')
        change = Store.change_tasks
        interrupted = []

        def commit_then_interrupt(store, *args, **kwargs):
            result = change(store, *args, **kwargs)
            if kwargs.get('require_all') and not interrupted:
                interrupted.append(True)
                raise NetworkError('Simulated interruption after category clear')
            return result

        with mock.patch.object(Store, 'change_tasks', commit_then_interrupt):
            s.click('finish:shopping:')
        self.assertEqual(interrupted, [True])
        self.assertIn('Moved 7 tasks to Trash', s.panel()['text'])
        self.assertTrue(all(t['revision'] == 2 for t in s.tasks()[:7]))
        s.click('undo:', PARTNER)
        self.assertFalse(any(t['deleted'] for t in s.tasks()))
        self.assert_quiet()

    def test_unselecting_every_item_removes_confirmation_without_data_writes(self):
        s = self.s
        before = s.tasks()
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:1:')
        self.assertEqual(self.picked(), set())
        self.assertFalse(any(b['callback_data'].startswith('batch:remove:') for b in s.buttons()))
        self.assertEqual(s.tasks(), before)
        self.assert_quiet()

    def test_navigation_discards_selection_and_old_session_buttons(self):
        s = self.s
        before = s.tasks()
        self.open_remove()
        old = s.panel()
        old_pick = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('pick:1:'))
        s.click('pick:1:')
        selected = s.panel()
        old_confirm = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('batch:remove:'))
        s.click('menu')
        s.click(old_confirm, message=selected, exact=True)
        self.assertEqual(s.tasks(), before)
        self.assertEqual(s.meta('screen'), 'menu')
        self.open_remove()
        s.click(old_pick, message=old, exact=True)
        self.assertEqual(self.picked(), set())
        self.assertEqual(s.tasks(), before)
        self.assert_quiet()

    def test_partner_cannot_pick_unpick_or_confirm_owners_selection(self):
        s = self.s
        before = s.tasks()
        self.open_remove()
        s.click('pick:1:')
        owner_panel = s.panel()
        s.click('pick:1:', PARTNER)
        s.click('pick:2:', PARTNER)
        self.confirm(PARTNER)
        self.assertEqual(s.panel(), owner_panel)
        self.assertEqual(self.picked(), {1})
        self.assertEqual(s.tasks(), before)
        self.confirm(OWNER)
        self.assertTrue(self.task(1)['deleted'])
        self.assertFalse(self.task(2)['deleted'])
        self.assert_quiet()

    def test_duplicate_pick_is_idempotent_even_after_unselection(self):
        s = self.s
        self.open_remove()
        old = s.panel()
        old_pick = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('pick:1:'))
        update = s.click(old_pick, message=old, exact=True)
        s.processed(s.state.replay(update))
        s.click(old_pick, message=old, exact=True)
        self.assertEqual(self.picked(), {1})
        s.click('pick:1:')
        s.click(old_pick, message=old, exact=True)
        self.assertEqual(self.picked(), set())
        self.assertFalse(self.task(1)['deleted'])
        self.assert_quiet()

    def test_old_confirmation_cannot_remove_a_newer_selection(self):
        s = self.s
        before = s.tasks()
        self.open_remove()
        s.click('pick:1:')
        old = s.panel()
        old_confirm = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('batch:remove:'))
        s.click('pick:2:')
        s.click(old_confirm, message=old, exact=True)
        self.assertEqual(s.tasks(), before)
        self.assertEqual(self.picked(), {1, 2})
        self.confirm()
        self.assertEqual({t['id'] for t in s.tasks() if t['deleted']}, {1, 2})
        self.assert_quiet()

    def test_restart_preserves_selection_and_duplicate_confirmation_is_once(self):
        s = self.s
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        before = s.tasks()
        controls = [b['callback_data'] for b in s.buttons()]
        s.restart()
        self.assertEqual(self.picked(), {1, 2})
        self.assertEqual([b['callback_data'] for b in s.buttons()], controls)
        update = self.confirm()
        committed = s.tasks()
        s.restart()
        s.processed(s.state.replay(update))
        self.assertEqual(s.tasks(), committed)
        self.assertEqual(committed[0]['revision'], before[0]['revision'] + 1)
        self.assertEqual(committed[1]['revision'], before[1]['revision'] + 1)
        self.assert_quiet()

    def test_stale_task_pick_refreshes_without_selecting_wrong_revision(self):
        s = self.s
        self.open_remove()
        self.concurrent_change(1, 'complete')
        s.click('pick:1:')
        self.assertEqual(self.picked(), set())
        self.assertTrue(self.task(1)['done'])
        self.assertFalse(self.task(1)['deleted'])
        s.click('pick:1:')
        self.confirm()
        self.assertTrue(self.task(1)['done'])
        self.assertTrue(self.task(1)['deleted'])
        self.assert_quiet()

    def test_one_stale_selected_task_aborts_whole_batch_until_reconfirmed(self):
        s = self.s
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        self.concurrent_change(1, 'complete')
        before = s.tasks()
        self.confirm()
        self.assertEqual(s.tasks(), before)
        self.assertEqual(self.picked(), {2})
        self.assertFalse(self.task(1)['deleted'])
        self.assertFalse(self.task(2)['deleted'])
        self.confirm()
        self.assertFalse(self.task(1)['deleted'])
        self.assertTrue(self.task(2)['deleted'])
        self.assert_quiet()

    def test_lost_confirmation_edit_response_does_not_remove_twice(self):
        s = self.s
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        before = s.tasks()
        s.state.fail_next('editMessageText', 'disconnect_after')
        update = self.confirm()
        s.processed(s.state.replay(update))
        self.assertIn('Moved 2 tasks to Trash', s.panel()['text'])
        self.assertEqual(self.task(1)['revision'], before[0]['revision'] + 1)
        self.assertEqual(self.task(2)['revision'], before[1]['revision'] + 1)
        self.assert_quiet()

    def test_batch_undo_and_single_selected_trash_restore_preserve_existing_trash(self):
        s = self.s
        self.concurrent_change(7, 'delete')
        preexisting = self.task(7)
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        self.confirm()
        self.undo(PARTNER)
        self.assertFalse(self.task(1)['deleted'])
        self.assertFalse(self.task(2)['deleted'])
        self.assertEqual(self.task(7), preexisting)
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        self.confirm()
        s.click('menu')
        s.click('view:all:trash:0')
        s.click('rpick:1:')
        s.click('batch:restore:')
        self.assertFalse(self.task(1)['deleted'])
        self.assertTrue(self.task(2)['deleted'])
        self.assertEqual(self.task(7), preexisting)
        self.assert_quiet()

    def test_batch_undo_conflict_does_not_partially_restore_other_items(self):
        s = self.s
        self.open_remove()
        s.click('pick:1:')
        s.click('pick:2:')
        self.confirm()
        self.concurrent_change(1, 'restore')
        before = s.tasks()
        self.undo()
        self.assertEqual(s.tasks(), before)
        self.assertFalse(self.task(1)['deleted'])
        self.assertTrue(self.task(2)['deleted'])
        self.assert_quiet()

    def test_distinct_sessions_with_same_short_nonce_do_not_reuse_commit_receipts(self):
        s = self.s
        token_hex = secrets.token_hex

        def fixed_short_nonce(length=None):
            return '1234abcd5678' if length == 6 else ('1234abcd' if length == 4 else token_hex(length))

        with mock.patch('home_bot.bot.secrets.token_hex', side_effect=fixed_short_nonce):
            self.open_remove()
            s.click('pick:1:')
            self.confirm()
            self.undo()
            before = self.task(1)
            self.open_remove()
            s.click('pick:1:')
            self.confirm()
        self.assertTrue(self.task(1)['deleted'])
        self.assertEqual(self.task(1)['revision'], before['revision'] + 1)
        self.assert_quiet()


class BatchRestoreHTTPTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()
        self.change(1, 'complete')
        for task_id in (1, 2, 8):
            self.change(task_id, 'delete')

    def tearDown(self):
        self.s.close()

    def task(self, task_id):
        return next(task for task in self.s.tasks() if task['id'] == task_id)

    def change(self, task_id, action):
        store = Store(self.s.worker.folder)
        try:
            self.assertTrue(store.change_task(task_id, action, store.task(task_id)['revision'], PARTNER))
        finally:
            store.close()

    def archive(self, category):
        store = Store(self.s.worker.folder)
        try:
            store.remove_category(category, PARTNER)
        finally:
            store.close()

    def open_trash(self, uid=OWNER):
        self.s.send('/menu', uid)
        self.s.click('view:all:trash:0', uid)

    def picked(self):
        return {int(b['callback_data'].split(':')[1]) for b in self.s.buttons()
                if b['callback_data'].startswith('rpick:') and b['callback_data'].split(':')[4] == '0'}

    def confirm(self, uid=OWNER):
        return self.s.click('batch:restore:', uid)

    def undo(self, uid=OWNER):
        control = next(b for b in self.s.buttons() if 'Undo restore' in b['text'])
        return self.s.click(control['callback_data'], uid, exact=True)

    def assert_quiet(self):
        self.assertEqual(len(self.s.bot_messages()), 1)
        self.assertEqual(self.s.panel()['message_id'], self.s.bot_messages()[0]['message_id'])
        self.assertEqual(self.s.state.files, {})
        self.assertEqual(list(self.s.worker.folder.glob('backups/*.zip')), [])

    def test_select_mixed_categories_deselect_and_confirm_before_atomic_undo(self):
        s = self.s
        before = s.tasks()
        revision = s.meta('_data_revision')
        self.open_trash()
        self.assertFalse(any(b['callback_data'].startswith(('task:restore:', 'batch:restore:')) for b in s.buttons()))
        for task_id in (1, 8, 2):
            s.click(f'rpick:{task_id}:')
        s.click('rpick:2:')
        self.assertEqual(self.picked(), {1, 8})
        self.assertEqual(s.tasks(), before)
        self.assertEqual(s.meta('_data_revision'), revision)
        confirm = next(b for b in s.buttons() if b['callback_data'].startswith('batch:restore:'))
        self.assertIn('Restore selected (2)', confirm['text'])
        self.confirm()
        self.assertIn('Restored 2 tasks', s.panel()['text'])
        for task_id in (1, 8):
            restored = self.task(task_id)
            original = before[task_id - 1]
            self.assertFalse(restored['deleted'])
            self.assertEqual(restored['category'], original['category'])
            self.assertEqual(restored['done'], original['done'])
            self.assertEqual(restored['revision'], original['revision'] + 1)
        self.assertEqual(self.task(2), before[1])
        self.undo(PARTNER)
        for task_id in (1, 8):
            restored = self.task(task_id)
            self.assertTrue(restored['deleted'])
            self.assertEqual(restored['done'], before[task_id - 1]['done'])
            self.assertEqual(restored['revision'], before[task_id - 1]['revision'] + 2)
        self.assertEqual(self.task(2), before[1])
        self.assert_quiet()

    def test_partner_cannot_change_or_confirm_another_users_restore_selection(self):
        s = self.s
        before = s.tasks()
        self.open_trash()
        s.click('rpick:1:')
        panel = s.panel()
        s.click('rpick:1:', PARTNER)
        s.click('rpick:8:', PARTNER)
        self.confirm(PARTNER)
        self.assertEqual(s.panel(), panel)
        self.assertEqual(self.picked(), {1})
        self.assertEqual(s.tasks(), before)
        self.confirm(OWNER)
        self.assertFalse(self.task(1)['deleted'])
        self.assertTrue(self.task(8)['deleted'])
        self.assert_quiet()

    def test_restore_selection_survives_restart_and_confirm_replay_is_once(self):
        s = self.s
        self.open_trash()
        s.click('rpick:1:')
        s.click('rpick:8:')
        before = s.tasks()
        controls = [b['callback_data'] for b in s.buttons()]
        s.restart()
        self.assertEqual(self.picked(), {1, 8})
        self.assertEqual([b['callback_data'] for b in s.buttons()], controls)
        s.state.fail_next('editMessageText', 'disconnect_after')
        update = self.confirm()
        committed = s.tasks()
        s.restart()
        s.processed(s.state.replay(update))
        self.assertEqual(s.tasks(), committed)
        for task_id in (1, 8):
            self.assertEqual(self.task(task_id)['revision'], before[task_id - 1]['revision'] + 1)
        self.undo()
        self.assertTrue(self.task(1)['deleted'])
        self.assertTrue(self.task(8)['deleted'])
        self.assert_quiet()

    def test_stale_selected_restore_aborts_every_item_then_requires_reconfirmation(self):
        s = self.s
        self.open_trash()
        s.click('rpick:1:')
        s.click('rpick:8:')
        self.change(1, 'restore')
        before = s.tasks()
        self.confirm()
        self.assertEqual(s.tasks(), before)
        self.assertEqual(self.picked(), {8})
        self.assertTrue(self.task(8)['deleted'])
        self.confirm()
        self.assertFalse(self.task(8)['deleted'])
        self.assert_quiet()

    def test_undo_restore_conflict_leaves_entire_mixed_batch_unchanged(self):
        s = self.s
        self.open_trash()
        s.click('rpick:1:')
        s.click('rpick:8:')
        self.confirm()
        self.change(8, 'complete')
        before = s.tasks()
        self.undo()
        self.assertEqual(s.tasks(), before)
        self.assertFalse(self.task(1)['deleted'])
        self.assertFalse(self.task(8)['deleted'])
        self.assert_quiet()

    def test_restore_cancel_and_duplicate_old_picks_do_not_restore_or_reselect(self):
        s = self.s
        before = s.tasks()
        self.open_trash()
        old = s.panel()
        pick = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('rpick:1:'))
        update = s.click(pick, exact=True)
        s.processed(s.state.replay(update))
        s.click(pick, message=old, exact=True)
        self.assertEqual(self.picked(), {1})
        s.click('rpick:1:')
        s.click(pick, message=old, exact=True)
        self.assertEqual(self.picked(), set())
        s.click('rpick:8:')
        selected = s.panel()
        confirm = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('batch:restore:'))
        s.click('menu')
        s.click(confirm, message=selected, exact=True)
        self.assertEqual(s.tasks(), before)
        self.open_trash()
        s.click(pick, message=old, exact=True)
        self.assertEqual(self.picked(), set())
        self.assertEqual(s.tasks(), before)
        self.assert_quiet()

    def test_archived_category_restore_stays_separate_from_individual_task_selection(self):
        s = self.s
        self.archive('household')
        self.open_trash()
        values = [b['callback_data'] for b in s.buttons()]
        self.assertFalse(any(v.startswith('rpick:8:') for v in values))
        self.assertTrue(any(v.startswith('category:restore:household:') for v in values))
        s.click('rpick:1:')
        s.click('category:restore:household:')
        self.assertTrue(self.task(1)['deleted'])
        self.assertTrue(self.task(8)['deleted'])  # Already trashed before category archive.
        self.assertFalse(self.task(9)['deleted'])
        self.open_trash()
        self.assertTrue(any(b['callback_data'].startswith('rpick:8:') for b in s.buttons()))
        self.assertEqual(self.picked(), set())
        self.assert_quiet()

    def test_newly_archived_category_invalidates_selected_trash_without_revision_change(self):
        s = self.s
        self.open_trash()
        s.click('rpick:1:')
        s.click('rpick:8:')
        revision = self.task(8)['revision']
        self.archive('household')
        self.assertEqual(self.task(8)['revision'], revision)
        before = s.tasks()
        self.confirm()
        self.assertEqual(s.tasks(), before)
        self.assertEqual(self.picked(), {1})
        self.confirm()
        self.assertFalse(self.task(1)['deleted'])
        self.assertTrue(self.task(8)['deleted'])
        self.assert_quiet()


if __name__ == '__main__':
    unittest.main()
