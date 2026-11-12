"""Simulator fidelity and malformed-request regression tests, using dummy data."""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from home_bot.bot import LOCAL_TEST_TOKEN, Telegram, TelegramError, NetworkError
from home_bot.simulator import GROUP, OWNER, BotWorker, SimulatorServer, TelegramState
from home_bot.storage import Store


def write_fault_matrix(path, cases, expected):
    observed = {(case['method'], case['failure']) for case in cases}
    passed = (len(cases) == len(expected) and observed == expected
              and all(case.get('passed') is True for case in cases))
    result = {'cases': cases, 'case_count': len(cases), 'expected_case_count': len(expected),
              'passed': passed, 'missing_cases': sorted(expected - observed),
              'scope': 'Actual HTTP transport and simulator application semantics; bot-level recovery is tested separately.'}
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


class SimulatorAuditTests(unittest.TestCase):
    def setUp(self):
        self.state = TelegramState()
        self.server = SimulatorServer(self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = Telegram(LOCAL_TEST_TOKEN, api_base=self.server.url)

    def tearDown(self):
        self.state.closing = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request_length(self, value):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=1)
        try:
            connection.putrequest('POST', '/sim/message')
            connection.putheader('Content-Length', value)
            connection.endheaders()
            return connection.getresponse().status
        finally:
            connection.close()

    def test_malformed_content_length_is_rejected(self):
        self.assertEqual(self.request_length('not-an-integer'), 400)

    def test_stopping_during_initialization_never_starts_a_late_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = BotWorker(Path(directory), self.server)
            entered, release, stop_waiting = threading.Event(), threading.Event(), threading.Event()
            errors = []
            original_join = worker.thread.join

            def delayed_store(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Test did not release initialization')
                return Store(*args, **kwargs)

            def observed_join(*args, **kwargs):
                stop_waiting.set()
                return original_join(*args, **kwargs)

            def stop():
                try:
                    worker.stop()
                except Exception as exc:
                    errors.append(str(exc))

            with mock.patch('home_bot.simulator.Store', side_effect=delayed_store), \
                    mock.patch('home_bot.simulator.Bot.run') as run, \
                    mock.patch.object(worker.thread, 'join', side_effect=observed_join):
                worker.thread.start()
                stopper = threading.Thread(target=stop)
                try:
                    self.assertTrue(entered.wait(2))
                    stopper.start()
                    self.assertTrue(stop_waiting.wait(2))
                finally:
                    release.set()
                    if stopper.ident is not None:
                        stopper.join(5)
                    original_join(5)
                self.assertEqual(errors, [])
                self.assertFalse(worker.thread.is_alive())
                self.assertFalse(stopper.is_alive())
                run.assert_not_called()

    def test_negative_content_length_is_rejected_without_waiting_for_body(self):
        self.assertEqual(self.request_length('-1'), 400)

    def test_oversized_content_length_is_rejected_without_reading_body(self):
        self.assertEqual(self.request_length(str(21 * 1024 * 1024)), 413)

    def test_markup_edits_validate_callback_size(self):
        message = self.api.send(GROUP, 'panel')
        with self.assertRaises(TelegramError) as caught:
            self.api.call('editMessageReplyMarkup', {'chat_id': GROUP, 'message_id': message['message_id'],
                'reply_markup': {'inline_keyboard': [[{'text': 'Bad', 'callback_data': 'a' * 65}]]}})
        self.assertEqual(caught.exception.code, 400)

    def test_bot_cannot_edit_a_user_message(self):
        message = self.state.new_message(GROUP, OWNER, text='User text')
        for method, content in [('editMessageText', {'text': 'Replaced'}),
                                ('editMessageReplyMarkup', {'reply_markup': {'inline_keyboard': []}})]:
            with self.subTest(method=method), self.assertRaises(TelegramError) as caught:
                self.api.call(method, {'chat_id': GROUP, 'message_id': message['message_id'], **content})
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.state.messages[(GROUP, message['message_id'])]['text'], 'User text')

    def test_document_caption_limit_uses_utf16(self):
        with tempfile.TemporaryDirectory() as folder:
            document = Path(folder) / 'synthetic.zip'
            document.write_bytes(b'local-test-only')
            with self.assertRaises(TelegramError) as caught:
                self.api.call('sendDocument', {'chat_id': GROUP, 'caption': '\U0001f600' * 513}, file=document)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(self.state.files, {})

    def test_api_attempt_log_includes_rejections_and_lost_responses(self):
        self.state.fail_next('getMe', '500')
        with self.assertRaises(TelegramError):
            self.api.call('getMe')
        self.state.fail_next('getMe', 'disconnect_before')
        with self.assertRaises(NetworkError):
            self.api.call('getMe')
        self.state.fail_next('getMe', 'disconnect_after')
        with self.assertRaises(NetworkError):
            self.api.call('getMe')
        self.api.call('getMe')
        self.assertEqual([r['method'] for r in self.state.requests], ['getMe'] * 4)
        self.assertEqual([r['failure'] for r in self.state.requests],
                         ['500', 'disconnect_before', 'disconnect_after', None])
        self.assertEqual(len(self.state.calls), 2)
        self.assertEqual(self.state.snapshot()['api_attempts'], 4)

    def test_fault_modes_cover_every_production_api_operation(self):
        """Transport coverage supplements bot recovery tests; no success assumed."""
        cases = []
        with tempfile.TemporaryDirectory() as folder:
            document = Path(folder) / 'synthetic.zip'
            document.write_bytes(b'local-fault-fixture-only')
            methods = ('getMe', 'getWebhookInfo', 'getUpdates', 'setMyCommands',
                       'setChatMenuButton', 'answerCallbackQuery', 'sendMessage',
                       'editMessageText', 'editMessageReplyMarkup', 'deleteMessage', 'sendDocument')
            for method in methods:
                for mode in ('500', '429', 'disconnect_before', 'disconnect_after'):
                    with self.subTest(method=method, mode=mode):
                        message = self.api.send(GROUP, 'Original')
                        mid = message['message_id']
                        payloads = {
                            'getMe': {}, 'getWebhookInfo': {}, 'getUpdates': {'offset': 0, 'timeout': 0},
                            'setMyCommands': {'commands': [{'command': 'menu', 'description': 'Menu'}]},
                            'setChatMenuButton': {'menu_button': {'type': 'commands'}},
                            'answerCallbackQuery': {'callback_query_id': f'{method}-{mode}'},
                            'sendMessage': {'chat_id': GROUP, 'text': 'New panel'},
                            'editMessageText': {'chat_id': GROUP, 'message_id': mid, 'text': 'Edited'},
                            'editMessageReplyMarkup': {'chat_id': GROUP, 'message_id': mid,
                                'reply_markup': {'inline_keyboard': [[{'text': 'Main menu', 'callback_data': 'menu'}]]}},
                            'deleteMessage': {'chat_id': GROUP, 'message_id': mid},
                            'sendDocument': {'chat_id': GROUP, 'caption': 'Intentional synthetic backup'},
                        }
                        before_calls, before_attempts = len(self.state.calls), len(self.state.requests)
                        before_messages, before_files = len(self.state.messages), len(self.state.files)
                        self.state.fail_next(method, mode)
                        error_type = TelegramError if mode in ('500', '429') else NetworkError
                        with self.assertRaises(error_type):
                            self.api.call(method, payloads[method],
                                          **({'file': document} if method == 'sendDocument' else {}))
                        applied = mode == 'disconnect_after'
                        self.assertEqual(len(self.state.requests) - before_attempts, 1)
                        self.assertEqual(len(self.state.calls) - before_calls, int(applied))
                        if method in ('sendMessage', 'sendDocument'):
                            self.assertEqual(len(self.state.messages) - before_messages, int(applied))
                        if method == 'sendDocument':
                            self.assertEqual(len(self.state.files) - before_files, int(applied))
                        if method == 'deleteMessage':
                            self.assertEqual((GROUP, mid) not in self.state.messages, applied)
                        if method == 'editMessageText':
                            self.assertEqual(self.state.messages[(GROUP, mid)]['text'], 'Edited' if applied else 'Original')
                        if method == 'editMessageReplyMarkup':
                            self.assertEqual(bool(self.state.messages[(GROUP, mid)].get('reply_markup')), applied)
                        cases.append({'method': method, 'failure': mode,
                                      'accepted_before_response_lost': applied, 'passed': True})
        report = Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',__import__('tempfile').gettempdir())) / 'home-api-fault-matrix.json'
        report.parent.mkdir(parents=True, exist_ok=True)
        expected = {(method, failure) for method in methods
                    for failure in ('500', '429', 'disconnect_before', 'disconnect_after')}
        write_fault_matrix(report, cases, expected)


class FaultEvidenceTests(unittest.TestCase):
    def test_failed_subtest_cannot_publish_a_passing_matrix(self):
        cases = []
        class DeliberatelyFailedProbe(unittest.TestCase):
            def runTest(self):
                for mode in ('500', '429'):
                    with self.subTest(mode=mode):
                        self.assertNotEqual(mode, '429', 'synthetic failure proves report handling')
                        cases.append({'method': 'getMe', 'failure': mode, 'passed': True})
        result = unittest.TextTestRunner(stream=io.StringIO()).run(DeliberatelyFailedProbe())
        self.assertFalse(result.wasSuccessful())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'faults.json'
            expected = {('getMe', '500'), ('getMe', '429')}
            write_fault_matrix(path, cases, expected)
            self.assertFalse(json.loads(path.read_text('utf-8'))['passed'])

    def test_duplicate_or_failed_case_cannot_replace_required_evidence(self):
        expected = {('getMe', '500'), ('getMe', '429')}
        a = {'method': 'getMe', 'failure': '500', 'passed': True}
        b = {'method': 'getMe', 'failure': '429', 'passed': True}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'faults.json'
            for cases in ([a, a], [a, {**b, 'passed': False}], [a], [a, b, b]):
                with self.subTest(cases=cases):
                    self.assertFalse(write_fault_matrix(path, cases, expected)['passed'])
            self.assertTrue(write_fault_matrix(path, [a, b], expected)['passed'])


if __name__ == '__main__':
    unittest.main()
