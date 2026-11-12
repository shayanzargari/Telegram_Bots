"""Selection, fingerprint/redaction, resource and bounded-retention regressions."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(specification); specification.loader.exec_module(module)
    return module


coordinator = load('suite_coordinator', 'tools/testing/run_tests.py')
retention = load('routine_retention', 'tools/cleanup/retention.py')
cleanup = load('controlled_cleanup', 'tools/cleanup/cleanup.py')


class CoordinatorTests(unittest.TestCase):
    def test_selection_deduplicates_same_suite_from_overlapping_profiles(self):
        catalog = [{'id': 'home.unit', 'inputs': ['bots/home'], 'profiles': ['smoke', 'acceptance']},
                   {'id': 'french.unit', 'inputs': ['bots/french'], 'profiles': ['acceptance']}]
        selected = coordinator.select_suites(catalog, ['home.unit'], ['smoke', 'acceptance'])
        self.assertEqual([suite['id'] for suite in selected], ['home.unit', 'french.unit'])
        self.assertEqual([suite['id'] for suite in coordinator.select_suites(catalog, profiles=['affected'], changed_paths=['bots\\home\\src\\file.py'])], ['home.unit'])

    def test_affected_selection_rejects_missing_bounds(self):
        with self.assertRaises(ValueError): coordinator.select_suites([{'id': 'one'}], profiles=['affected'])

    def test_unknown_suite_rejected(self):
        with self.assertRaises(ValueError): coordinator.select_suites([], requested=['unknown'])

    def test_unknown_impact_broadens_offline_and_documentation_stays_narrow(self):
        catalog = [{'id': 'docs', 'category': 'context', 'inputs': ['docs'], 'profiles': []},
                   {'id': 'app', 'category': 'unit', 'inputs': ['bots'], 'profiles': []},
                   {'id': 'live', 'category': 'live', 'inputs': [], 'profiles': []}]
        self.assertEqual([item['id'] for item in coordinator.select_suites(catalog, profiles=['affected'], changed_paths=['new-global.py'])], ['docs', 'app'])
        self.assertEqual([item['id'] for item in coordinator.select_suites(catalog, profiles=['affected'], changed_paths=['README.md'])], ['docs'])

    def test_fingerprint_detects_modified_installed_package_without_version_change(self):
        with tempfile.TemporaryDirectory(prefix='center-env-fingerprint-') as directory:
            environment = Path(directory) / 'environment'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)], check=True, capture_output=True, timeout=30)
            site = environment / ('Lib/site-packages' if sys.platform == 'win32' else 'lib/python3.11/site-packages')
            site.mkdir(parents=True, exist_ok=True)
            package = site / 'fixture_package.py'; package.write_text('value=1\n', 'utf-8')
            metadata = site / 'fixture_distribution-1.0.0.dist-info'; metadata.mkdir()
            (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture-distribution\nVersion: 1.0.0\n', 'utf-8')
            (metadata / 'RECORD').write_text('fixture_package.py,,\nfixture_distribution-1.0.0.dist-info/METADATA,,\n', 'utf-8')
            interpreter = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            first = coordinator.environment_fingerprint(interpreter)
            package.write_text('value=2\n', 'utf-8')
            second = coordinator.environment_fingerprint(interpreter)
            self.assertEqual(first['packages'][0]['version'], second['packages'][0]['version'])
            self.assertNotEqual(first['packages'][0]['installed_files_sha256'], second['packages'][0]['installed_files_sha256'])

    def test_redaction_occurs_before_persistent_logs_and_buffer_is_bounded(self):
        with tempfile.TemporaryDirectory(prefix='center-log-fixture-') as directory:
            path = Path(directory)
            canary = '123456789:' + 'ChunkedSyntheticCanary_1234567890'
            script = path / 'writer.py'
            script.write_text('import sys\nsys.stdout.write(' + repr('https://api.telegram.org/bot' + canary + '/getMe\n') + ')\nsys.stderr.write("x"*2200000)\n', 'utf-8')
            result = coordinator.execute([sys.executable, str(script)], path, None, path / 'output', 20)
            self.assertEqual(result['exit_code'], 0)
            self.assertNotIn(canary, (path / 'output/stdout.log').read_text('utf-8'))
            self.assertIn('[REDACTED]', (path / 'output/stdout.log').read_text('utf-8'))
            self.assertLess((path / 'output/stderr.log').stat().st_size, 1024 * 1024 + 200)

    def test_resource_lock_prevents_simultaneous_fixture_owners(self):
        with tempfile.TemporaryDirectory(prefix='center-resource-fixture-') as directory:
            root = Path(directory)
            with coordinator.reserve(root, ['browser-desktop']):
                with self.assertRaises(RuntimeError):
                    with coordinator.reserve(root, ['browser-desktop']): pass


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='center-retention-fixture-')
        self.root = Path(self.temporary.name); (self.root / 'config').mkdir(); (self.root / 'docs').mkdir(); (self.root / 'work/tests').mkdir(parents=True)
        (self.root / 'config/retention.json').write_text(json.dumps({'routine_bundles_per_suite_environment': 3, 'soft_routine_bytes': 512 * 1024 * 1024}), 'utf-8')

    def tearDown(self): self.temporary.cleanup()

    def fixture(self, outcome, ordinal, **extra):
        identity = 'fixture-' + str(ordinal); output = 'work/tests/' + identity + '/suite'
        folder = self.root / output; folder.mkdir(parents=True); (folder / 'bulk.log').write_bytes(b'x' * 10000)
        result = {'suite': 'one', 'owner': 'fixture', 'environment': 'core', 'outcome': outcome, 'output': output, **extra}
        record = {'id': identity, 'created': ordinal, 'results': [result]}
        (folder.parent / 'summary.json').write_text(json.dumps(record), 'utf-8')
        return record

    def test_four_runs_keep_outcomes_and_reused_provenance_while_resolved_bulk_expires(self):
        runs = [self.fixture('Failed', 1, resolved_by='fixture-2'), self.fixture('Passed', 2), self.fixture('Passed', 3), self.fixture('Passed', 4)]
        reused = {'id': 'fixture-reused', 'created': 5, 'results': [{'suite': 'other', 'environment': 'core', 'outcome': 'Reused evidence',
                  'output': 'work/tests/fixture-3/suite', 'original_output': 'work/tests/fixture-3/suite'}]}
        runs.append(reused)
        (self.root / 'work/tests/index.json').write_text(json.dumps({'schema': 1, 'runs': runs}), 'utf-8')
        first = retention.apply(self.root)
        self.assertEqual(first['logical_bytes_removed'], 10000)
        self.assertFalse((self.root / 'work/tests/fixture-1/suite').exists())
        self.assertTrue((self.root / 'work/tests/fixture-1/summary.json').exists())
        self.assertTrue((self.root / 'work/tests/fixture-3/suite').exists())
        retained = json.loads((self.root / 'work/tests/index.json').read_text('utf-8'))
        self.assertEqual(retained['runs'][0]['results'][0]['outcome'], 'Failed')
        self.assertEqual(retained['runs'][-1]['results'][0]['outcome'], 'Reused evidence')
        second = retention.apply(self.root)
        self.assertEqual(second['logical_bytes_removed'], 0)
        self.assertLess(second['routine_logical_bytes'], 100000)

    def test_unresolved_failure_and_current_checkpoint_reference_override_routine_cap(self):
        runs = [self.fixture('Failed', 1)] + [self.fixture('Passed', number) for number in range(2, 6)]
        (self.root / 'docs/PROJECT_STATUS.md').write_text('Evidence work/tests/fixture-2/summary.json', 'utf-8')
        (self.root / 'work/tests/index.json').write_text(json.dumps({'schema': 1, 'runs': runs}), 'utf-8')
        report = retention.apply(self.root)
        self.assertTrue((self.root / 'work/tests/fixture-1/suite').exists())
        self.assertTrue((self.root / 'work/tests/fixture-2/suite').exists())


class CleanupTests(unittest.TestCase):
    def test_missing_tree_nested_git_and_original_state_are_protected(self):
        with tempfile.TemporaryDirectory(prefix='center-cleanup-protected-') as directory:
            root = Path(directory).resolve()
            proof = root / 'proof.json'; proof.write_text('{"status":"Passed"}', 'utf-8')
            output = root / 'work/obsolete'; output.mkdir(parents=True); (output / 'file').write_bytes(b'old')
            entry = {'path': str(output), 'owner': 'fixture', 'classification': 4, 'reason': 'Reproducible generated output',
                     'consumers_checked': ['fixture'], 'preservation_route': 'reproduce', 'verification': ['proof.json'],
                     'expected_fingerprint': cleanup.fingerprint(output), 'status': 'Eligible', 'no_active_consumer': True, 'no_active_run': True}
            for relative in ('DATA', '.SECRETS', 'Our_Home_Telegram_Bot/data', 'French Notes/french', 'French Notes'):
                target = root / relative; target.mkdir(parents=True, exist_ok=True); entry['path'] = str(target)
                with self.assertRaises(ValueError): cleanup.validate(root, entry)
                self.assertTrue(target.is_dir())
            entry['path'] = str(root / 'work/missing')
            with self.assertRaises(ValueError): cleanup.validate(root, entry)
            repository = root / 'archive/checkout'; (repository / '.git').mkdir(parents=True)
            entry['path'] = str(repository)
            with self.assertRaises(ValueError): cleanup.validate(root, entry)
            self.assertTrue((repository / '.git').is_dir())

    def test_changed_target_or_protected_state_cannot_pass_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='center-cleanup-fixture-') as directory:
            root = Path(directory).resolve(); output = root / 'work/obsolete'; output.mkdir(parents=True); (output / 'file').write_bytes(b'old')
            proof = root / 'proof.json'; proof.write_text('{"status":"Passed"}', 'utf-8')
            entry = {'path': str(output), 'owner': 'fixture', 'classification': 4, 'reason': 'Reproducible generated output',
                     'consumers_checked': ['fixture'], 'preservation_route': 'reproduce', 'verification': ['proof.json'],
                     'expected_fingerprint': cleanup.fingerprint(output), 'status': 'Eligible', 'no_active_consumer': True, 'no_active_run': True}
            cleanup.validate(root, entry)
            (output / 'file').write_bytes(b'new changed material')
            with self.assertRaises(ValueError): cleanup.validate(root, entry)
            state = root / 'data'; state.mkdir(); entry['path'] = str(state)
            with self.assertRaises(ValueError): cleanup.validate(root, entry)


if __name__ == '__main__': unittest.main()
