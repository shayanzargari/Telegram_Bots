from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from bot_center_runtime.credentials import CredentialStore,protect,IdentityError,ConnectivityError,verify_identity,private_directory
from bot_center_runtime.locks import ResourceLock,LockBusy,user_sid
from bot_center_runtime.process import ManagedProcess,Job

FIXTURE=Path(__file__).with_name('native_fixture.py')


def wait_for(predicate,seconds=8):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.02)
    raise AssertionError('Owned fixture did not reach its expected state before the deadline.')


def synthetic():return '123456789'+':'+'native_fixture_'+'B'*30


def descriptor_sddl(path):
    advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes=[w.LPWSTR,ctypes.c_int,w.DWORD,w.LPVOID,w.LPVOID,w.LPVOID,w.LPVOID,ctypes.POINTER(w.LPVOID)]
    advapi.GetNamedSecurityInfoW.restype=w.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[w.LPVOID,w.DWORD,w.DWORD,ctypes.POINTER(w.LPWSTR),ctypes.POINTER(w.ULONG)]
    kernel.LocalFree.argtypes=[w.HLOCAL]
    descriptor=w.LPVOID();text=w.LPWSTR()
    result=advapi.GetNamedSecurityInfoW(str(path),1,4,None,None,None,None,ctypes.byref(descriptor))
    if result:raise ctypes.WinError(result)
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor,1,4,ctypes.byref(text),None):raise ctypes.WinError(ctypes.get_last_error())
        try:return text.value
        finally:kernel.LocalFree(text)
    finally:kernel.LocalFree(descriptor)


def terminate_owned_identity(pid):
    """Terminate the PID reported by this fixture, including a venv redirector child."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x100001,False,pid)
    if not handle:return
    try:
        if kernel.WaitForSingleObject(handle,0)==258:kernel.TerminateProcess(handle,1)
        kernel.WaitForSingleObject(handle,5000)
    finally:kernel.CloseHandle(handle)


@unittest.skipUnless(os.name=='nt','native Windows required')
class CredentialTests(unittest.TestCase):
    def test_dpapi_recovery_roundtrip_not_plaintext_and_tamper_rejected(self):
        secret=synthetic();sealed=protect(secret.encode())
        self.assertFalse(secret.encode() in sealed,'DPAPI ciphertext contains plaintext canary.')
        self.assertEqual(protect(sealed,True).decode(),secret)
        from bot_center_runtime.credentials import CredentialError
        with self.assertRaises(CredentialError):protect(sealed[:-5]+b'wrong',True)

    def test_credential_rotation_wrong_valid_identity_preserves_working_ciphertext(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=CredentialStore(temporary);old=synthetic();store.store_protected('fixture',old)
            before=store.path('fixture').read_bytes()
            response=io.BytesIO(json.dumps({'ok':True,'result':{'id':2222,'username':'wrong_fixture_bot'}}).encode())
            with patch('bot_center_runtime.credentials.urllib.request.urlopen',return_value=response),self.assertRaises(IdentityError):
                store.stage_replace('fixture','987654321'+':'+'other_fixture_'+'C'*30,expected_id=1111)
            self.assertEqual(store.path('fixture').read_bytes(),before)
            self.assertEqual(store.load('fixture'),old)
            self.assertFalse(any(old.encode() in p.read_bytes() for p in (Path(temporary)/'.secrets').iterdir()))

    def test_network_failure_does_not_become_invalid_credential_or_replace_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=CredentialStore(temporary);store.store_protected('fixture',synthetic());before=store.path('fixture').read_bytes()
            with patch('bot_center_runtime.credentials.urllib.request.urlopen',side_effect=OSError('fixture network unavailable')),self.assertRaises(ConnectivityError):
                store.stage_replace('fixture',synthetic(),expected_id=1111)
            self.assertEqual(store.path('fixture').read_bytes(),before)

    def test_private_directory_removes_preexisting_everyone_grant(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'secrets';directory.mkdir()
            subprocess.run(['icacls',str(directory),'/grant','*S-1-1-0:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL,creationflags=0x08000000)
            private_directory(directory)
            # Query native trustees without localization or a shell intermediary.
            trustees=set(re.findall(r';;;([^;)]+)\)',descriptor_sddl(directory)))
            self.assertEqual(trustees,{user_sid()},'Private credential directory retained another principal grant.')


@unittest.skipUnless(os.name=='nt','native Windows required')
class LockTests(unittest.TestCase):
    def name(self):return 'poll:fixture-'+uuid.uuid4().hex
    def acquire_child(self,name):
        return subprocess.run([sys.executable,str(FIXTURE),'--mode','acquire','--name',name],capture_output=True,text=True,timeout=10,creationflags=0x08000000)

    def test_core_first_blocks_standalone_and_release_allows_it(self):
        name=self.name()
        with ResourceLock(name):
            child=self.acquire_child(name);self.assertEqual(child.returncode,3);self.assertEqual(child.stdout.strip(),'busy')
        self.assertEqual(self.acquire_child(name).returncode,0)

    def test_standalone_first_blocks_core_and_owner_loss_releases(self):
        name=self.name()
        with tempfile.TemporaryDirectory() as temporary:
            process=subprocess.Popen([sys.executable,str(FIXTURE),'--mode','hold','--name',name,'--directory',temporary],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
            try:
                wait_for(lambda:(Path(temporary)/'lock.json').exists())
                actual_owner=json.loads((Path(temporary)/'lock.json').read_text())['owner']
                with self.assertRaises(LockBusy):ResourceLock(name).acquire()
                terminate_owned_identity(actual_owner)
                if process.poll() is None:process.wait(timeout=5)
                def acquire_after_exit():
                    try:return ResourceLock(name).acquire()
                    except LockBusy:return None
                acquired=wait_for(acquire_after_exit,seconds=3);acquired.close()
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)

    def test_caller_thread_exit_does_not_release_living_owner_lock(self):
        name=self.name();held=[]
        thread=threading.Thread(target=lambda:held.append(ResourceLock(name).acquire()))
        thread.start();thread.join(timeout=5);self.assertFalse(thread.is_alive())
        try:
            child=self.acquire_child(name)
            self.assertEqual(child.returncode,3,'Polling protection disappeared when the acquisition thread exited.')
        finally:
            for lock in held:lock.close()


@unittest.skipUnless(os.name=='nt','native Windows required')
class ContainmentTests(unittest.TestCase):
    def process(self,mode,**fields):
        boot={'contract_version':1,'run_id':uuid.uuid4().hex,'manifest':{'id':'isolated-native-fixture'},**fields}
        return ManagedProcess([sys.executable,str(FIXTURE),'--mode',mode],boot,cwd=FIXTURE.parent)

    def test_dedicated_control_remains_responsive_under_output_pressure(self):
        process=self.process('pressure')
        try:
            wait_for(lambda:any(e['kind']=='ready' for e in process.events))
            wait_for(lambda:len(process.output['stdout'])>=1024*1024 and len(process.output['stderr'])>=1024*1024)
            started=time.monotonic();process.request_stop();wait_for(lambda:process.poll() is not None,seconds=3)
            self.assertLess(time.monotonic()-started,3)
            self.assertTrue(all(len(value)<=1024*1024 for value in process.output.values()))
        finally:process.close()

    def test_worker_cannot_execute_before_job_assignment_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);original=Job.assign;checked=[]
            def inspect_suspended(job,process):
                # This marker precedes the worker's bootstrap read, so waiting
                # for bootstrap cannot conceal an erroneously runnable process.
                time.sleep(.5)
                self.assertFalse((directory/'entered.json').exists(),'Worker executed before assignment to its owning Job Object.')
                checked.append(True);original(job,process)
            boot={'contract_version':1,'run_id':uuid.uuid4().hex,'manifest':{'id':'suspended-fixture'}}
            with patch.object(Job,'assign',inspect_suspended):
                process=ManagedProcess([sys.executable,str(FIXTURE),'--mode','entry-marker','--directory',temporary],boot,cwd=directory)
            try:
                wait_for(lambda:(directory/'entered.json').exists())
                self.assertEqual(checked,[True]);process.request_stop();wait_for(lambda:process.poll() is not None)
            finally:process.close()

    def test_long_output_redacts_token_even_across_buffer_cut(self):
        secret=synthetic();process=self.process('leakstream',canary=secret)
        try:
            wait_for(lambda:any(e.get('fixture')=='output_complete' for e in process.events))
            process.request_stop();wait_for(lambda:process.poll() is not None)
        finally:process.close()
        self.assertFalse(secret.encode() in process.output['stdout'],'Token canary leaked across a long-output buffer cut.')

    def test_old_run_repeated_sequence_and_oversized_protocol_rejected(self):
        for mode in ('stale','sequence','malformed','missing-kind','wrong-contract','boolean-contract','invalid-kind','failure-fields','health-fields'):
            with self.subTest(mode=mode):
                process=self.process(mode)
                try:
                    def invalid_forwarded(event):
                        return (not isinstance(event.get('kind'),str) or type(event.get('contract_version')) is not int or event.get('contract_version')!=1 or
                            (event.get('kind')=='failure' and not isinstance(event.get('category'),str)) or
                            (event.get('kind')=='health' and not isinstance(event.get('connection','Unknown'),str)))
                    wait_for(lambda:process.protocol_error or not process.threads[0].is_alive() or any(invalid_forwarded(e) for e in process.events))
                    self.assertEqual(process.protocol_error,'Invalid or truncated worker protocol.')
                    process.request_stop();wait_for(lambda:process.poll() is not None)
                finally:process.close()

    def test_unrelated_inheritable_job_handle_excluded(self):
        job=Job();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.SetHandleInformation.argtypes=[w.HANDLE,w.DWORD,w.DWORD]
        self.assertTrue(kernel.SetHandleInformation(job.handle,1,1))
        process=self.process('inheritance',forbidden_job=int(job.handle))
        try:
            event=wait_for(lambda:next((e for e in process.events if e.get('fixture')=='inheritance'),None))
            self.assertFalse(event['forbidden_job_valid'],'A private unrelated Job Object handle was inherited by another worker.')
            process.request_stop();wait_for(lambda:process.poll() is not None)
        finally:process.close();job.close()

    def test_abrupt_lifetime_owner_loss_kills_child_and_grandchild(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
        kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT];kernel.CloseHandle.argtypes=[w.HANDLE]
        handles=[]
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            owner=subprocess.Popen([sys.executable,str(FIXTURE),'--mode','owner','--directory',str(directory)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=0x08000000)
            try:
                wait_for(lambda:(directory/'owner.json').exists())
                tree=json.loads((directory/'owner.json').read_text())
                for identity in (tree['child'],tree['grandchild']):
                    handle=kernel.OpenProcess(0x100001,False,identity);self.assertTrue(handle);handles.append(handle)
                terminate_owned_identity(tree['owner'])
                if owner.poll() is None:owner.wait(timeout=5)
                for handle in handles:self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'Owned descendant survived lifetime owner loss.')
            finally:
                if owner.poll() is None:owner.kill();owner.wait(timeout=5)
                for handle in handles:
                    if kernel.WaitForSingleObject(handle,0)==258:kernel.TerminateProcess(handle,1)
                    kernel.CloseHandle(handle)


if __name__=='__main__':unittest.main()
