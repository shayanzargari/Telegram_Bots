"""Controlled HTTP action latency, durable-commit and SQL/API operation audit."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time


def percentiles(values):
    if not values:
        return None
    values = sorted(values)
    def percentile(p):
        index = (len(values) - 1) * p
        low = int(index)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (index - low)
    return {'n': len(values), 'median_ms': round(statistics.median(values), 2),
            'p95_ms': round(percentile(.95), 2), 'p99_ms': round(percentile(.99), 2),
            'max_ms': round(max(values), 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--delays', default='0,0.1,0.3,1')
    args = parser.parse_args()
    source = args.source.resolve()
    from home_bot import simulator as local_telegram
    from home_bot import bot
    from home_bot.storage import Store
    from test_local_telegram import HTTPScenario

    def acknowledged_only(self, update):
        if update:
            self.wait(lambda: (self.meta('last_update_id') or -1) >= update['update_id'])
        return update
    # Integration helpers may also await eventual prompt deletion. Keep action
    # acknowledgement timing separate from background cleanup in this benchmark.
    HTTPScenario.processed = acknowledged_only

    traces, commits = [], []
    class TracedStore(Store):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.conn.set_trace_callback(lambda sql: traces.append((time.perf_counter(), sql.split()[0])))
            for name in ('add_task', 'add_tasks', 'add_category', 'change_task', 'change_tasks', 'remove_category', 'restore_category'):
                original = getattr(self, name, None)
                if original is None:
                    continue
                def tracked(*a, _name=name, _original=original, **kw):
                    result = _original(*a, **kw)
                    commits.append((time.perf_counter(), _name))
                    return result
                setattr(self, name, tracked)
    local_telegram.Store = TracedStore
    bot.Store = TracedStore
    output = {'source': str(source), 'method': 'real loopback HTTP; delay each non-poll API call',
              'tail_note': 'Descriptive quantiles from a small controlled sample, not production SLO estimates.',
              'scenarios': [], 'passed': False}
    for delay in map(float, args.delays.split(',')):
        started = time.perf_counter()
        s = HTTPScenario()
        startup_ms = (time.perf_counter() - started) * 1000
        samples, visible_events = [], []
        try:
            dispatch = s.state.dispatch
            def record(method, payload, *a, **kw):
                result = dispatch(method, payload, *a, **kw)
                if method in ('editMessageText', 'sendMessage'):
                    visible_events.append((time.perf_counter(), method,
                                           payload.get('message_id', (result or {}).get('message_id'))))
                return result
            s.state.dispatch = record
            original = s.worker.bot.api.call
            def delayed(method, *a, **kw):
                if method != 'getUpdates':
                    time.sleep(delay)
                return original(method, *a, **kw)
            s.worker.bot.api.call = delayed

            def measure(name, operation):
                # Setup/previous acknowledgement is outside this action's interval.
                s.wait(lambda: all(j.done() for j in s.worker.bot.answer_jobs), timeout=10)
                s.wait(lambda: not s.meta('exports_dirty'), timeout=20)
                s.wait(lambda: not s.meta('prompt_deletions'), timeout=20)
                api_start, sql_start, visible_start, commit_start = len(s.state.calls), len(traces), len(visible_events), len(commits)
                panel_id = s.panel()['message_id']
                start = time.perf_counter()
                result = operation()
                completed = time.perf_counter()
                s.wait(lambda: all(j.done() for j in s.worker.bot.answer_jobs), timeout=10)
                s.wait(lambda: not s.meta('exports_dirty'), timeout=20)
                s.wait(lambda: not s.meta('prompt_deletions'), timeout=20)
                visible = [t for t, _, mid in visible_events[visible_start:] if mid == panel_id]
                saved = [t for t, _ in commits[commit_start:]]
                calls = Counter(c['method'] for c in s.state.calls[api_start:] if c['method'] != 'getUpdates')
                sql = Counter(kind for _, kind in traces[sql_start:])
                samples.append({'action': name, 'processed_ms': round((completed-start)*1000, 2),
                    'visible_ms': round((min(visible)-start)*1000, 2) if visible else 0,
                    'commit_ms': round((max(saved)-start)*1000, 2) if saved else None,
                    'api_requests': dict(calls), 'sql_statements': dict(sql),
                    'render_cached': not bool(visible)})
                return result

            count = 12 if delay == 0 else (10 if delay == .1 else 4)
            for index in range(count):
                s.send('/menu')
                measure('open_category', lambda: s.click('cat:shopping'))
                measure('check_task', lambda: s.click('task:complete:1:'))
                measure('uncheck_task', lambda: s.click('task:reopen:1:'))
                measure('finish_category', lambda: s.click('finish:shopping:'))
                s.click('undo:')
                measure('open_menu', lambda: s.click('menu'))
                measure('show_updated_list', lambda: s.click('showlist'))
                s.click('menu')
                measure('start_category_input', lambda: s.click('category:add'))
                prompt = s.meta(f'pending_{local_telegram.OWNER}')['prompt_id']
                measure('create_category', lambda: s.send(f'Benchmark {index}', reply=prompt))
                s.click('menu')
                s.click('cat:shopping')
                s.click('add:shopping')
                prompt = s.meta(f'pending_{local_telegram.OWNER}')['prompt_id']
                measure('add_task', lambda: s.send(f'Task {index}', reply=prompt))
                s.click('cat:shopping')
                s.click('remove:shopping')
                if any(b['callback_data'].startswith('pick:') for b in s.buttons()):
                    measure('select_task_for_removal', lambda: s.click('pick:1:'))
                    measure('remove_task', lambda: s.click('batch:remove:'))
                else:  # Frozen pre-selection source remains benchmarkable.
                    measure('remove_task', lambda: s.click('task:delete:1:'))
                s.click('menu')
                s.click('view:all:trash:')
                if any(b['callback_data'].startswith('rpick:') for b in s.buttons()):
                    measure('select_task_for_restore', lambda: s.click('rpick:1:'))
                    measure('restore_task', lambda: s.click('batch:restore:'))
                else:  # Frozen pre-selection source remains benchmarkable.
                    measure('restore_task', lambda: s.click('task:restore:1:'))
                s.click('menu')
                s.click('cat:study')
                measure('remove_category', lambda: s.click('category:remove:study'))
                # If a confirmation screen is introduced it must be measured explicitly.
                if any(b['callback_data'].startswith('category:confirm:') for b in s.buttons()):
                    measure('confirm_remove_category', lambda: s.click('category:confirm:'))
                s.click('menu')
                s.click('view:all:trash:')
                measure('restore_category', lambda: s.click('category:restore:study:'))
            started = time.perf_counter()
            s.restart()
            restart_ms = (time.perf_counter()-started)*1000
            groups = {}
            for name in sorted({row['action'] for row in samples}):
                selected = [row for row in samples if row['action'] == name]
                groups[name] = {metric: percentiles([row[metric] for row in selected if row[metric] is not None])
                                for metric in ('processed_ms', 'visible_ms', 'commit_ms')}
                groups[name]['median_api_requests'] = statistics.median(sum(row['api_requests'].values()) for row in selected)
                groups[name]['median_sql_statements'] = statistics.median(sum(row['sql_statements'].values()) for row in selected)
            output['scenarios'].append({'delay_ms': delay*1000, 'startup_ms': round(startup_ms,2),
                'restart_ms': round(restart_ms,2), 'sample_count':len(samples), 'groups':groups, 'samples':samples})
            args.output.write_text(json.dumps(output,indent=2),encoding='utf-8')
            print(json.dumps({'delay_ms':delay*1000,'samples':len(samples),'passed':True}),flush=True)
        finally:
            s.close()
    output['passed'] = True
    args.output.write_text(json.dumps(output,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
