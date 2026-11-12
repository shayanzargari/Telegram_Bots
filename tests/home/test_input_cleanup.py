"""Only successfully saved task/category replies are removed from Telegram."""
import copy
import time
import unittest
from unittest import mock

import test_bot
from home_bot.bot import Bot, NetworkError, TelegramError
from home_bot.simulator import GROUP, OWNER, PARTNER, STRANGER
from test_local_telegram import HTTPScenario


class InputCleanupHTTPTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def exists(self, update):
        return (GROUP, update['message']['message_id']) in self.s.state.messages

    def test_tasks_and_categories_remove_accepted_replies_only(self):
        s = self.s
        command = s.send('/menu')
        original_panel = s.panel()['message_id']
        s.click('cat:shopping')
        s.click('add:shopping')
        accepted = s.send('Apples\nPears', reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.assertFalse(self.exists(accepted))
        self.assertTrue(self.exists(command))
        self.assertEqual([t['title'] for t in s.tasks()][-2:], ['Apples', 'Pears'])
        s.click('menu', PARTNER)
        s.click('category:add', PARTNER)
        prompt_id = s.meta(f'pending_{PARTNER}')['prompt_id']
        category = s.send('Garden projects', PARTNER, reply=prompt_id)
        self.assertFalse(self.exists(category))
        self.assertNotIn((GROUP, prompt_id), s.state.messages)
        self.assertIn('Garden projects', str(s.meta('custom_categories')))
        s.processed(s.state.replay(accepted))
        s.processed(s.state.replay(category))
        self.assertEqual(sum(t['title'] == 'Apples' for t in s.tasks()), 1)
        self.assertEqual(len(s.meta('custom_categories')), 1)
        self.assertEqual(s.panel()['message_id'], original_panel)
        self.assertEqual(len(s.bot_messages()), 1)
        self.assertEqual(s.state.files, {})
        self.assertEqual(list(s.worker.folder.glob('backups/*.zip')), [])

    def test_invalid_cancelled_and_other_users_replies_stay(self):
        s = self.s
        s.click('cat:shopping')
        s.click('add:shopping')
        prompt = s.meta(f'pending_{OWNER}')['prompt_id']
        invalid = s.send('x' * 241, reply=prompt)
        prompt = s.meta(f'pending_{OWNER}')['prompt_id']
        stranger = s.send('Do not save', STRANGER, reply=prompt)
        partner = s.send('Partner conversation', PARTNER, reply=prompt)
        s.click('menu')
        cancelled = s.send('Late cancelled reply', reply=prompt)
        for update in (invalid, stranger, partner):
            self.assertTrue(self.exists(update))
        self.assertTrue(any(m.get('text') == 'Late cancelled reply' for m in s.state.messages.values()))
        s.click('category:add')
        duplicate = s.send('Groceries', reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.assertTrue(self.exists(duplicate))
        self.assertIsNotNone(s.meta(f'pending_{OWNER}'))

    def test_partner_screen_stays_when_saved_input_is_deleted(self):
        s = self.s
        s.click('cat:shopping')
        s.click('add:shopping')
        prompt = s.meta(f'pending_{OWNER}')['prompt_id']
        s.click('menu', PARTNER)
        s.click('cat:study', PARTNER)
        before = s.panel()
        accepted = s.send('Bananas', reply=prompt)
        self.assertFalse(self.exists(accepted))
        self.assertEqual(s.panel(), before)
        self.assertEqual(s.tasks()[-1]['title'], 'Bananas')

    def test_missing_group_rights_does_not_break_save_or_prompt_cleanup(self):
        s = self.s
        s.state.delete_user_messages_allowed = False
        s.click('category:add')
        prompt = s.meta(f'pending_{OWNER}')['prompt_id']
        accepted = s.send('Weekend plans', reply=prompt)
        self.assertTrue(self.exists(accepted))
        self.assertIn('Weekend plans', str(s.meta('custom_categories')))
        self.assertIn('Added category', s.panel()['text'])
        self.assertNotIn((GROUP, prompt), s.state.messages)
        self.assertIsNone(s.meta(f'pending_{OWNER}'))
        s.wait(lambda: all('retry_at' in entry for entry in s.meta('prompt_deletions') or []))
        queued = s.meta('prompt_deletions')
        self.assertEqual([e['message_id'] for e in queued], [accepted['message']['message_id']])
        self.assertGreater(queued[0]['retry_at'], time.time())
        s.click('menu')
        self.assertEqual(s.meta('screen'), 'menu')


class InputCleanupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.h = test_bot.BotTests()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_all_tasks_commit_before_input_deletion(self):
        h = self.h
        prompt = h.add()
        call = h.api.call

        def inspect(method, payload=None, **kwargs):
            if method == 'deleteMessage':
                self.assertEqual([t['title'] for t in h.store.list_tasks('shopping', 'all')],
                                 ['Milk', 'Apples', 'Pears'])
            return call(method, payload, **kwargs)

        with mock.patch.object(h.api, 'call', side_effect=inspect):
            update = h.send('Apples\nPears', reply=prompt)
        self.assertIn({'chat_id': h.GROUP, 'message_id': update['message']['message_id']},
                      [p for method, p, _ in h.api.calls if method == 'deleteMessage'])

    def test_failed_save_keeps_input(self):
        h = self.h
        prompt = h.add()
        deleted_before = [p for m, p, _ in h.api.calls if m == 'deleteMessage']
        with mock.patch.object(h.store, 'add_tasks', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                h.send('Apples', reply=prompt)
        self.assertEqual([p for m, p, _ in h.api.calls if m == 'deleteMessage'], deleted_before)
        self.assertFalse(h.store.get('prompt_deletions', []))

    def test_panel_failure_keeps_cleanup_durable_across_restart(self):
        h = self.h
        prompt = h.add()
        h.api.fail_once['editMessageText'] = NetworkError('offline')
        with self.assertRaises(NetworkError):
            h.send('Apples', reply=prompt)
        self.assertEqual(len(h.store.get('prompt_deletions')), 1)
        self.assertEqual(sum(t['title'] == 'Apples' for t in h.store.list_tasks('shopping', 'all')), 1)
        h.bot = Bot(h.store, h.api, 'testbot', h.directory)
        h.bot.cleanup_prompts()
        self.assertEqual(h.store.get('prompt_deletions'), [])

    def test_lost_delete_response_is_retried_without_readding(self):
        h = self.h
        prompt = h.add()
        h.api.fail_once['deleteMessage'] = NetworkError('response lost')
        update = h.send('Apples', reply=prompt)
        queued = copy.deepcopy(h.store.get('prompt_deletions'))
        self.assertEqual(len(queued), 1)
        h.bot.handle(update)
        self.assertEqual(sum(t['title'] == 'Apples' for t in h.store.list_tasks('shopping', 'all')), 1)
        h.api.fail_once['deleteMessage'] = TelegramError(400, 'message to delete not found')
        h.bot = Bot(h.store, h.api, 'testbot', h.directory)
        with mock.patch('home_bot.bot.time.time', return_value=queued[0]['retry_at'] + 1):
            h.bot.cleanup_prompts()
        self.assertEqual(h.store.get('prompt_deletions'), [])

    def test_permissions_and_rate_limits_back_off_then_recover(self):
        h = self.h
        for failure in (TelegramError(400, 'not enough rights'), TelegramError(403, 'Forbidden'),
                        TelegramError(429, 'Too many requests', retry_after=90)):
            with self.subTest(code=failure.code):
                prompt = h.add()
                h.api.fail_once['deleteMessage'] = failure
                h.send('Apples', reply=prompt)
                queued = h.store.get('prompt_deletions')
                before = len(h.api.calls)
                h.bot.cleanup_prompts()
                self.assertEqual(len(h.api.calls), before)
                self.assertGreaterEqual(queued[0]['retry_at'] - time.time(),
                                        89 if failure.code == 429 else 59)
                with mock.patch('home_bot.bot.time.time', return_value=queued[0]['retry_at'] + 1):
                    h.bot.cleanup_prompts()
                self.assertEqual(h.store.get('prompt_deletions'), [])

    def test_expired_and_delayed_entries_do_not_block_new_cleanup(self):
        h = self.h
        now = time.time()
        h.store.set('prompt_deletions', [
            {'chat_id': h.GROUP, 'message_id': i, 'retry_at': now + 300, 'expires': now + 1000}
            for i in range(10, 15)])
        h.bot.queue_message_cleanup(h.GROUP, 20, sent_at=now - 48 * 3600)
        h.bot.queue_message_cleanup(h.GROUP, 21)
        h.bot.queue_message_cleanup(h.GROUP, 21)
        h.bot.cleanup_prompts()
        self.assertEqual(h.api.last('deleteMessage')['message_id'], 21)
        self.assertEqual(len(h.store.get('prompt_deletions')), 5)


if __name__ == '__main__':
    unittest.main()
