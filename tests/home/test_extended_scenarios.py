"""Extended recovery, concurrency, and model-based tests against local Telegram."""
import copy
import html
import json
from pathlib import Path
import random
import threading
import time
import unittest
from unittest import mock

from home_bot.bot import NetworkError
from home_bot.simulator import GROUP, OTHER_GROUP, OWNER, PARTNER, STRANGER
from test_local_telegram import HTTPScenario


class RecoveryScenarios(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def assert_quiet(self):
        s = self.s
        self.assertEqual(len(s.bot_messages()), 1)
        self.assertEqual(s.state.files, {})
        self.assertEqual(list(s.worker.folder.glob('backups/*.zip')), [])
        self.assertIsNone(s.worker.error)

    def begin_add(self, category='home', uid=OWNER):
        self.s.send('/menu', uid)
        self.s.click('cat:' + category, uid)
        self.s.click('add:' + category, uid)
        return self.s.meta(f'pending_{uid}')['prompt_id']

    def test_menu_recovers_deleted_panel_even_when_content_is_cached(self):
        s = self.s
        old_id = s.panel()['message_id']
        s.worker.bot.api.call('deleteMessage', {'chat_id': GROUP, 'message_id': old_id})
        s.send('/menu')
        self.assertNotEqual(s.meta('list_id'), old_id)
        s.click('cat:shopping')
        s.click('task:complete:1:')
        self.assertTrue(s.tasks()[0]['done'])
        self.assert_quiet()

    def test_private_menu_recovers_deleted_panel(self):
        s = self.s
        s.send('/menu', chat=OWNER)
        old_id = s.private_panel(OWNER)['message_id']
        s.worker.bot.api.call('deleteMessage', {'chat_id': OWNER, 'message_id': old_id})
        s.send('/menu', chat=OWNER)
        self.assertNotEqual(s.private_panel(OWNER)['message_id'], old_id)
        self.assertEqual(len(s.bot_messages(OWNER)), 1)
        self.assert_quiet()

    def test_connection_commands_cancel_own_add_before_returning_to_menu(self):
        s = self.s
        for uid in (OWNER, PARTNER):
            for command in ('/start', '/setup'):
                with self.subTest(command=command, uid=uid):
                    mid = self.begin_add(uid=uid)
                    s.send(command, uid)
                    self.assertIsNone(s.meta(f'pending_{uid}'))
                    s.send('This abandoned input must not be added', uid, reply=mid)
                    self.assertEqual(len(s.tasks()), 32)
        self.assert_quiet()

    def test_uncertain_replacement_send_is_not_automatically_duplicated(self):
        s = self.s
        old_id = s.panel()['message_id']
        s.worker.bot.api.call('deleteMessage', {'chat_id': GROUP, 'message_id': old_id})
        s.state.fail_next('sendMessage', 'disconnect_after')
        s.send('/menu')
        self.assertEqual(len(s.bot_messages()), 1)
        delivered = s.bot_messages()[0]
        self.assertIsNone(s.meta('list_id'))
        s.restart()
        self.assertEqual(len(s.bot_messages()), 1)
        s.click('cat:shopping', message=delivered)
        self.assertEqual(s.meta('list_id'), delivered['message_id'])
        s.click('task:complete:1:')
        self.assertTrue(s.tasks()[0]['done'])
        self.assertIsNone(s.meta('panel_send_list_id'))
        self.assert_quiet()

    def test_uncertain_undelivered_panel_is_not_blindly_resent_by_menu(self):
        s = self.s
        old_id = s.panel()['message_id']
        s.worker.bot.api.call('deleteMessage', {'chat_id': GROUP, 'message_id': old_id})
        s.state.fail_next('sendMessage', 'disconnect_before')
        s.send('/menu')
        self.assertEqual(s.bot_messages(), [])
        s.send('/menu')
        self.assertEqual(s.bot_messages(), [])
        self.assertIsNotNone(s.meta('panel_send_list_id'))
        self.assertEqual(s.state.files, {})

    def test_uncertain_private_send_recovers_from_its_existing_button(self):
        s = self.s
        s.state.fail_next('sendMessage', 'disconnect_after')
        s.send('/menu', chat=OWNER)
        self.assertEqual(len(s.bot_messages(OWNER)), 1)
        panel = s.bot_messages(OWNER)[0]
        s.click('members', message=panel, chat=OWNER)
        self.assertEqual(s.private_panel(OWNER)['message_id'], panel['message_id'])
        self.assertEqual(len(s.bot_messages(OWNER)), 1)
        self.assert_quiet()

    def test_restart_preserves_both_pending_additions_and_categories(self):
        s = self.s
        mid = self.begin_add('shopping')
        self.begin_add('study', PARTNER)
        pending = [s.meta(f'pending_{uid}') for uid in (OWNER, PARTNER)]
        s.restart()
        self.assertEqual([s.meta(f'pending_{uid}') for uid in (OWNER, PARTNER)], pending)
        partner_panel = s.panel()
        s.send('Owner after restart', reply=mid)
        self.assertEqual(s.panel(), partner_panel)
        s.send('Partner after restart', PARTNER, reply=s.meta(f'pending_{PARTNER}')['prompt_id'])
        self.assertEqual([(t['category'], t['title']) for t in s.tasks()[32:]],
                         [('shopping', 'Owner after restart'), ('study', 'Partner after restart')])
        self.assert_quiet()

    def test_late_invalid_reply_does_not_replace_partners_screen(self):
        s = self.s
        mid = self.begin_add('shopping')
        self.begin_add('home', PARTNER)
        panel = s.panel()
        for invalid in (' ', 'x' * 241, '\n'.join(str(i) for i in range(21)), 'a\x00b'):
            s.send(invalid, reply=mid)
            self.assertEqual(s.panel(), panel)
            self.assertEqual(len(s.tasks()), 32)
            mid = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send('Valid owner item', reply=mid)
        self.assertEqual(s.panel(), panel)
        s.send('Valid partner item', PARTNER, reply=s.meta(f'pending_{PARTNER}')['prompt_id'])
        self.assertEqual(len(s.tasks()), 34)
        self.assert_quiet()

    def test_repeated_add_delivery_after_restart_and_menu_changes_is_once(self):
        s = self.s
        mid = self.begin_add('household')
        update = s.send('First\nSecond\nThird', reply=mid)
        expected = s.tasks()
        s.restart()
        for _ in range(12):
            s.state.replay(update)
        s.send('/menu', PARTNER)
        s.click('showlist', PARTNER)
        self.assertEqual(s.tasks(), expected)
        self.assertEqual(len(expected), 35)
        self.assert_quiet()

    def test_rapid_double_clicks_apply_each_task_transition_only_once(self):
        s = self.s
        s.click('cat:shopping')
        for action in ('complete', 'reopen') * 12:
            panel = s.panel()
            data = next(b['callback_data'] for b in s.buttons()
                        if b['callback_data'].startswith(f'task:{action}:1:'))
            revision = s.tasks()[0]['revision']
            updates = [s.post('/sim/click', {'message_id': panel['message_id'], 'data': data,
                                           'uid': uid, 'chat_id': GROUP})
                       for uid in (OWNER, PARTNER, OWNER, PARTNER)]
            s.processed(updates[-1])
            task = s.tasks()[0]
            self.assertEqual(task['revision'], revision + 1)
            self.assertEqual(bool(task['done']), action == 'complete')
        self.assert_quiet()

    def test_remove_undo_survives_restart_and_cannot_reverse_an_unrelated_action(self):
        s = self.s
        s.click('cat:shopping')
        s.click('remove:shopping')
        s.click('pick:1:')
        s.click('batch:remove:')
        receipt = s.panel()
        s.restart()
        s.click('undo:', uid=PARTNER)
        self.assertFalse(s.tasks()[0]['deleted'])
        s.click('cat:shopping')
        s.click('remove:shopping')
        s.click('pick:2:')
        s.click('batch:remove:')
        expected = s.tasks()
        s.click('undo:', message=receipt)
        self.assertEqual(s.tasks(), expected)
        self.assert_quiet()

    def test_poll_and_callback_answer_outages_do_not_duplicate_actions(self):
        s = self.s
        s.click('cat:shopping')
        for method in ('getUpdates', 'answerCallbackQuery'):
            for mode in ('500', '429', 'disconnect_before', 'disconnect_after'):
                with self.subTest(method=method, mode=mode):
                    s.state.fail_next(method, mode)
                    before = s.tasks()[0]
                    action = 'reopen' if before['done'] else 'complete'
                    update = s.click(f'task:{action}:1:')
                    s.state.replay(update)
                    s.send('/menu')
                    self.assertEqual(s.tasks()[0]['revision'], before['revision'] + 1)
                    s.click('cat:shopping')
        self.assert_quiet()

    def test_all_unicode_input_boundaries_and_escaping(self):
        s = self.s
        mid = self.begin_add('study')
        titles = ['شیر و موز', 'Français — écouter', '🧗' * 240,
                  '<b>literal & text</b>', "'); DROP TABLE tasks; --"]
        s.send('\r\n'.join(titles), reply=mid)
        self.assertEqual([t['title'] for t in s.tasks()[32:]], titles)
        s.click('menu')
        s.click('cat:study')
        self.assertIn('&lt;b&gt;literal &amp; text&lt;/b&gt;', s.panel()['text'])
        self.assertTrue(all(t in html.unescape(s.panel()['text']) for t in titles))
        mid = self.begin_add('study')
        s.send('🧗' * 241, reply=mid)
        self.assertEqual(len(s.tasks()), 37)
        s.click('cancel:')
        self.assert_quiet()

    def test_backup_permission_failure_requires_another_intentional_selection(self):
        from home_bot.bot import TelegramError
        s = self.s
        original = s.worker.bot.api.call
        def reject(method, *args, **kwargs):
            if method == 'sendDocument':
                raise TelegramError(403, 'Cannot send documents')
            return original(method, *args, **kwargs)
        with mock.patch.object(s.worker.bot.api, 'call', side_effect=reject):
            s.click('backup')
            s.wait_backups()
        self.assertIn('could not be sent', s.panel()['text'])
        self.assertEqual(len(s.state.files), 0)
        self.assertEqual(len(list(s.worker.folder.glob('backups/*.zip'))), 1)
        s.restart()
        s.send('/menu')
        s.click('cat:shopping')
        s.click('task:complete:1:')
        self.assertEqual(len(s.state.files), 0)
        s.click('menu')
        s.click('backup')
        s.wait_backups()
        self.assertEqual(len(s.state.files), 1)

    def test_private_and_group_backups_do_not_mix_targets_or_freeze_tasks(self):
        s = self.s
        entered, release = threading.Event(), threading.Event()
        original = s.worker.bot.api.call
        destinations = []
        def hold_first_upload(method, *args, **kwargs):
            if method == 'sendDocument':
                destinations.append(args[0]['chat_id'])
                if len(destinations) == 1:
                    entered.set()
                    self.assertTrue(release.wait(8))
            return original(method, *args, **kwargs)
        try:
            with mock.patch.object(s.worker.bot.api, 'call', side_effect=hold_first_upload):
                s.click('backup')
                self.assertTrue(entered.wait(3))
                s.send('/menu', uid=PARTNER, chat=PARTNER)
                private_menu = s.private_panel(PARTNER)
                s.click('backup', uid=PARTNER, chat=PARTNER, message=private_menu)
                s.click('cat:shopping')
                s.click('task:complete:1:')
                self.assertTrue(s.tasks()[0]['done'])
                s.click('menu')
                s.click('showlist')
                help_panel = s.panel()
                release.set()
                s.wait_backups()
                self.assertEqual(s.panel(), help_panel)
        finally:
            release.set()
        self.assertEqual(destinations, [GROUP, PARTNER])
        for chat in (GROUP, PARTNER):
            messages = s.bot_messages(chat)
            self.assertEqual(len([m for m in messages if m.get('document')]), 1)
            self.assertEqual(len([m for m in messages if not m.get('document')]), 1)

    def test_stalled_click_acknowledgements_do_not_delay_menu_or_task_updates(self):
        s = self.s
        entered, release = threading.Event(), threading.Event()
        original = s.worker.bot.api.call
        def stalled_answer(method, *args, **kwargs):
            if method == 'answerCallbackQuery':
                entered.set()
                self.assertTrue(release.wait(8))
            return original(method, *args, **kwargs)
        try:
            with mock.patch.object(s.worker.bot.api, 'call', side_effect=stalled_answer):
                started = time.monotonic()
                s.click('cat:shopping')
                self.assertTrue(entered.wait(1))
                s.click('task:complete:1:')
                self.assertTrue(s.tasks()[0]['done'])
                self.assertLess(time.monotonic() - started, 2)
                # A sustained burst remains bounded while both workers stall.
                for _ in range(24):
                    s.click('menu')
                    s.click('cat:shopping')
                self.assertLessEqual(len(s.worker.bot.answer_jobs), 16)
                self.assertIn('Groceries', s.panel()['text'])
                release.set()
        finally:
            release.set()
        self.assert_quiet()

    def test_poll_wait_is_bounded_for_prompt_restart_and_network_recovery(self):
        s = self.s
        s.send('/menu')
        calls = [c for c in s.state.calls if c['method'] == 'getUpdates']
        self.assertTrue(calls)
        self.assertTrue(all(c['payload']['timeout'] <= 5 for c in calls))

    def test_deleted_input_panel_recovers_without_losing_submitted_task(self):
        s = self.s
        mid = self.begin_add('home')
        panel_id = s.panel()['message_id']
        # A reply can already be queued when somebody deletes the bot panel.
        s.worker.stop()
        update = s.post('/sim/message', {'text': 'Saved despite deleted panel', 'uid': OWNER,
                                        'chat_id': GROUP, 'reply_id': mid})
        s.worker.bot.api.call('deleteMessage', {'chat_id': GROUP, 'message_id': panel_id})
        s.restart()
        s.processed(update)
        self.assertEqual(s.tasks()[-1]['title'], 'Saved despite deleted panel')
        self.assertEqual(s.tasks()[-1]['category'], 'home')
        self.assertIn('Added 1 task', s.panel()['text'])
        self.assertNotEqual(s.panel()['message_id'], panel_id)
        self.assert_quiet()

    def test_stranger_and_other_bot_commands_cannot_disturb_pending_input(self):
        s = self.s
        mid = self.begin_add()
        before = s.panel()
        for command in ('/menu', '/backup', '/add', '/setup', '/start'):
            s.send(command, STRANGER)
            # /setup and /start may explain setup in an unrelated group;
            # neither is permitted to change the real shared panel.
            s.send(command, OWNER, chat=OTHER_GROUP)
        s.send('/menu@another_bot')
        s.send('Stranger reply', STRANGER, reply=mid)
        self.assertEqual(s.panel(), before)
        self.assertEqual(len(s.tasks()), 32)
        s.send('Authorized reply', reply=mid)
        self.assertEqual(len(s.tasks()), 33)
        self.assert_quiet()


class ModelBasedScenarios(unittest.TestCase):
    def test_four_seeded_household_runs_match_independent_task_model(self):
        report = {'seeds': [], 'steps': 0, 'task_actions': 0, 'adds': 0,
                  'duplicate_deliveries': 0, 'restarts': 0, 'passed': False}
        started = time.monotonic()
        for seed in (6, 98, 20261109, 8675309):
            s = HTTPScenario()
            rng = random.Random(seed)
            model = {t['id']: {k: t[k] for k in ('title', 'category', 'done', 'deleted', 'revision')}
                     for t in s.tasks()}
            receipts = {}
            category_receipts = {}
            selection_owner, selected, selection_action = None, {}, None
            stats = {'seed': seed, 'steps': 0, 'actions': {}}
            try:
                for step in range(300):
                    uid = rng.choice((OWNER, PARTNER))
                    if step and step % 75 == 0:
                        s.restart()
                        report['restarts'] += 1
                    buttons = [b for b in s.buttons() if b['callback_data'] != 'backup']
                    task_buttons = [b for b in buttons if b['callback_data'].startswith(('task:', 'pick:', 'rpick:', 'batch:'))]
                    chosen = rng.choice(task_buttons if task_buttons and rng.random() < .65 else buttons)
                    value = chosen['callback_data']
                    family = value.split(':')[0]
                    stats['actions'][family] = stats['actions'].get(family, 0) + 1
                    before_panel = s.panel()
                    update = s.click(value, uid, exact=True)
                    if family in {'menu', 'cat', 'view', 'add', 'category', 'showlist', 'finish'}:
                        selection_owner, selected, selection_action = None, {}, None
                    if family == 'remove':
                        selection_owner, selected, selection_action = uid, {}, 'remove'
                    elif value.startswith('view:all:trash:'):
                        selection_owner, selected, selection_action = uid, {}, 'restore'
                    elif family in {'pick', 'rpick'}:
                        _, tid, revision, _, desired, _ = value.split(':')
                        if uid == selection_owner:
                            if desired == '1':
                                selected[int(tid)] = int(revision)
                            else:
                                selected.pop(int(tid), None)
                    elif family == 'batch':
                        if uid == selection_owner:
                            self.assertEqual(value.split(':')[1], selection_action)
                            previous_tasks = []
                            for tid, revision in selected.items():
                                self.assertEqual(model[tid]['revision'], revision)
                                previous = (model[tid]['done'], model[tid]['deleted'])
                                model[tid]['deleted'] = int(selection_action == 'remove')
                                model[tid]['revision'] += 1
                                previous_tasks.append((tid, previous, model[tid]['revision']))
                            for b in s.buttons():
                                if b['callback_data'].startswith('undo:'):
                                    receipts[b['callback_data']] = previous_tasks
                            report['task_actions'] += len(previous_tasks)
                    elif family == 'finish':
                        category = value.split(':')[1]
                        previous_tasks = []
                        for tid, task in model.items():
                            if task['category'] == category and not task['deleted']:
                                previous = (task['done'], task['deleted'])
                                task['deleted'] = 1
                                task['revision'] += 1
                                previous_tasks.append((tid, previous, task['revision']))
                        for b in s.buttons():
                            if b['callback_data'].startswith('undo:'):
                                receipts[b['callback_data']] = previous_tasks
                        report['task_actions'] += len(previous_tasks)
                    elif value.startswith('category:remove:'):
                        category = value.split(':')[2]
                        tids = [tid for tid, task in model.items() if task['category'] == category and not task['deleted']]
                        for tid in tids:
                            model[tid]['deleted'] = 1
                            model[tid]['revision'] += 1
                        entry = next(c for c in s.meta('removed_categories') if c['key'] == category)
                        category_receipts[entry['token']] = tids
                    elif value.startswith('category:restore:'):
                        token = value.split(':')[3]
                        for tid in category_receipts[token]:
                            model[tid]['deleted'] = 0
                            model[tid]['revision'] += 1
                    elif family == 'task':
                        _, action, tid, revision = value.split(':')
                        task = model[int(tid)]
                        self.assertEqual(task['revision'], int(revision))
                        previous = (task['done'], task['deleted'])
                        if action in ('complete', 'reopen'):
                            task['done'] = int(action == 'complete')
                        else:
                            task['deleted'] = int(action == 'delete')
                        task['revision'] += 1
                        for b in s.buttons():
                            if b['callback_data'].startswith('undo:'):
                                receipts[b['callback_data']] = [(int(tid), previous, task['revision'])]
                        report['task_actions'] += 1
                    elif family == 'undo':
                        for tid, previous, revision in receipts[value]:
                            self.assertEqual(model[tid]['revision'], revision)
                            model[tid]['done'], model[tid]['deleted'] = previous
                            model[tid]['revision'] += 1
                            report['task_actions'] += 1
                    elif family == 'add':
                        # Mix successful replies with validation/cancellation/navigation.
                        if rng.random() < .7:
                            title = f'Seed {seed} item {step}'
                            new_id = max(model) + 1
                            reply = s.send(title, uid, reply=s.meta(f'pending_{uid}')['prompt_id'])
                            model[new_id] = {'title': title, 'category': value.split(':')[1],
                                             'done': 0, 'deleted': 0, 'revision': 1}
                            report['adds'] += 1
                            if rng.random() < .3:
                                s.state.replay(reply)
                                report['duplicate_deliveries'] += 1
                    if rng.random() < .12:
                        s.state.replay(update)
                        report['duplicate_deliveries'] += 1
                        # Barrier leaves the view unchanged: unauthorized callback.
                        s.click('menu', STRANGER, message=before_panel, exact=True) if any(
                            b['callback_data'] == 'menu' for row in before_panel.get('reply_markup', {}).get('inline_keyboard', [])
                            for b in row) else s.send('/menu', STRANGER)
                    actual = {t['id']: {k: t[k] for k in ('title', 'category', 'done', 'deleted', 'revision')}
                              for t in s.tasks()}
                    self.assertEqual(actual, model, (seed, step, value))
                    permanent = [m for m in s.bot_messages() if not m.get('reply_markup', {}).get('force_reply')]
                    self.assertEqual(len(permanent), 1, (seed, step))
                    self.assertLessEqual(len(s.bot_messages()), 3, (seed, step))
                    self.assertEqual(s.state.files, {}, (seed, step))
                    self.assertEqual(s.meta('screen'), s.meta('panel_state_list_id')['screen'])
                    stats['steps'] += 1
                    report['steps'] += 1
                self.assertEqual(list(s.worker.folder.glob('backups/*.zip')), [])
                self.assertEqual(s.query('PRAGMA integrity_check')[0]['integrity_check'], 'ok')
                report['seeds'].append(stats)
            finally:
                s.close()
        report['seconds'] = round(time.monotonic() - started, 2)
        report['passed'] = True
        path = Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',__import__('tempfile').gettempdir())) / 'home-extended-model.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
