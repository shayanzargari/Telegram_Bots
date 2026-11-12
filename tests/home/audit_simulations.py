"""Reproducible synthetic HTTP campaigns; deliberately excluded from quick discovery.

Run: py -3 tests/audit_simulations.py --actions 10000 --rapid-actions 2000 --restarts 100
All data lives in TemporaryDirectory. No real token or household data is read.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import threading
import time
import traceback
import zipfile

import home_bot
ROOT = Path(home_bot.__file__).resolve().parent
from home_bot.simulator import GROUP, OWNER, PARTNER, STRANGER
from test_local_telegram import HTTPScenario

FIELDS = ('title', 'category', 'done', 'deleted', 'revision')
DEFAULTS = {'shopping': 'Groceries', 'household': 'Home shopping', 'admin': 'Admin & personal',
            'home': 'Home tasks', 'study': 'Languages & study'}
SEEDS = (2026110901, 2026110902, 2026110903, 2026110904)


def task_map(tasks):
    return {task['id']: {key: task[key] for key in FIELDS} for task in tasks}


class Campaign:
    def __init__(self, seed, report):
        self.seed, self.report = seed, report
        self.rng = random.Random(seed)
        self.s = HTTPScenario()
        fixture = json.loads((ROOT / 'seed_tasks.json').read_text('utf-8'))
        self.model = {index: {'title': task['title'], 'category': task['category'],
                             'done': 0, 'deleted': 0, 'revision': 1}
                      for index, task in enumerate(fixture, 1)}
        assert task_map(self.s.tasks()) == self.model, ('initial fixture', seed)
        self.categories = dict(DEFAULTS)
        self.removed = {}
        self.receipts = {}
        self.batch_receipts = set()
        self.unbound_batch_receipt = None
        self.selection = None
        self.pending = {}
        self.history = []
        self.cached = []
        self.operations = 0
        self.backups = set()
        self.documents = 0
        self.expected_new_category = None
        self.actions = collections.Counter()
        self.checks = 0

    def check(self, context):
        actual = task_map(self.s.tasks())
        assert actual == self.model, (self.seed, self.operations, context, 'tasks', actual, self.model)
        catalog = dict(DEFAULTS)
        catalog.update({c['key']: c['label'] for c in self.s.meta('custom_categories') or []})
        assert catalog == self.categories, (self.seed, self.operations, context, 'category catalog')
        archived = {entry['key']: entry for entry in self.s.meta('removed_categories') or []}
        assert set(archived) == set(self.removed), (self.seed, self.operations, context, 'archived membership')
        for key, expected in self.removed.items():
            assert archived[key]['task_ids'] == expected['task_ids'], (context, 'archived IDs', key)
            if expected.get('token'):
                assert archived[key]['token'] == expected['token'], (context, 'archive token', key)
            else:
                expected['token'] = archived[key]['token']  # Opaque generated identifier only.
        actual_selection = self.s.meta('remove_selection')
        assert bool(actual_selection) == bool(self.selection), (context, 'selection session')
        if self.selection:
            if self.selection['nonce'] is None:
                self.selection['nonce'] = actual_selection['nonce']  # Opaque generated identifier only.
            for key in ('owner', 'category', 'nonce', 'selected', 'items', 'version', 'action'):
                assert actual_selection[key] == self.selection[key], (context, 'selection', key,
                                                                      actual_selection[key], self.selection[key])
            assert bool(actual_selection.get('committed')) == self.selection.get('committed', False)
        expected_prompts = set()
        for uid in (OWNER, PARTNER):
            actual_pending = self.s.meta(f'pending_{uid}')
            expected = self.pending.get(uid)
            assert bool(actual_pending) == bool(expected), (self.seed, self.operations, context, 'pending', uid)
            if expected:
                assert actual_pending.get('kind', 'task') == expected['kind'], (context, 'input kind', uid)
                assert actual_pending.get('category') == expected.get('category'), (context, 'input category', uid)
                if actual_pending.get('prompt_id'):
                    expected_prompts.add(actual_pending['prompt_id'])
        def prompt_ids():
            with self.s.state.condition:
                return {m['message_id'] for m in self.s.state.messages.values()
                        if m['chat']['id'] == GROUP and m['from']['is_bot'] and
                        m.get('reply_markup', {}).get('force_reply')}
        # Database invariants were checked immediately above. Deleting a known
        # obsolete prompt may finish asynchronously after the result is visible.
        # This campaign grants deletion rights, so require eventual cleanup;
        # separate tests cover denied permission and transient delete failures.
        self.s.wait(lambda: prompt_ids() == expected_prompts, timeout=10)
        with self.s.state.condition:
            messages = [m for m in self.s.state.messages.values() if m['chat']['id'] == GROUP and m['from']['is_bot']]
        permanent = [m for m in messages if not m.get('document') and not m.get('reply_markup', {}).get('force_reply')]
        assert len(permanent) == 1, (self.seed, self.operations, context, 'permanent panels', len(permanent))
        assert len([m for m in messages if m.get('reply_markup', {}).get('force_reply')]) <= 2
        assert len(self.s.state.files) == self.documents, (context, 'unrequested document')
        assert {p.name for p in self.s.worker.folder.glob('backups/*.zip')} == self.backups, (context, 'unrequested ZIP')
        assert self.s.meta('screen') == self.s.meta('panel_state_list_id')['screen'], (context, 'screen mismatch')
        assert [command['command'] for command in self.s.state.commands] == ['menu'], (context, 'registered commands')
        if self.s.meta('screen') == 'menu':
            callbacks = [b['callback_data'] for b in self.s.buttons()]
            expected_categories = ['cat:' + key for key in self.categories if key not in self.removed]
            assert [value for value in callbacks if value.startswith('cat:')] == expected_categories, (context, 'menu categories')
            assert callbacks[0] == 'category:add' and callbacks[-1] == 'backup'
            assert 'add:choose' not in callbacks
            assert 'help' not in callbacks and not any(v.startswith(('remove:', 'finish:')) for v in callbacks)
            for control in self.s.buttons():
                if control['callback_data'].startswith('cat:'):
                    category = control['callback_data'].split(':')[1]
                    count = sum(t['category'] == category and not t['deleted'] and not t['done']
                                for t in self.model.values())
                    assert control['text'].endswith(f' · {count}'), (context, 'category count', category)
            opened = sum(not t['deleted'] and not t['done'] for t in self.model.values())
            done = sum(not t['deleted'] and t['done'] for t in self.model.values())
            assert f'{opened} open · {done} done' in self.s.panel()['text'], (context, 'menu totals')
        self.checks += 1

    def record(self, action, uid, detail):
        self.operations += 1
        self.actions[action] += 1
        self.history.append({'operation': self.operations, 'action': action, 'uid': uid, 'detail': detail})
        self.history = self.history[-40:]

    def apply_callback(self, value, uid, current, query_id):
        if uid not in (OWNER, PARTNER) or (value != 'menu' and value not in current):
            return
        parts = value.split(':')
        if value in {'menu', 'help', 'members', 'invite', 'showlist', 'add:choose'} or parts[0] in {'cat', 'view', 'remove'}:
            self.pending.pop(uid, None)
            self.selection = None
        if value == 'category:add':
            self.pending[uid] = {'kind': 'category'}
        elif parts[0] == 'add' and value != 'add:choose':
            self.pending[uid] = {'kind': 'task', 'category': parts[1]}
        elif parts[0] == 'cancel':
            if parts[1] == str(uid):
                self.pending.pop(uid, None)
                self.selection = None
        elif parts[0] == 'remove':
            self.selection = {'owner': uid, 'category': parts[1], 'nonce': None,
                              'selected': {}, 'items': {}, 'version': 0, 'action': 'delete'}
        elif parts[0] == 'view' and parts[2] == 'trash':
            self.selection = {'owner': uid, 'category': parts[1], 'nonce': None,
                              'selected': {}, 'items': {}, 'version': 0, 'action': 'restore'}
        elif parts[0] in ('pick', 'rpick'):
            if not self.selection or self.selection['owner'] != uid or self.selection.get('committed'):
                return
            _, raw_id, raw_revision, nonce, desired, raw_generation = parts
            task = self.model.get(int(raw_id))
            revision, generation = int(raw_revision), int(raw_generation)
            restoring = self.selection['action'] == 'restore'
            if (nonce != self.selection['nonce'] or generation != self.selection['items'].get(raw_id, 0)
                    or not task or bool(task['deleted']) != restoring or task['category'] in self.removed
                    or self.selection['category'] not in ('all', task['category'])
                    or (parts[0] == 'rpick') != restoring
                    or task['revision'] != revision):
                return
            if desired == '1':
                self.selection['selected'][raw_id] = revision
            else:
                self.selection['selected'].pop(raw_id, None)
            self.selection['items'][raw_id] = generation + 1
            self.selection['version'] += 1
        elif parts[0] == 'batch':
            if (not self.selection or self.selection['owner'] != uid or self.selection.get('committed')
                    or ('restore' if self.selection['action'] == 'restore' else 'remove') != parts[1]
                    or self.selection['nonce'] != parts[2] or self.selection['version'] != int(parts[3])):
                return
            selected = self.selection['selected']
            restoring = self.selection['action'] == 'restore'
            stale = [key for key, revision in selected.items() if int(key) not in self.model
                     or self.selection['category'] not in ('all', self.model[int(key)]['category'])
                     or self.model[int(key)]['category'] in self.removed
                     or bool(self.model[int(key)]['deleted']) != restoring
                     or self.model[int(key)]['revision'] != revision]
            if stale:
                for key in stale:
                    selected.pop(key)
                    self.selection['items'][key] = self.selection['items'].get(key, 0) + 1
                self.selection['version'] += 1
                return
            if not selected:
                return
            previous_tasks = []
            for key, revision in selected.items():
                tid = int(key)
                task = self.model[tid]
                previous = (task['done'], task['deleted'])
                task['deleted'] = int(not restoring)
                task['revision'] += 1
                previous_tasks.append((tid, previous, task['revision']))
            # The receipt identifier is opaque. Bind it to the resulting visible
            # Undo control after processing; task expectations remain independent.
            self.unbound_batch_receipt = previous_tasks
            self.selection['committed'] = True
        elif parts[0] == 'finish':
            category = parts[1]
            expected = {int(v.split(':')[2]): int(v.split(':')[3]) for v in current
                        if v.startswith(('task:complete:', 'task:reopen:'))}
            actual = {tid: t['revision'] for tid, t in self.model.items()
                      if t['category'] == category and not t['deleted']}
            if expected != actual:
                return
            self.pending.pop(uid, None)
            self.selection = None
            previous_tasks = []
            for tid in actual:
                task = self.model[tid]
                previous = (task['done'], task['deleted'])
                task['deleted'] = 1
                task['revision'] += 1
                previous_tasks.append((tid, previous, task['revision']))
            if previous_tasks:
                self.unbound_batch_receipt = previous_tasks
        elif parts[:2] == ['category', 'remove']:
            self.selection = None
            category = parts[2]
            self.pending.pop(uid, None)
            for user, pending in list(self.pending.items()):
                if pending.get('category') == category:
                    self.pending.pop(user)
            if category not in self.removed:
                ids = [tid for tid, t in self.model.items() if t['category'] == category and not t['deleted']]
                for tid in ids:
                    self.model[tid]['deleted'] = 1
                    self.model[tid]['revision'] += 1
                self.removed[category] = {'task_ids': ids}
        elif parts[:2] == ['category', 'restore']:
            self.selection = None
            self.pending.pop(uid, None)
            entry = self.removed.get(parts[2])
            if entry and entry['token'] == parts[3]:
                for tid in entry['task_ids']:
                    self.model[tid]['deleted'] = 0
                    self.model[tid]['revision'] += 1
                del self.removed[parts[2]]
        elif parts[0] == 'task':
            action, tid, revision = parts[1], int(parts[2]), int(parts[3])
            task = self.model.get(tid)
            if not task or task['category'] in self.removed or task['revision'] != revision:
                return
            valid = {'complete': not task['deleted'] and not task['done'],
                     'reopen': not task['deleted'] and task['done'],
                     'delete': not task['deleted'], 'restore': task['deleted']}[action]
            if not valid:
                return
            previous = (task['done'], task['deleted'])
            if action in ('complete', 'reopen'):
                task['done'] = int(action == 'complete')
            else:
                task['deleted'] = int(action == 'delete')
            task['revision'] += 1
            receipt = hashlib.sha256(query_id.encode()).hexdigest()[:16]
            self.receipts[receipt] = [(tid, previous, task['revision'])]
        elif parts[0] == 'undo':
            receipt = self.receipts.get(parts[1])
            if receipt:
                if all(self.model[tid]['revision'] == revision and self.model[tid]['category'] not in self.removed
                       for tid, previous, revision in receipt):
                    for tid, previous, revision in receipt:
                        self.model[tid]['done'], self.model[tid]['deleted'] = previous
                        self.model[tid]['revision'] += 1
                if parts[1] in self.batch_receipts:
                    self.selection = None

    def click(self, value, uid=OWNER, source=None):
        current_panel = self.s.panel()
        current = {b['callback_data'] for row in current_panel.get('reply_markup', {}).get('inline_keyboard', []) for b in row}
        source = source or current_panel
        # The shared HTTP scenario also models a callback queued while its old
        # message existed, then delivered after that panel was replaced/deleted.
        update = self.s.click(value, uid, exact=True, message=source)
        self.apply_callback(value, uid, current, update['callback_query']['id'])
        if self.unbound_batch_receipt is not None:
            undo = [button['callback_data'] for button in self.s.buttons()
                    if button['callback_data'].startswith('undo:')]
            assert len(undo) == 1, ('batch undo control', undo)
            receipt = undo[0].split(':', 1)[1]
            self.receipts[receipt] = self.unbound_batch_receipt
            self.batch_receipts.add(receipt)
            self.unbound_batch_receipt = None
        self.record(value.split(':')[0], uid, value)
        if value == 'backup' and value in current and uid in (OWNER, PARTNER):
            before = self.documents
            self.s.wait_backups()
            assert len(self.s.state.files) == before + 1, ('manual backup count', before)
            self.documents += 1
            self.backups = {p.name for p in self.s.worker.folder.glob('backups/*.zip')}
            _, content = list(self.s.state.files.values())[-1]
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                saved = json.loads(archive.read('tasks.json'))
                assert task_map(saved['tasks']) == self.model, ('backup task snapshot', self.seed, self.operations)
                assert archive.testzip() is None
                assert 'bot_token' not in archive.read('tasks.json').decode()
        self.cached.append((source, value, update))
        self.cached = self.cached[-80:]
        self.check(value)
        return update

    def send(self, text, uid=OWNER, reply=None, *, valid_input=False):
        expected = self.pending.get(uid)
        if text.startswith('/'):
            if uid in (OWNER, PARTNER):
                self.pending.pop(uid, None)
                self.selection = None
        elif expected and valid_input:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if expected['kind'] == 'category':
                self.expected_new_category = ' '.join(lines[0].split())
            else:
                for title in lines:
                    tid = max(self.model, default=0) + 1
                    self.model[tid] = {'title': title, 'category': expected['category'], 'done': 0,
                                       'deleted': 0, 'revision': 1}
            self.pending.pop(uid, None)
        update = self.s.send(text, uid, reply=reply)
        if self.expected_new_category is not None:
            new = [c for c in self.s.meta('custom_categories') if c['key'] not in self.categories]
            assert len(new) == 1 and new[0]['label'] == self.expected_new_category, ('new category', new)
            self.categories[new[0]['key']] = self.expected_new_category
            self.expected_new_category = None
        self.record('input' if reply else 'command', uid, text)
        self.check('message')
        return update

    def restart(self):
        self.s.restart()
        self.report['restarts'] += 1
        self.check('restart')

    def random_step(self):
        uid = self.rng.choice((OWNER, PARTNER))
        if self.rng.random() < .015 and any(b['callback_data'] == 'backup' for b in self.s.buttons()):
            return self.click('backup', uid)
        choice = self.rng.random()
        if self.pending and choice < .22:
            uid = self.rng.choice(list(self.pending))
            pending = self.pending[uid]
            prompt = self.s.meta(f'pending_{uid}')['prompt_id']
            if self.rng.random() < .20:
                return self.send('x' * 241, uid, reply=prompt)
            if pending['kind'] == 'category':
                if len(self.categories) >= 35:
                    return self.send('Groceries', uid, reply=prompt)
                title = f'Plan {self.seed % 100} {self.operations}'
            else:
                title = f'Item {self.seed % 100} {self.operations}'
                if self.rng.random() < .15:
                    title += '\nMilk duplicate title'
            return self.send(title, uid, reply=prompt, valid_input=True)
        if choice < .06:
            return self.send('/menu', uid)
        if choice < .11 and self.cached:
            source, value, original = self.rng.choice(self.cached)
            self.report['stale_button_attempts'] += 1
            return self.click(value, uid, source)
        buttons = [b['callback_data'] for b in self.s.buttons() if b['callback_data'] != 'backup']
        # Keep the complete model campaign interactive within Telegram's limits;
        # oversized single-message fallbacks have separate deterministic tests.
        buttons = [v for v in buttons if not v.startswith('add:') or v == 'add:choose' or
                   sum(t['category'] == v.split(':')[1] and not t['deleted'] for t in self.model.values()) < 35]
        tasks = [value for value in buttons if value.startswith(('task:', 'pick:', 'rpick:', 'batch:', 'finish:', 'undo:', 'category:restore:'))]
        confirmations = [value for value in buttons if value.startswith('batch:')]
        if confirmations and self.rng.random() < .3:
            return self.click(confirmations[0], uid)
        selected = self.rng.choice(tasks if tasks and self.rng.random() < .67 else buttons)
        update = self.click(selected, uid)
        if self.rng.random() < .09:
            self.s.state.replay(update)
            self.report['duplicate_deliveries'] += 1
            # A fresh unauthorized update is a barrier after the duplicate.
            self.send('/menu', STRANGER)
        return update

    def rapid_pair(self, pair_index):
        # Two users submit from the same visible panel without waiting for either
        # response. A worker-thread observer records every intermediate commit.
        self.send('/menu')
        self.click('cat:shopping')
        buttons = [b['callback_data'] for b in self.s.buttons() if b['callback_data'].startswith('task:')]
        if not buttons:
            self.click('add:shopping')
            self.send('Rapid one\nRapid two', reply=self.s.meta(f'pending_{OWNER}')['prompt_id'], valid_input=True)
            self.click('cat:shopping')
            buttons = [b['callback_data'] for b in self.s.buttons() if b['callback_data'].startswith('task:')]
        values = [buttons[0], buttons[0] if pair_index % 2 == 0 or len(buttons) == 1 else buttons[1]]
        panel = self.s.panel()
        valid = {b['callback_data'] for b in self.s.buttons()}
        observed = {}
        observation_lock = threading.Lock()
        original = self.s.worker.bot.handle

        def observe(update):
            original(update)
            if 'callback_query' in update:
                with observation_lock:
                    observed[update['update_id']] = task_map(
                        [dict(r) for r in self.s.worker.bot.store.conn.execute('SELECT * FROM tasks ORDER BY id')])

        self.s.worker.bot.handle = observe
        expected = []
        changes = []
        try:
            # Hold only the simulator input queue lock while enqueueing. The
            # requests are handled by the real polling/HTTP bot afterwards.
            with self.s.state.condition:
                updates = [self.s.state.click(panel['message_id'], value, uid=uid)
                           for value, uid in zip(values, (OWNER, PARTNER))]
            for update, value, uid in zip(updates, values, (OWNER, PARTNER)):
                before = copy.deepcopy(self.model)
                self.apply_callback(value, uid, valid, update['callback_query']['id'])
                expected.append(copy.deepcopy(self.model))
                changes.append(before != self.model)
                # After a task action its revised callback cannot be reused;
                # independent task callbacks remain valid on the same screen.
                _, action, tid, revision = value.split(':')
                if self.model[int(tid)]['revision'] != int(revision):
                    valid.discard(value)
            self.s.processed(updates[-1])
            for update, value, model, changed in zip(updates, values, expected, changes):
                with observation_lock:
                    actual = observed[update['update_id']]
                assert actual == model, ('rapid intermediate', pair_index, value, actual, model)
                self.report['rapid_actions'] += 1
                self.report['rapid_committed_changes' if changed else 'rapid_stale_rejections'] += 1
                self.checks += 1
                self.record('rapid', update['callback_query']['from']['id'], value)
            self.check('rapid pair')
        finally:
            self.s.worker.bot.handle = original

    def stats(self):
        return {'seed': self.seed, 'operations': self.operations, 'state_checks': self.checks,
                'action_counts': dict(self.actions), 'final_tasks': len(self.model),
                'final_categories': len(self.categories), 'manual_backup_documents': self.documents,
                'api_attempts': len(self.s.state.requests), 'api_dispatches': len(self.s.state.calls)}


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actions', type=int, default=10000)
    parser.add_argument('--rapid-actions', type=int, default=2000)
    parser.add_argument('--restarts', type=int, default=100)
    parser.add_argument('--output', type=Path, default=Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',Path(__file__).resolve().parents[2]/'work/tests/manual/home-campaign'))/'model-campaign.json')
    args = parser.parse_args()
    started = time.monotonic()
    report = {'requested_actions': args.actions, 'requested_rapid_actions': args.rapid_actions,
              'requested_restarts': args.restarts, 'randomized_actions': 0, 'rapid_actions': 0,
              'rapid_committed_changes': 0, 'rapid_stale_rejections': 0,
              'restarts': 0, 'duplicate_deliveries': 0, 'stale_button_attempts': 0,
              'seeds': [], 'passed': False, 'source_sha256': {
                  p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                  for p in ('bot.py', 'storage.py', 'simulator.py', 'seed_tasks.json',
                            str(Path(__file__).with_name('test_local_telegram.py')), str(Path(__file__)))}}
    campaign = None
    try:
        for index, seed in enumerate(SEEDS):
            campaign = Campaign(seed, report)
            target = args.actions // len(SEEDS) + (index < args.actions % len(SEEDS))
            restart_target = args.restarts // len(SEEDS) + (index < args.restarts % len(SEEDS))
            restart_done = 0
            while campaign.operations < target:
                campaign.random_step()
                should_restart = int(campaign.operations * restart_target / max(target, 1))
                while restart_done < should_restart:
                    campaign.restart()
                    restart_done += 1
                if campaign.operations % 250 == 0:
                    print(json.dumps({'seed': seed, 'actions': campaign.operations, 'restarts': report['restarts']}), flush=True)
            while restart_done < restart_target:
                campaign.restart()
                restart_done += 1
            report['randomized_actions'] += campaign.operations
            # Intentional manual backup, followed by navigation/restart assertions
            # that the backup inventory no longer changes.
            campaign.send('/menu')
            campaign.click('backup')
            campaign.send('/menu')
            campaign.check('post-backup navigation')
            assert campaign.s.query('PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
            report['seeds'].append(campaign.stats())
            campaign.s.close()
            campaign = None
            save_report(args.output, report)
        campaign = Campaign(2026110999, report)
        for pair in range((args.rapid_actions + 1) // 2):
            campaign.rapid_pair(pair)
            if (pair + 1) % 100 == 0:
                print(json.dumps({'rapid_pairs': pair + 1, 'rapid_actions': report['rapid_actions']}), flush=True)
        report['rapid_campaign'] = campaign.stats()
        campaign.s.close()
        campaign = None
        report['passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
        report['traceback'] = traceback.format_exc()
        if campaign:
            report['failed_seed'] = campaign.seed
            report['history'] = campaign.history
            report['failed_stats'] = campaign.stats()
        raise
    finally:
        if campaign:
            campaign.s.close()
        report['seconds'] = round(time.monotonic() - started, 2)
        completed_stats = report['seeds'] + ([report['rapid_campaign']] if 'rapid_campaign' in report else [])
        report['total_user_actions'] = sum(run['operations'] for run in completed_stats)
        report['state_checks'] = sum(run['state_checks'] for run in completed_stats)
        report['additional_setup_actions'] = (report['total_user_actions'] - report['randomized_actions']
                                              - report['rapid_actions'])
        report['manual_backup_documents'] = sum(run['manual_backup_documents'] for run in completed_stats)
        report['sources_unchanged'] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == expected
                                        for p, expected in report['source_sha256'].items())
        save_report(args.output, report)
        print(json.dumps({k: v for k, v in report.items() if k not in
                          {'seeds', 'source_sha256', 'history', 'traceback', 'error'}}), flush=True)


if __name__ == '__main__':
    main()
