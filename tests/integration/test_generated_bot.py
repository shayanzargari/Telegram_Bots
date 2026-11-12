"""Build and register one installed template bot without production state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
import uuid

from bot_center.commands import CommandService
from bot_center.registration import RegistrationService


WORKSPACE = Path(__file__).resolve().parents[2]
WHEELHOUSE = WORKSPACE / 'work/build/wheelhouse'
SHARED = WHEELHOUSE / 'bot_center_runtime-1.0.0-py3-none-any.whl'


@unittest.skipUnless(os.name == 'nt' and SHARED.is_file(), 'Built native Windows runtime required')
class GeneratedBotInstallation(unittest.TestCase):
    def test_generated_bot_installs_its_own_environment_and_passes_fake_contracts(self):
        output = Path(os.environ.get('BOT_CENTER_TEST_OUTPUT', WORKSPACE / 'work/tests/manual-generated'))
        output.mkdir(parents=True, exist_ok=True)
        root = output / ('fixture-' + uuid.uuid4().hex[:8])
        root.mkdir()
        (root / 'config').mkdir()
        (root / 'docs').mkdir()
        (root / 'config/installation.json').write_text('{"schema":1,"generation":"generated-fixture"}', encoding='utf-8')
        (root / 'config/registry.json').write_text('{"schema":1,"bots":[]}', encoding='utf-8')
        (root / 'config/suites.json').write_text('{"schema":1,"suites":[]}', encoding='utf-8')
        (root / 'config/operations.json').write_text('{"schema":1,"operations":[]}', encoding='utf-8')
        wheels = root / 'work/build/wheelhouse'
        wheels.mkdir(parents=True)
        copied = []
        for pattern in ('setuptools-80.9.0-*.whl', 'wheel-0.45.1-*.whl'):
            matches = list(WHEELHOUSE.glob(pattern))
            self.assertEqual(len(matches), 1, 'Pinned local template build wheel is missing.')
            source = matches[0]
            shutil.copy2(source, wheels / source.name)
            copied.append({'name': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
        service = RegistrationService(root, template_root=WORKSPACE / 'templates/python_bot')
        result = service.create('garden-fixture', 'Garden fixture', telegram_id=888888, shared_wheel=SHARED)
        self.assertEqual(result['status'], 'Completed')
        self.assertFalse(result['enabled'])
        self.assertEqual(result['desired'], 'stopped')
        manifest = result['manifest']
        python = root / manifest['python']
        self.assertTrue(python.is_file())
        self.assertNotEqual(python.resolve(), Path(sys.executable).resolve())
        check = CommandService(root, recover=False)
        try:
            original = check.bot('garden-fixture')
            self.assertFalse(original['enabled'])
            self.assertEqual(original['desired'], 'stopped')
            self.assertIsNone(original['run_id'])
            check.submit('garden-fixture', 'enable', operation_id='enable-without-start')
            enabled = check.bot('garden-fixture')
            self.assertTrue(enabled['enabled'])
            self.assertEqual(enabled['desired'], 'stopped')
            self.assertIsNone(enabled['run_id'])
            check.submit('garden-fixture', 'disable', operation_id='return-disabled')
        finally:
            check.close()
        service.validate(manifest, existing=True, allow_registered_id=manifest['id'])
        probe = '''import importlib.util,json,sys
from pathlib import Path
import garden_fixture_bot.managed as application
import bot_center_runtime as runtime
print(json.dumps({'python':sys.executable,'application':application.__file__,'shared':runtime.__file__,
 'fastapi_available':importlib.util.find_spec('fastapi') is not None,
 'unrelated_application_imports':[n for n in sys.modules if n=='bot_center' or n.startswith(('home_bot','french_notes'))]}))
'''
        observed = subprocess.run([str(python), '-c', probe], cwd=output, capture_output=True,
                                  text=True, encoding='utf-8', timeout=30, creationflags=0x08000000)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        installed = json.loads(observed.stdout)
        site_packages = root / '.venvs/garden-fixture/Lib/site-packages'
        self.assertTrue(Path(installed['application']).resolve().is_relative_to(site_packages.resolve()))
        self.assertTrue(Path(installed['shared']).resolve().is_relative_to(site_packages.resolve()))
        self.assertFalse(installed['fastapi_available'])
        self.assertEqual(installed['unrelated_application_imports'], [])
        contracts = subprocess.run([str(Path(sys.executable)), str(WORKSPACE / 'tools/testing/run_tests.py'),
                                    '--root', str(root), '--suite', 'garden-fixture.contract', '--fresh'],
                                   cwd=output, capture_output=True, text=True, encoding='utf-8',
                                   timeout=180, creationflags=0x08000000)
        (output / 'generated-coordinator.log').write_text(contracts.stdout + contracts.stderr, encoding='utf-8')
        self.assertEqual(contracts.returncode, 0, contracts.stdout + contracts.stderr)
        latest = json.loads((root / 'work/tests/latest.json').read_text('utf-8'))
        summary_path = root / latest['summary']
        summary = json.loads(summary_path.read_text('utf-8'))
        self.assertEqual(len(summary['results']), 1)
        self.assertEqual(summary['results'][0]['outcome'], 'Passed')
        diagnostics = root / summary['results'][0]['output'] / 'stderr.log'
        count = re.search(r'Ran (\d+) tests?', diagnostics.read_text('utf-8'))
        self.assertIsNotNone(count)
        self.assertGreaterEqual(int(count[1]), 6)
        # Record the untouched production boundary and reproducible artifact
        # identities alongside the nested coordinator's actual evidence.
        (output / 'generated-bot-evidence.json').write_text(json.dumps({
            'status': 'Passed', 'fixture': str(root), 'new_environment': str(python),
            'installed': installed, 'initial_enrollment': 'disabled/stopped',
            'enable_keeps_stopped': True, 'validation_starts_no_process': True,
            'contract_count': int(count[1]), 'contract_summary': str(summary_path),
            'shared_wheel_sha256': hashlib.sha256(SHARED.read_bytes()).hexdigest(),
            'build_inputs': copied, 'production_state_changed': False,
            'real_telegram': False, 'credential_imported': False,
            'template': {str(p.relative_to(WORKSPACE)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (WORKSPACE / 'templates/python_bot').rglob('*') if p.is_file()},
        }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
