"""Deterministic regressions discovered by the comprehensive bot audit."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

SOURCE = Path(__import__('home_bot').__file__).resolve().parent
from home_bot.bot import Bot, NetworkError, Telegram, TelegramError, startup_call
from home_bot.simulator import GROUP, OWNER
import test_bot
from test_local_telegram import HTTPScenario


class BotAuditTests(unittest.TestCase):
    def setUp(self):
        self.h = test_bot.BotTests()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_new_menu_does_not_resend_unknown_delivered_panel(self):
        h = self.h
        h.store.set('list_id', None)
        h.bot.render_cache.clear()
        send = h.api.send

        def delivered_then_lost(*args, **kwargs):
            send(*args, **kwargs)
            raise NetworkError('response lost after accepting send')

        with mock.patch.object(h.api, 'send', side_effect=delivered_then_lost):
            with self.assertRaises(NetworkError):
                h.send('/menu')
        before = sum(m == 'sendMessage' for m, _, _ in h.api.calls)
        h.send('/menu')
        self.assertEqual(sum(m == 'sendMessage' for m, _, _ in h.api.calls), before)
        self.assertIsNotNone(h.store.get('panel_send_list_id'))

    def test_idle_expired_category_prompt_is_removed(self):
        h = self.h
        h.click('category:add')
        pending = h.store.get('pending_101')
        prompt_id = pending['prompt_id']
        pending['expires'] = time.time() - 1
        h.store.set('pending_101', pending)
        h.bot.maintenance()
        self.assertIsNone(h.store.get('pending_101'))
        self.assertNotIn((h.GROUP, prompt_id), h.api.messages)
        self.assertIn('expired', h.panel()['text'].lower())

    def test_expiring_owner_input_does_not_take_partner_screen(self):
        h = self.h
        h.click('category:add')
        pending = h.store.get('pending_101')
        pending['expires'] = time.time() - 1
        h.store.set('pending_101', pending)
        h.send('/menu', h.MEMBER)
        h.click('cat:study', h.MEMBER)
        before = copy.deepcopy(h.panel())
        h.bot.maintenance()
        self.assertIsNone(h.store.get('pending_101'))
        self.assertEqual(h.panel(), before)

    def test_cancelled_unknown_prompt_is_recovered_and_deleted_from_late_reply(self):
        h = self.h
        send = h.api.send

        def delivered_then_lost(*args, **kwargs):
            send(*args, **kwargs)
            raise NetworkError('prompt response lost')

        with mock.patch.object(h.api, 'send', side_effect=delivered_then_lost):
            with self.assertRaises(NetworkError):
                h.click('category:add')
        prompt_id = h.api.message_id
        prompt = copy.deepcopy(h.api.messages[(h.GROUP, prompt_id)])
        prompt.update(message_id=prompt_id, chat={'id': h.GROUP},
                      **{'from': {'is_bot': True, 'username': 'testbot'}})
        h.send('/menu')
        h.sequence += 1
        h.bot.handle({'update_id': h.sequence, 'message': {
            'message_id': 9001, 'chat': {'id': h.GROUP, 'type': 'supergroup'},
            'from': {'id': h.OWNER}, 'text': 'Cancelled name', 'reply_to_message': prompt}})
        self.assertNotIn((h.GROUP, prompt_id), h.api.messages)
        self.assertEqual(h.store.get('custom_categories', []), [])

    def test_failed_snapshot_edit_recovers_from_persisted_panel(self):
        h = self.h
        h.api.fail_once['editMessageText'] = NetworkError('edit response lost')
        with self.assertRaises(NetworkError):
            h.click('showlist')
        h.bot = Bot(h.store, h.api, 'testbot', h.directory)
        h.store.set('ui_dirty', True)
        h.bot.maintenance()
        self.assertIn('Updated list', h.panel()['text'])
        self.assertFalse(h.store.get('ui_dirty'))

    def test_old_category_remove_cannot_delete_after_restore(self):
        h = self.h
        h.click('cat:shopping')
        stale = next(b['callback_data'] for b in h.buttons()
                     if b['callback_data'].startswith('category:remove:shopping'))
        h.click('category:remove:shopping')
        h.click('category:restore:shopping:')
        h.send('/menu')
        h.click('cat:shopping')
        before = h.store.task(h.milk)
        h.bot.handle({'update_id': 9000, 'callback_query': {
            'id': 'old-category-removal', 'from': {'id': h.MEMBER}, 'data': stale,
            'message': {'message_id': h.store.get('list_id'),
                        'chat': {'id': h.GROUP, 'type': 'supergroup'}}}})
        self.assertIn('shopping', h.store.categories())
        self.assertEqual(h.store.task(h.milk), before)

    def test_background_export_does_not_block_menu_and_handles_newer_revision(self):
        h = self.h
        entered, release = threading.Event(), threading.Event()

        def stalled():
            entered.set()
            release.wait(3)
            return h.store.revision() if threading.current_thread() is threading.main_thread() else -1

        # Baseline exposes no worker; stall its synchronous export to reproduce.
        target = '_export_worker' if hasattr(h.bot, '_export_worker') else 'refresh_exports'
        h.bot.in_polling_loop = True
        h.store.set('exports_dirty', True)
        started = time.perf_counter()
        try:
            with mock.patch.object(h.bot, target, side_effect=stalled):
                h.bot.maintenance()
                self.assertLess(time.perf_counter() - started, 0.5)
                self.assertTrue(entered.wait(1))
                h.send('/menu')
                self.assertEqual(h.store.get('screen'), 'menu')
                h.store.add_task('home', 'Newer while export waits', h.OWNER)
                h.bot.changed()
                release.set()
                h.bot.export_job.result(timeout=2)
                h.bot.collect_exports()
                self.assertTrue(h.store.get('exports_dirty'))
        finally:
            release.set()
            if getattr(h.bot, 'export_executor', None):
                h.bot.export_executor.shutdown(wait=True)
            if h.bot.answer_executor:
                h.bot.answer_executor.shutdown(wait=True)
            h.bot.in_polling_loop = False

    def test_all_main_option_pairs_reject_stale_cross_screen_controls(self):
        h = self.h
        choices = ['category:add', 'cat:shopping', 'cat:household',
                   'cat:admin', 'cat:home', 'cat:study', 'showlist', 'view:all:trash:0']
        tested = 0
        for destination in choices:
            for old in choices + ['backup']:
                if destination == old:
                    continue
                h.send('/menu')
                stale = next(b['callback_data'] for b in h.buttons() if b['callback_data'] == old)
                h.click(destination)
                before = copy.deepcopy(h.panel())
                revision = h.store.revision()
                h.bot.handle({'update_id': 10000 + tested, 'callback_query': {
                    'id': f'stale-pair-{tested}', 'from': {'id': h.OWNER}, 'data': stale,
                    'message': {'message_id': h.store.get('list_id'), 'chat': {'id': h.GROUP}}}})
                self.assertEqual(h.panel(), before, (destination, old))
                self.assertEqual(h.store.revision(), revision)
                tested += 1
        self.assertEqual(tested, 64)
        self.assertFalse(any(m == 'sendDocument' for m, _, _ in h.api.calls))

    def test_cancelled_task_reply_cannot_enter_new_category_request(self):
        h = self.h
        old_prompt = h.add('home')
        h.send('/menu')
        new_prompt = h.add('study')
        self.assertNotEqual(old_prompt, new_prompt)
        before = h.store.list_tasks(mode='all')
        h.send('Late old home input', reply=old_prompt)
        self.assertEqual(h.store.list_tasks(mode='all'), before)
        self.assertEqual(h.store.get('pending_101')['prompt_id'], new_prompt)
        h.send('New study input', reply=new_prompt)
        self.assertEqual(h.store.list_tasks('study', 'all')[-1]['title'], 'New study input')
        self.assertEqual(len(h.api.messages), 1)

    def test_all_ordered_category_control_pairs_and_longer_chains(self):
        h = self.h
        controls = ('add', 'remove', 'done', 'remove_category', 'main')
        h.store.change_task(h.milk, 'complete', 1, h.OWNER)
        original = {t['id']: (t['title'], t['category'], t['done'], t['deleted'])
                    for t in h.store.list_tasks(mode='all')}
        pairs = 0

        def exercise(category, action):
            before = h.store.revision()
            if action == 'add':
                h.click('add:' + category)
                pending = h.store.get('pending_101')
                self.assertEqual(pending['category'], category)
                self.assertTrue(h.api.messages[(h.GROUP, pending['prompt_id'])]['reply_markup']['force_reply'])
                h.click('cancel:')
                self.assertIsNone(h.store.get('pending_101'))
                h.click('cat:' + category)
            elif action == 'remove':
                h.click('remove:' + category)
                self.assertTrue(h.store.get('remove_mode'))
                picks = [b for b in h.buttons() if b['callback_data'].startswith('pick:')]
                self.assertEqual(len(picks), len(h.store.list_tasks(category, 'all')))
                self.assertFalse(any(b['callback_data'].startswith(('task:delete:', 'batch:remove:'))
                                     for b in h.buttons()))
                h.click('cat:' + category)
            elif action == 'done':
                had_tasks = bool(h.store.list_tasks(category, 'all'))
                h.click(f'finish:{category}:')
                self.assertEqual(h.store.list_tasks(category, 'all'), [])
                if had_tasks:
                    h.click('undo:')
                    self.assertEqual(h.store.get('screen'), 'list')
                h.click('menu')
                h.click('cat:' + category)
            elif action == 'remove_category':
                h.click('category:remove:' + category)
                self.assertNotIn(category, h.store.categories())
                h.click('category:restore:' + category)
                self.assertIn(category, h.store.categories())
                h.click('menu')
                h.click('cat:' + category)
            else:
                h.click('menu')
                self.assertEqual(h.store.get('screen'), 'menu')
                h.click('cat:' + category)
            if action not in {'remove_category', 'done'}:
                self.assertEqual(h.store.revision(), before)
            self.assertEqual(h.store.get('view')['category'], category)
            self.assertFalse(h.store.get('remove_mode'))
            self.assertEqual(len(h.api.messages), 1)
            self.assertEqual({t['id']: (t['title'], t['category'], t['done'], t['deleted'])
                              for t in h.store.list_tasks(mode='all')}, original)

        for category in ('shopping', 'household', 'admin', 'home', 'study'):
            h.send('/menu')
            h.click('cat:' + category)
            for first in controls:
                for second in controls:
                    exercise(category, first)
                    exercise(category, second)
                    pairs += 1
            for action in ('add', 'remove', 'done', 'remove_category', 'add', 'main'):
                exercise(category, action)
        self.assertEqual(pairs, 125)
        self.assertFalse(any(m == 'sendDocument' for m, _, _ in h.api.calls))
        self.assertEqual(list(h.directory.glob('backups/*.zip')), [])

    def test_all_ordered_main_options_include_explicit_backup_destinations(self):
        h = self.h
        choices = ('category:add', 'cat:shopping', 'cat:household',
                   'cat:admin', 'cat:home', 'cat:study', 'showlist', 'view:all:trash:0', 'backup')
        expected_uploads = 0
        original_revision = h.store.revision()
        pairs = 0
        for first in choices:
            for second in choices:
                for action in (first, second):
                    h.send('/menu')
                    h.click(action)
                    expected_uploads += action == 'backup'
                    self.assertEqual(sum(m == 'sendDocument' for m, _, _ in h.api.calls), expected_uploads)
                    self.assertEqual(h.store.revision(), original_revision)
                    self.assertEqual(len([m for m in h.api.messages.values() if 'text' in m
                        and not m.get('reply_markup', {}).get('force_reply')]), 1)
                    if action == 'backup':
                        # Backup retains Main menu, so its navigation controls
                        # remain valid; they are not stale cross-screen actions.
                        self.assertEqual(h.store.get('screen'), 'menu')
                        self.assertEqual([b['callback_data'] for b in h.buttons()], list(choices))
                pairs += 1
        h.send('/menu')
        self.assertEqual(pairs, 81)
        self.assertEqual(expected_uploads, 18)
        self.assertEqual(len(list(h.directory.glob('backups/*.zip'))), 1)

    def test_duplicate_backup_callback_after_restart_does_not_resend_document(self):
        h = self.h
        update = h.click('backup')
        h.bot = Bot(h.store, h.api, 'testbot', h.directory)
        h.bot.handle(update)
        self.assertEqual(sum(m == 'sendDocument' for m, _, _ in h.api.calls), 1)
        h.click('backup')
        self.assertEqual(sum(m == 'sendDocument' for m, _, _ in h.api.calls), 2)

    def test_shared_panel_reply_cannot_bypass_unique_input_identity(self):
        h = self.h
        for kind in ('task', 'category'):
            h.send('/menu')
            if kind == 'task':
                h.add('home')
            else:
                h.click('category:add')
            before = h.store.revision()
            h.send('Ambiguous old input', reply=h.store.get('list_id'))
            self.assertEqual(h.store.revision(), before)
            self.assertIsNotNone(h.store.get('pending_101'))

    def test_task_prompt_is_targeted_and_cleaned_after_success(self):
        h = self.h
        prompt = h.add('home')
        message = h.api.messages[(h.GROUP, prompt)]
        self.assertTrue(message['reply_markup']['force_reply'])
        self.assertTrue(message['reply_markup']['selective'])
        self.assertIn('tg://user?id=101', message['text'])
        h.send('Wrong member', h.MEMBER, reply=prompt)
        self.assertEqual(len(h.store.list_tasks('home', 'all')), 1)
        h.send('Desk setup', reply=prompt)
        self.assertEqual(len(h.store.list_tasks('home', 'all')), 2)
        self.assertNotIn((h.GROUP, prompt), h.api.messages)

    def test_task_invalid_input_opens_new_personal_prompt_without_replacing_partner_screen(self):
        h = self.h
        prompt = h.add('home')
        h.send('/menu', h.MEMBER)
        h.click('cat:study', h.MEMBER)
        before = copy.deepcopy(h.panel())
        h.send('x' * 241, reply=prompt)
        replacement = h.store.get('pending_101')['prompt_id']
        self.assertNotEqual(replacement, prompt)
        self.assertNotIn((h.GROUP, prompt), h.api.messages)
        self.assertEqual(h.panel(), before)
        h.send('Valid owner task', reply=replacement)
        self.assertEqual(h.panel(), before)

    def test_polling_retries_database_lock_without_losing_or_duplicating_input(self):
        h = self.h
        prompt = h.add('home')
        update = {'update_id': 5000, 'message': {'message_id': 6000,
            'chat': {'id': h.GROUP, 'type': 'supergroup'}, 'from': {'id': h.OWNER},
            'text': 'Survives database lock', 'reply_to_message': {'message_id': prompt}}}
        add_name = 'add_tasks' if hasattr(h.store, 'add_tasks') else 'add_task'
        call, handle, add = h.api.call, h.bot.handle, getattr(h.store, add_name)
        attempts = []

        def locked_once(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError('database is locked')
            return add(*args, **kwargs)

        def finish(value):
            handle(value)
            h.bot.running = False

        def dispatch(method, payload=None, **kwargs):
            return [update] if method == 'getUpdates' else call(method, payload, **kwargs)

        with mock.patch.object(h.api, 'call', side_effect=dispatch), \
                mock.patch.object(h.bot, 'handle', side_effect=finish), \
                mock.patch.object(h.store, add_name, side_effect=locked_once), \
                mock.patch.object(h.bot, 'pause') as pause, mock.patch('home_bot.bot.log.warning'):
            h.bot.run()
        self.assertEqual(len(attempts), 2)
        pause.assert_called_once_with(2)
        self.assertEqual(h.store.get('last_update_id'), 5000)
        self.assertEqual(sum(t['title'] == 'Survives database lock'
                             for t in h.store.list_tasks('home', 'all')), 1)

    def test_nontransient_database_errors_are_not_swallowed(self):
        h = self.h
        with mock.patch.object(h.bot, 'maintenance', side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'disk I/O error'):
                h.bot.run()

    def test_slow_cleanup_is_bounded_and_does_not_block_saved_input_or_menu(self):
        h = self.h
        prompt = h.add('home')
        release = threading.Event()
        call = h.api.call
        h.bot.in_polling_loop = True

        def stall_deletion(method, payload=None, **kwargs):
            if method == 'deleteMessage':
                release.wait(3)
            return call(method, payload, **kwargs)

        try:
            with mock.patch.object(h.api, 'call', side_effect=stall_deletion):
                started = time.perf_counter()
                h.send('Saved before slow cleanup', reply=prompt)
                self.assertLess(time.perf_counter() - started, 0.5)
                self.assertIn('Added 1 task', h.panel()['text'])
                self.assertEqual(len(h.bot.cleanup_jobs), 2)
                self.assertEqual(len(h.store.get('prompt_deletions')), 2)
                h.bot.queue_message_cleanup(h.GROUP, 9999)
                started = time.perf_counter()
                h.send('/menu')
                self.assertLess(time.perf_counter() - started, 0.5)
                self.assertEqual(h.store.get('screen'), 'menu')
                self.assertEqual(len(h.bot.cleanup_jobs), 2)
                # The replaced menu joins the two inputs and manual cleanup.
                self.assertEqual(len(h.store.get('prompt_deletions')), 4)
                release.set()
                for job in list(h.bot.cleanup_jobs.values()):
                    job.result(timeout=2)
                h.bot.cleanup_prompts()
                for job in list(h.bot.cleanup_jobs.values()):
                    job.result(timeout=2)
                h.bot.collect_cleanup()
                self.assertEqual(h.store.get('prompt_deletions'), [])
                self.assertEqual(len(h.api.messages), 1)
        finally:
            release.set()
            if h.bot.cleanup_executor:
                h.bot.cleanup_executor.shutdown(wait=True)
            if h.bot.answer_executor:
                h.bot.answer_executor.shutdown(wait=True)
            h.bot.in_polling_loop = False

    def test_async_cleanup_retry_state_survives_restart_and_honors_rate_limit(self):
        h = self.h
        prompt = h.add('home')
        h.bot.in_polling_loop = True
        h.api.fail_once['deleteMessage'] = TelegramError(429, 'Rate limited', 120)
        try:
            h.send('Saved once despite cleanup rate limit', reply=prompt)
            for job in list(h.bot.cleanup_jobs.values()):
                job.result(timeout=2)
            h.bot.collect_cleanup()
            queued = h.store.get('prompt_deletions')
            self.assertEqual(len(queued), 1)
            self.assertGreater(queued[0]['retry_at'] - time.time(), 119)
            h.bot.cleanup_executor.shutdown(wait=True)
            if h.bot.answer_executor:
                h.bot.answer_executor.shutdown(wait=True)
            h.bot = Bot(h.store, h.api, 'testbot', h.directory)
            calls = len(h.api.calls)
            h.bot.cleanup_prompts()
            self.assertEqual(len(h.api.calls), calls)
            with mock.patch('home_bot.bot.time.time', return_value=queued[0]['retry_at'] + 1):
                h.bot.cleanup_prompts()
            self.assertEqual(h.store.get('prompt_deletions'), [])
            self.assertEqual(sum(t['title'] == 'Saved once despite cleanup rate limit'
                                 for t in h.store.list_tasks('home', 'all')), 1)
        finally:
            if h.bot.cleanup_executor:
                h.bot.cleanup_executor.shutdown(wait=True)
            if h.bot.answer_executor:
                h.bot.answer_executor.shutdown(wait=True)
            h.bot.in_polling_loop = False

    def test_delete_rate_limit_also_delays_new_cleanup_after_restart(self):
        h = self.h
        h.bot.queue_message_cleanup(h.GROUP, 9000)
        h.api.fail_once['deleteMessage'] = TelegramError(429, 'Slow down', 120)
        h.bot.cleanup_prompts()
        cooldown = h.store.get('cleanup_retry_at')
        h.bot = Bot(h.store, h.api, 'testbot', h.directory)
        h.bot.queue_message_cleanup(h.GROUP, 9001)
        before = sum(method == 'deleteMessage' for method, _, _ in h.api.calls)
        h.bot.cleanup_prompts()
        self.assertEqual(sum(method == 'deleteMessage' for method, _, _ in h.api.calls), before)
        h.send('/menu')
        self.assertEqual(h.store.get('screen'), 'menu')
        with mock.patch('home_bot.bot.time.time', return_value=cooldown + 1):
            h.bot.cleanup_prompts()
        self.assertEqual(h.store.get('prompt_deletions'), [])

    def test_late_category_creation_refreshes_current_main_menu_without_navigation(self):
        h = self.h
        h.click('category:add', h.MEMBER)
        prompt = h.store.get(f'pending_{h.MEMBER}')['prompt_id']
        h.send('/menu', h.OWNER)
        panel_id = h.store.get('list_id')
        h.send('Weekend plans', h.MEMBER, reply=prompt)
        category = h.store.get('custom_categories')[-1]['key']
        self.assertEqual(h.store.get('screen'), 'menu')
        self.assertEqual(h.store.get('list_id'), panel_id)
        self.assertIn('cat:' + category, [b['callback_data'] for b in h.buttons()])
        self.assertEqual(len(h.api.messages), 1)

    def test_late_task_addition_refreshes_counts_on_current_main_menu(self):
        h = self.h
        prompt = h.add('home', h.MEMBER)
        h.send('/menu', h.OWNER)
        panel_id = h.store.get('list_id')
        h.send('New desk lamp', h.MEMBER, reply=prompt)
        self.assertEqual(h.store.get('screen'), 'menu')
        self.assertEqual(h.store.get('list_id'), panel_id)
        self.assertIn('4 open', h.panel()['text'])
        home = next(b for b in h.buttons() if b['callback_data'] == 'cat:home')
        self.assertTrue(home['text'].endswith('2'))

class TransportAuditTests(unittest.TestCase):
    def test_malformed_success_envelope_is_retryable_not_process_crash(self):
        for value in ([], None, 'unexpected', {'ok': True}, {'ok': False, 'parameters': None},
                      {'ok': False, 'error_code': 'invalid'}):
            with self.subTest(value=value), mock.patch('home_bot.bot.urllib.request.urlopen',
                    return_value=io.BytesIO(json.dumps(value).encode())):
                with self.assertRaises((NetworkError, TelegramError)):
                    Telegram('000000:synthetic-token').call('getMe')

    def test_startup_honors_long_retry_after(self):
        api = mock.Mock()
        api.call.side_effect = [TelegramError(429, 'Rate limited', 120), {'id': 1}]
        clock = [0.0]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch('home_bot.bot.time.monotonic', side_effect=lambda: clock[0]), \
                mock.patch('home_bot.bot.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                mock.patch('home_bot.bot.log.warning'):
            startup_call(api, 'getMe', Path(directory))
        self.assertEqual(clock[0], 120.0)


if __name__ == '__main__':
    unittest.main()
