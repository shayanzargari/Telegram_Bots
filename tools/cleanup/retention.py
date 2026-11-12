"""Expire redundant routine bulk while retaining compact outcomes and provenance."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]


def plan(root=ROOT):
    root = Path(root).resolve()
    policy = json.loads((root / 'config/retention.json').read_text('utf-8'))
    index_path = root / 'work/tests/index.json'
    index = json.loads(index_path.read_text('utf-8')) if index_path.exists() else {'runs': []}
    keep = set()
    candidates = {}
    per_pair = {}
    references = set()
    for document in [root / 'docs/PROJECT_STATUS.md', *(root / 'docs').glob('*.md')]:
        if document.exists():
            text = document.read_text('utf-8')
            for run in index['runs']:
                if 'work/tests/' + run['id'] in text:
                    references.add(run['id'])
    latest = root / 'work/tests/latest-acceptance.json'
    if latest.exists():
        references.add(json.loads(latest.read_text('utf-8'))['run_id'])
    for run in sorted(index['runs'], key=lambda value: value['created'], reverse=True):
        for result in run['results']:
            pair = result['suite'], result['environment']
            output = result.get('output')
            if not output:
                continue
            if result['outcome'] == 'Reused evidence':
                original = result.get('original_output')
                if original:
                    keep.add(original)
                continue
            per_pair[pair] = per_pair.get(pair, 0) + 1
            pinned = result.get('pin') or result.get('retention') in ('pinned', 'pinned-recovery', 'migration', 'acceptance') or run.get('pin') or run['id'] in references
            unresolved = result['outcome'] in ('Failed', 'Blocked') and not result.get('resolved_by')
            if pinned or unresolved or per_pair[pair] <= policy['routine_bundles_per_suite_environment']:
                keep.add(output)
            else:
                candidates[output] = {'output': output, 'owner': result.get('owner'), 'suite': result['suite'], 'environment': result['environment'],
                                      'outcome': result['outcome'], 'reason': 'Redundant resolved routine bulk outside latest retained bundles', 'compact_summary': 'work/tests/' + run['id'] + '/summary.json'}
    removals = []
    for output, item in candidates.items():
        if output in keep:
            continue
        path = (root / output).resolve()
        allowed = (root / 'work/tests').resolve()
        if allowed not in path.parents or path == allowed or path.is_symlink():
            raise ValueError('Retention output escapes the tests area.')
        item['logical_bytes'] = sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0
        removals.append(item)
    total = sum(p.stat().st_size for p in (root / 'work/tests').rglob('*') if p.is_file()) if (root / 'work/tests').exists() else 0
    return {'schema': 1, 'created': time.time(), 'removals': removals, 'protected_outputs': sorted(keep), 'routine_logical_bytes': total,
            'soft_budget_exceeded': total > policy['soft_routine_bytes'], 'compact_outcomes_retained': True, 'index': index}


def _apply(root=ROOT):
    root = Path(root).resolve()
    report = plan(root)
    for item in report['removals']:
        path = (root / item['output']).resolve()
        if (root / 'work/tests').resolve() not in path.parents or path.is_symlink():
            raise ValueError('Retention deletion target changed.')
        for child in path.rglob('*') if path.exists() else ():
            if child.is_symlink() or getattr(child.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Retention cannot traverse links.')
        if path.exists():
            shutil.rmtree(path)
        for run in report['index']['runs']:
            for result in run['results']:
                if result.get('output') == item['output']:
                    result['bulk_expired'] = time.time()
                    result['retained_summary'] = item['compact_summary']
    index = report.pop('index')
    temporary = root / 'work/tests/index.json.tmp'
    temporary.write_text(json.dumps(index, indent=2, sort_keys=True), 'utf-8')
    temporary.replace(root / 'work/tests/index.json')
    report['status'] = 'Applied'
    report['logical_bytes_removed'] = sum(item['logical_bytes'] for item in report['removals'])
    return report


def apply(root=ROOT):
    # The coordinator owns this resource implementation. Load that maintained
    # standard-library tool directly without changing sys.path or requiring an
    # editable install; both callers use the exact same cross-process resource.
    specification = importlib.util.spec_from_file_location('retention_test_coordinator', ROOT / 'tools/testing/run_tests.py')
    coordinator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(coordinator)
    with coordinator.reserve(Path(root).resolve(), ['test-evidence-index']):
        return _apply(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, default=ROOT); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = apply(args.root) if args.apply else plan(args.root)
    report.pop('index', None)
    print(json.dumps(report, indent=2))
