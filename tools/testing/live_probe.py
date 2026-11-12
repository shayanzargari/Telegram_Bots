"""Observe both real managed pollers through authenticated IPC, without sending messages."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from bot_center_runtime.ipc import request
from bot_center_runtime.redaction import redact


ROOT = Path(__file__).resolve().parents[2]


def observe(root, output, timeout=60):
    root = Path(root).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    samples = []
    consecutive = 0
    reason = None
    while time.monotonic() < deadline:
        try:
            status = request(root, {'action': 'status'}, timeout=10)['body']
            bots = {bot['id']: bot for bot in status.get('bots', [])}
            observed = []
            for identity in ('home', 'french'):
                bot = bots.get(identity, {})
                observed.append({key: bot.get(key) for key in (
                    'id', 'run_id', 'process_id', 'desired', 'enabled', 'observed',
                    'ready', 'connection', 'last_poll_age_seconds', 'telemetry_age_seconds')})
            good = status.get('ready') and all(
                bot.get('id') and bot.get('ready') and bot.get('process_id')
                and bot.get('connection') == 'Connected' for bot in observed)
            samples.append({'at': time.time(), 'core_ready': bool(status.get('ready')), 'bots': observed})
            consecutive = consecutive + 1 if good else 0
            if consecutive >= 3:
                break
            if any(bot.get('attention') for bot in bots.values()):
                reason = 'A bot requires attention; inspect authenticated dashboard diagnostics.'
                break
        except Exception as error:
            reason = redact(error)
            consecutive = 0
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    passed = consecutive >= 3
    result = {'status': 'Passed' if passed else 'Failed', 'scope': 'Both real managed bots concurrently completed Telegram polls with current telemetry.',
              'observations': samples, 'messages_sent_by_probe': 0,
              'reply_behavior': 'Not assessed; requires an inbound user interaction.',
              'phone_verification': 'Separate manual check', 'reason': None if passed else reason or 'Concurrent connected polling did not reach three consecutive observations.'}
    (output / 'live-polling.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'observations'}))
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    return observe(args.root, args.output or Path(os.environ.get('BOT_CENTER_TEST_OUTPUT', ROOT / 'work/live/polling')))


if __name__ == '__main__':
    raise SystemExit(main())
