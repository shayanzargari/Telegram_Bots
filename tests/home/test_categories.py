"""Category creation, scoped actions, migration, and portable backups."""
import contextlib
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from home_bot.simulator import OWNER, PARTNER
from home_bot.storage import Store
from test_local_telegram import HTTPScenario


class CategoryStorageTests(unittest.TestCase):
    def test_categories_round_trip_with_tasks_and_legacy_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            try:
                category = store.add_category('Weekend projects', OWNER, 'category-update-1')
                revision = store.revision()
                self.assertEqual(store.add_category('Weekend projects', OWNER, 'category-update-1'), category)
                self.assertEqual(store.revision(), revision)
                tid = store.add_task(category, 'Paint the chair', OWNER)
                store.change_task(tid, 'complete', 1, OWNER)
                self.assertEqual(store.counts()[category]['done'], 1)
                export = store.export_latest()
                self.assertIn('Weekend projects', export['html'].read_text('utf-8'))
                self.assertEqual(list(store.backups_dir.glob('*.zip')), [])
                backup = store.create_backup()
                with zipfile.ZipFile(backup) as archive:
                    data = json.loads(archive.read('tasks.json'))
                    self.assertEqual(data['settings']['custom_categories'][0]['key'], category)
                store.add_category('Later category', PARTNER)
                store.restore_backup(backup)
                self.assertEqual(list(store.categories().values())[-1], 'Weekend projects')
                self.assertTrue(store.task(tid)['done'])
                self.assertEqual(store.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                legacy = copy.deepcopy(data)
                legacy['tasks'] = []
                legacy['settings'].pop('custom_categories')
                path = Path(temporary) / 'legacy.json'
                path.write_text(json.dumps(legacy), encoding='utf-8')
                store.restore_backup(path)
                self.assertEqual(len(store.categories()), 5)
            finally:
                store.close()

    def test_invalid_categories_and_backups_leave_data_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary))
            try:
                category = store.add_category('Travel', OWNER)
                store.add_task(category, 'Passport', OWNER)
                before = store.revision()
                for name in (' travel ', 'TRAVEL', 'Groceries', '', 'x' * 25, 'Bad\nName', 'a\x00b'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        store.add_category(name, OWNER)
                self.assertEqual(store.revision(), before)
                original = json.loads(store.export_latest()['json'].read_text('utf-8'))
                for setting in ([], [{'key': 'all', 'label': 'Bad'}],
                                [{'key': category, 'label': 'Travel'}, {'key': category, 'label': 'Other'}]):
                    invalid = copy.deepcopy(original)
                    invalid['settings']['custom_categories'] = setting
                    path = Path(temporary) / 'invalid.json'
                    path.write_text(json.dumps(invalid), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        store.restore_backup(path)
                    self.assertEqual(store.revision(), before)
                self.assertEqual(list(store.backups_dir.glob('*.zip')), [])
            finally:
                store.close()


class CategoryInteractionTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def create(self, name, uid=OWNER):
        s = self.s
        s.send('/menu', uid)
        s.click('category:add', uid)
        update = s.send(name, uid, reply=s.meta(f'pending_{uid}')['prompt_id'])
        return s.meta('custom_categories')[-1]['key'], update

    def quiet(self):
        s = self.s
        self.assertEqual(len(s.bot_messages()), 1)
        self.assertEqual(s.state.files, {})
        self.assertEqual(list(s.worker.folder.glob('backups/*.zip')), [])

    def test_main_menu_and_category_controls_match_requested_layout(self):
        s = self.s
        values = [b['callback_data'] for b in s.buttons()]
        self.assertEqual(values[:2], ['category:add', 'cat:shopping'])
        self.assertNotIn('add:choose', values)
        self.assertEqual(values[-1], 'backup')
        self.assertEqual(values.count('backup'), 1)
        self.assertNotIn('help', values)
        self.assertFalse(any(v.startswith(('remove:', 'finish:')) for v in values))
        for category in ('shopping', 'household', 'admin', 'home', 'study'):
            s.click('cat:' + category)
            values = [b['callback_data'] for b in s.buttons()]
            self.assertTrue({f'add:{category}', f'remove:{category}'} <= set(values))
            self.assertTrue(any(v.startswith(f'finish:{category}:') for v in values))
            s.click('task:complete:')
            self.assertFalse(any(b['callback_data'].startswith('undo:') for b in s.buttons()))
            s.click('task:reopen:')
            s.click('menu')
        self.quiet()

    def test_custom_category_done_clears_and_undo_restores_only_its_tasks(self):
        s = self.s
        original = s.tasks()
        category, _ = self.create('Trips & <family>')
        s.click('cat:' + category)
        self.assertIn('Trips &amp; &lt;family&gt;', s.panel()['text'])
        s.click('add:' + category)
        s.send('Book hotel\nPack bags', reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        s.click('cat:' + category)
        s.click('task:complete:33:')
        s.click(f'finish:{category}:')
        self.assertTrue(all(t['deleted'] for t in s.tasks()[32:]))
        self.assertIn('Moved 2 tasks to Trash', s.panel()['text'])
        s.click('undo:')
        self.assertFalse(any(t['deleted'] for t in s.tasks()[32:]))
        self.assertTrue(s.tasks()[32]['done'])
        self.assertEqual(s.meta('screen'), 'list')
        self.assertEqual(s.meta('view')['category'], category)
        s.click('remove:' + category)
        picks = [b['callback_data'] for b in s.buttons() if b['callback_data'].startswith('pick:')]
        self.assertEqual({int(v.split(':')[1]) for v in picks}, {33, 34})
        s.click('pick:33:')
        self.assertFalse(s.tasks()[32]['deleted'])
        s.click('batch:remove:')
        s.click('undo:')
        self.assertEqual(s.tasks()[:32], original)
        self.assertFalse(s.tasks()[32]['deleted'])
        self.quiet()

    def test_category_creation_retry_restart_and_duplicate_names(self):
        s = self.s
        s.click('category:add')
        mid = s.meta(f'pending_{OWNER}')['prompt_id']
        for invalid in (' ', 'x' * 25, 'Two\nNames', 'Groceries', 'a\x00b'):
            s.send(invalid, reply=mid)
            self.assertEqual(s.meta('custom_categories'), None)
            self.assertEqual(s.meta('screen'), 'input')
            mid = s.meta(f'pending_{OWNER}')['prompt_id']
        s.state.fail_next('editMessageText', 'disconnect_after')
        update = s.send('Travel', reply=mid)
        s.state.replay(update)
        s.restart()
        s.send('/menu')
        self.assertEqual(len(s.meta('custom_categories')), 1)
        s.click('category:add')
        s.send(' TRAVEL ', reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.assertIn('already exists', s.panel()['text'])
        self.assertEqual(len(s.meta('custom_categories')), 1)
        s.click('cancel:')
        self.quiet()

    def test_category_and_task_additions_from_two_people_stay_separate(self):
        s = self.s
        s.click('category:add')
        mid = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send('/menu', PARTNER)
        s.click('cat:home', PARTNER)
        s.click('add:home', PARTNER)
        partner_panel = s.panel()
        s.send('Garden', reply=mid)
        self.assertEqual(s.panel(), partner_panel)
        s.send('Partner home task', PARTNER, reply=s.meta(f'pending_{PARTNER}')['prompt_id'])
        self.assertEqual(s.tasks()[-1]['category'], 'home')
        self.assertEqual(s.meta('custom_categories')[0]['label'], 'Garden')
        s.send('/menu')
        s.click('category:add')
        s.click('menu')
        s.send('Cancelled category', reply=mid)
        self.assertEqual(len(s.meta('custom_categories')), 1)
        self.quiet()

    def test_custom_category_in_manual_telegram_backup(self):
        s = self.s
        category, _ = self.create('Weekend projects')
        s.click('cat:' + category)
        s.click('add:' + category)
        s.send('Build a shelf', reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.quiet()
        s.click('menu')
        s.click('backup')
        s.wait_backups()
        import io
        _, content = next(iter(s.state.files.values()))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            data = json.loads(archive.read('tasks.json'))
            self.assertEqual(data['settings']['custom_categories'][0]['key'], category)
            self.assertEqual(data['tasks'][-1]['category'], category)
            self.assertIn('Weekend projects', archive.read('tasks.html').decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
