"""Concurrent installed launchers against a private, credential-free core."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from bot_center.commands import CommandService
from bot_center_runtime.ipc import IPCError, process_creation, request


WORKSPACE = Path(__file__).resolve().parents[2]
COREPY = WORKSPACE / '.venvs/core/Scripts/python.exe'
COREPYW = WORKSPACE / '.venvs/core/Scripts/pythonw.exe'

# Only executable location is adapted: the fixture uses the already installed
# environment, while configuration, state, locks, IPC and processes are real.
LAUNCH = r'''
import json,os,subprocess,sys,uuid
from pathlib import Path
from unittest.mock import patch
from bot_center.launcher import launch
root=Path(sys.argv[1]);pythonw=sys.argv[2];original=subprocess.Popen
def create(arguments,*args,**kwargs):
    arguments=list(arguments)
    if arguments[0]==str(root/'.venvs/core/Scripts/pythonw.exe'):
        arguments[0]=pythonw
        child=original(arguments,*args,**kwargs)
        (root/('spawn-'+uuid.uuid4().hex+'.json')).write_text(json.dumps({'pid':child.pid}),encoding='utf-8')
        return child
    return original(arguments,*args,**kwargs)
with patch('bot_center.launcher.subprocess.Popen',side_effect=create):
    response=launch(root,open_browser=False)
print(json.dumps({'pid':response['pid'],'creation':response['creation'],'instance':response['instance']}))
'''

OUTSIDE_OWNER = r'''
import json,os,sys,time
from pathlib import Path
from bot_center_runtime.locks import ResourceLock
root=Path(sys.argv[1])
with ResourceLock('poll:'+sys.argv[2]):
    (root/'outside-ready.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf-8')
    while not (root/'outside-stop').exists():time.sleep(.05)
'''


@unittest.skipUnless(os.name == 'nt' and COREPYW.is_file(), 'Installed native Windows core required')
class ConcurrentLaunchers(unittest.TestCase):
    def wait(self, predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.05)
        self.fail('Private launcher fixture did not complete its lifecycle.')

    def wave(self, root, count):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        processes = [subprocess.Popen(
            [str(COREPY), '-c', LAUNCH, str(root), str(COREPYW)],
            cwd=root, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=0x08000000,
        ) for _ in range(count)]
        results = []
        try:
            for process in processes:
                output, errors = process.communicate(timeout=65)
                self.assertEqual(process.returncode, 0, errors)
                results.append(json.loads(output))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        return results

    def close_core(self, root, identity):
        if not identity:
            return
        # An authenticated PID plus exact creation identity gates the only
        # termination fallback; unrelated listeners/processes are never targets.
        try:
            if process_creation(identity['pid']) != identity['creation']:
                return
        except (OSError, IPCError):
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000 | 0x0001, False, identity['pid'])
        if not handle:
            return
        try:
            try:
                request(root, {'action': 'quit', 'target': '*'}, timeout=5)
            except (OSError, IPCError):
                pass
            if kernel.WaitForSingleObject(handle, 15000) == 258:
                kernel.TerminateProcess(handle, 91)
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            kernel.CloseHandle(handle)

    def test_concurrent_clicks_share_one_core_and_saved_stop_survives_reopening(self):
        identity = None
        outside = None
        with tempfile.TemporaryDirectory(prefix='center-launcher-') as directory:
            root = Path(directory).resolve()
            (root / 'config').mkdir()
            (root / 'data/fixture').mkdir(parents=True)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            (root / 'config/installation.json').write_text('{"schema":1,"generation":"private-launcher"}', encoding='utf-8')
            (root / 'config/settings.json').write_text(json.dumps({'port': port}), encoding='utf-8')
            manifest = {'schema_version': 1, 'contract_version': 1, 'id': 'fixture',
                        'display_name': 'Private launcher fixture', 'module': 'fixture.managed',
                        'python': str(COREPY), 'cwd': 'data/fixture', 'data_dir': 'data/fixture',
                        'log_dir': 'logs/fixture', 'credential_ref': 'fixture',
                        'telegram_id': 976543210, 'capabilities': ['polling'], 'required_state': [], 'jobs': []}
            (root / 'config/registry.json').write_text(json.dumps({'schema': 1, 'bots': [manifest]}), encoding='utf-8')
            service = CommandService(root)
            try:
                service.enroll(manifest, enabled=True, desired='stopped')
            finally:
                service.close()
            try:
                first = self.wave(root, 6)
                identity = first[0]
                self.assertTrue(all(item == identity for item in first))
                self.assertEqual(len(list(root.glob('spawn-*.json'))), 1)
                status = request(root, {'action': 'status'})['body']
                self.assertTrue(status['ready'])
                self.assertEqual(status['bots'][0]['desired'], 'stopped')
                self.assertIsNone(status['bots'][0]['process_id'])
                stop = request(root, {'action': 'stop', 'target': 'fixture', 'operation_id': 'saved-stop'})['body']
                self.assertEqual(stop['status'], 'Accepted')
                repeated = self.wave(root, 3)
                self.assertTrue(all(item == identity for item in repeated))
                self.assertEqual(len(list(root.glob('spawn-*.json'))), 1)
                self.assertEqual(request(root, {'action': 'status'})['body']['bots'][0]['desired'], 'stopped')
                outside = subprocess.Popen([str(COREPY), '-c', OUTSIDE_OWNER, str(root), str(manifest['telegram_id'])],
                                           cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, creationflags=0x08000000)
                self.wait(lambda: (root / 'outside-ready.json').is_file())
                self.wait(lambda: request(root, {'action': 'status'})['body']['bots'][0]['external_hold'])
                self.assertEqual(request(root, {'action': 'status'})['body']['bots'][0]['observed'], 'Running outside core')
                (root / 'outside-stop').touch()
                outside.wait(timeout=5)
                self.wait(lambda: request(root, {'action': 'status'})['body']['bots'][0]['observed'] == 'Outside core hold')
                old = identity
                self.close_core(root, identity)
                identity = None
                reopened = self.wave(root, 3)
                identity = reopened[0]
                self.assertNotEqual(identity['instance'], old['instance'])
                self.assertTrue(all(item == identity for item in reopened))
                self.assertEqual(len(list(root.glob('spawn-*.json'))), 2)
                bot = request(root, {'action': 'status'})['body']['bots'][0]
                self.assertEqual(bot['desired'], 'stopped')
                self.assertIsNone(bot['process_id'])
                self.assertTrue(bot['external_hold'])
                output = Path(os.environ.get('BOT_CENTER_TEST_OUTPUT', WORKSPACE / 'work/tests/manual-launcher'))
                output.mkdir(parents=True, exist_ok=True)
                (output / 'concurrent-launcher-evidence.json').write_text(json.dumps({
                    'status': 'Passed', 'concurrent_first_clicks': 6, 'repeated_clicks': 3,
                    'concurrent_reopen_clicks': 3, 'spawned_cores': 2,
                    'one_core_per_lifetime': True, 'saved_stop_preserved': True,
                    'outside_identity_owner_exit_and_core_reopen_preserve_hold': True,
                    'bot_polling_started': False, 'real_telegram': False,
                    'installed_interpreter': str(COREPY),
                }, indent=2), encoding='utf-8')
            finally:
                if outside is not None and outside.poll() is None:
                    (root / 'outside-stop').touch()
                    try:
                        outside.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        outside.terminate()
                        outside.wait(timeout=5)
                if identity is None:
                    discovery = root / 'data/core/discovery.json'
                    if discovery.is_file():
                        identity = json.loads(discovery.read_text('utf-8'))
                self.close_core(root, identity)


if __name__ == '__main__':
    unittest.main()
