"""Repeatable local latency sample; optionally load an earlier source directory."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--request-delay', type=float, default=0.1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Test support is beside this script; production behavior is imported from
    # the wheel installed in the explicitly selected environment.
    from test_local_telegram import HTTPScenario
    scenario = HTTPScenario()
    measurements = []
    try:
        scenario.click('cat:shopping')
        original = scenario.worker.bot.api.call
        def delayed(method, *values, **kwargs):
            if method != 'getUpdates':
                time.sleep(args.request_delay)
            return original(method, *values, **kwargs)
        scenario.worker.bot.api.call = delayed
        for action in ('complete', 'reopen') * 6:
            index = len(scenario.state.calls)
            started = time.perf_counter()
            scenario.click(f'task:{action}:1:')
            elapsed = time.perf_counter() - started
            calls = [c['method'] for c in scenario.state.calls[index:] if c['method'] != 'getUpdates']
            measurements.append({'action': action, 'ms': round(elapsed * 1000, 1), 'calls': calls})
            scenario.wait(lambda: scenario.meta('exports_dirty') is not True)
        result = {'request_delay_ms': args.request_delay * 1000,
                  'samples': len(measurements),
                  'median_ms': round(statistics.median(m['ms'] for m in measurements), 1),
                  'max_ms': max(m['ms'] for m in measurements), 'measurements': measurements}
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in result.items() if key != 'measurements'}))
    finally:
        scenario.close()


if __name__ == '__main__':
    main()
