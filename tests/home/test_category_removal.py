import copy
import json
from pathlib import Path
import tempfile
import unittest

from home_bot.simulator import OWNER, PARTNER
from home_bot.storage import Store
from test_local_telegram import HTTPScenario


class CategoryRemovalStorageTests(unittest.TestCase):
    def test_archive_restore_preserves_checks_and_preexisting_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                open_id = store.add_task('home', 'Desk', OWNER)
                done_id = store.add_task('home', 'Mirror', OWNER)
                trash_id = store.add_task('home', 'Old shelf', OWNER)
                store.change_task(done_id, 'complete', 1, OWNER)
                store.change_task(trash_id, 'delete', 1, OWNER)
                before = {tid: store.task(tid) for tid in (open_id, done_id, trash_id)}
                removed = store.remove_category('home', OWNER)
                revision = store.revision()
                self.assertNotIn('home', store.categories())
                self.assertEqual(store.remove_category('home', PARTNER), removed)
                self.assertEqual(store.revision(), revision)
                self.assertEqual(store.task(trash_id), before[trash_id])
                self.assertTrue(store.task(done_id)['done'])
                self.assertFalse(store.change_task(open_id, 'restore', 2, OWNER))
                with self.assertRaises(ValueError):
                    store.add_task('home', 'Late input', OWNER)
                self.assertTrue(store.restore_category('home', removed['token'], PARTNER))
                self.assertIn('home', store.categories())
                self.assertFalse(store.task(open_id)['deleted'])
                self.assertTrue(store.task(done_id)['done'])
                self.assertEqual(store.task(trash_id), before[trash_id])
                second = store.remove_category('home', OWNER)
                self.assertNotEqual(second['token'], removed['token'])
                self.assertFalse(store.restore_category('home', removed['token'], OWNER))
                self.assertEqual(list(store.backups_dir.glob('*.zip')), [])
            finally:
                store.close()

    def test_archived_custom_and_empty_categories_survive_backup_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                custom = store.add_category('Garden', OWNER)
                tid = store.add_task(custom, 'Buy seeds', OWNER)
                removed = store.remove_category(custom, OWNER)
                empty = store.remove_category('study', OWNER)
                backup = store.create_backup()
                store.restore_category(custom, removed['token'], OWNER)
                store.restore_backup(backup)
                self.assertNotIn(custom, store.categories())
                self.assertNotIn('study', store.categories())
                restored_tokens = {entry['key']: entry['token'] for entry in store.get('removed_categories')}
                self.assertFalse(store.restore_category(custom, removed['token'], PARTNER))
                self.assertTrue(store.restore_category(custom, restored_tokens[custom], PARTNER))
                self.assertFalse(store.task(tid)['deleted'])
                self.assertTrue(store.restore_category('study', restored_tokens['study'], PARTNER))
                self.assertEqual(store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                store.close()

    def test_bad_archive_metadata_cannot_remove_or_restore_unrelated_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                store.add_task('home', 'Desk', OWNER)
                other = store.add_task('study', 'French', OWNER)
                entry = store.remove_category('home', OWNER)
                data = json.loads(store.export_latest()['json'].read_text('utf-8'))
                revision = store.revision()
                for altered in ([other], [entry['task_ids'][0], entry['task_ids'][0]], [999999]):
                    invalid = copy.deepcopy(data)
                    invalid['settings']['removed_categories'][0]['task_ids'] = altered
                    path = Path(directory) / 'invalid.json'
                    path.write_text(json.dumps(invalid), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        store.restore_backup(path)
                    self.assertEqual(store.revision(), revision)
                self.assertEqual(list(store.backups_dir.glob('*.zip')), [])
            finally:
                store.close()


class CategoryRemovalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def assert_quiet(self):
        self.assertEqual(len(self.s.bot_messages()), 1)
        self.assertEqual(self.s.state.files, {})
        self.assertEqual(list(self.s.worker.folder.glob('backups/*.zip')), [])

    def test_name_prompt_opens_reply_and_disappears_when_saved(self):
        s = self.s
        mid = s.panel()['message_id']
        s.click('category:add')
        prompt_id = s.meta(f'pending_{OWNER}')['prompt_id']
        self.assertNotEqual(prompt_id, mid)
        prompt = next(m for m in s.bot_messages() if m['message_id'] == prompt_id)
        self.assertTrue(prompt['reply_markup']['force_reply'])
        self.assertTrue(prompt['reply_markup']['selective'])
        self.assertIn(f'tg://user?id={OWNER}', prompt['text'])
        s.send('Garden', reply=prompt_id)
        self.assertEqual(s.meta('custom_categories')[0]['label'], 'Garden')
        self.assertEqual(s.panel()['message_id'], mid)
        self.assert_quiet()

    def test_invalid_name_reopens_typing_and_cancel_removes_prompt(self):
        s = self.s
        s.click('category:add')
        old = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send('Groceries', reply=old)
        new = s.meta(f'pending_{OWNER}')['prompt_id']
        self.assertNotEqual(new, old)
        self.assertEqual(len(s.bot_messages()), 2)
        self.assertIn('already exists', s.panel()['text'])
        s.click('cancel:')
        self.assert_quiet()

    def test_uncertain_prompt_delivery_recovers_from_typed_reply_without_duplicate(self):
        s = self.s
        s.state.fail_next('sendMessage', 'disconnect_after')
        s.click('category:add')
        self.assertEqual(len(s.bot_messages()), 2)
        prompt = next(m for m in s.bot_messages() if m.get('reply_markup', {}).get('force_reply'))
        s.send('Travel', reply=prompt['message_id'])
        self.assertEqual(len(s.meta('custom_categories')), 1)
        self.assert_quiet()

    def test_category_actions_keep_long_labels_on_full_width_rows(self):
        s = self.s
        for category in ('shopping', 'household', 'admin', 'home', 'study'):
            s.click('cat:' + category)
            rows = s.panel()['reply_markup']['inline_keyboard']
            self.assertEqual([[b['text'] for b in row] for row in rows[-5:]],
                             [['➕ Add task'], ['➖ Remove task'], ['🗑️ Remove category'], ['✅ Done'], ['🏠 Main menu']])
            s.click('menu')
        self.assert_quiet()

    def test_remove_category_restore_from_trash_after_restart(self):
        s = self.s
        s.click('cat:shopping')
        s.click('task:complete:1:')
        s.click('remove:shopping')
        s.click('pick:2:')
        s.click('batch:remove:')
        prior = s.tasks()
        s.click('cat:shopping')
        s.click('category:remove:shopping')
        self.assertEqual([b['text'] for b in s.buttons()],
                         ['↩️ Undo remove category', '🏠 Main menu'])
        s.click('category:restore:shopping:', PARTNER)
        self.assertEqual(s.meta('screen'), 'list')
        self.assertEqual(s.meta('view')['category'], 'shopping')
        self.assertEqual([(t['done'], t['deleted']) for t in s.tasks()],
                         [(t['done'], t['deleted']) for t in prior])
        s.click('category:remove:shopping', PARTNER)
        self.assertEqual(sum(t['deleted'] for t in s.tasks()[:7]), 7)
        self.assertTrue(s.tasks()[0]['done'])
        s.click('menu')
        self.assertNotIn('cat:shopping', [b['callback_data'] for b in s.buttons()])
        s.restart()
        s.click('view:all:trash:0')
        self.assertFalse(any(b['callback_data'].startswith(('rpick:', 'task:restore:')) for b in s.buttons()))
        s.click('category:restore:shopping:')
        for before, after in zip(prior, s.tasks()):
            for key in ('id', 'title', 'category', 'done', 'deleted'):
                self.assertEqual(before[key], after[key])
        self.assert_quiet()

    def test_category_undo_reopens_after_lost_response_and_duplicate_delivery(self):
        s = self.s
        s.click('cat:home')
        s.click('category:remove:home')
        s.state.fail_next('editMessageText', 'disconnect_after')
        update = s.click('category:restore:home:', PARTNER)
        restored = s.tasks()
        s.restart()
        s.processed(s.state.replay(update))
        self.assertEqual(s.tasks(), restored)
        self.assertEqual(s.meta('screen'), 'list')
        self.assertEqual(s.meta('view')['category'], 'home')
        self.assertIn('add:home', [b['callback_data'] for b in s.buttons()])
        self.assert_quiet()

    def test_removing_category_cancels_its_pending_task_without_crossing_other_inputs(self):
        s = self.s
        s.click('cat:home')
        s.click('add:home')
        mid = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send('/menu', PARTNER)
        s.click('cat:home', PARTNER)
        s.click('category:remove:home', PARTNER)
        panel = s.panel()
        s.send('Late home task', reply=mid)
        self.assertEqual(len(s.tasks()), 32)
        self.assertEqual(s.panel(), panel)
        s.click('category:restore:home:')
        self.assert_quiet()

    def test_two_category_prompts_are_user_bound_and_one_does_not_cancel_the_other(self):
        s = self.s
        s.click('category:add')
        first = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send('/menu', PARTNER)
        s.click('category:add', PARTNER)
        second = s.meta(f'pending_{PARTNER}')['prompt_id']
        panel = s.panel()
        s.send('Wrong user', PARTNER, reply=first)
        self.assertIsNone(s.meta('custom_categories'))
        s.send('Garden', reply=first)
        self.assertEqual(s.panel(), panel)
        s.send('Work', PARTNER, reply=second)
        self.assertEqual([c['label'] for c in s.meta('custom_categories')], ['Garden', 'Work'])
        self.assert_quiet()

    def test_name_errors_reopen_only_the_callers_prompt_without_replacing_partner_screen(self):
        s = self.s
        s.click('category:add')
        s.send('/menu', PARTNER)
        s.click('cat:study', PARTNER)
        partner_screen = s.panel()
        for invalid in ('x' * 25, 'Groceries'):
            prompt_id = s.meta(f'pending_{OWNER}')['prompt_id']
            s.send(invalid, reply=prompt_id)
            self.assertEqual(s.panel(), partner_screen)
            new_id = s.meta(f'pending_{OWNER}')['prompt_id']
            self.assertNotEqual(prompt_id, new_id)
            self.assertEqual(len(s.bot_messages()), 2)
        s.send('Garden', reply=new_id)
        self.assertEqual(s.panel(), partner_screen)
        self.assert_quiet()


if __name__ == '__main__':
    unittest.main()
