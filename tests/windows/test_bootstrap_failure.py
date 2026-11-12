"""Native construction failures leave no worker tree, lock or open channel."""
from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from bot_center_runtime import process as runtime
from bot_center_runtime.locks import LockBusy,ResourceLock
from bot_center_runtime.protocol import ProtocolError,inherited_stream


def fixture_worker():
    """Deliberately own a descendant and lock before accepting bootstrap."""
    if os.environ.get('FIXTURE_CLOSE_BOOTSTRAP')=='1':
        inherited_stream('BOT_CENTER_BOOTSTRAP','rb').close()
    with ResourceLock(os.environ['FIXTURE_BOOTSTRAP_LOCK']):
        descendant=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(120)'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            close_fds=True,creationflags=0x08000000)
        path=Path(os.environ['FIXTURE_BOOTSTRAP_DIRECTORY'])/'tree.json'
        pending=path.with_suffix('.tmp')
        pending.write_text(json.dumps({'worker':os.getpid(),'descendant':descendant.pid}),'utf-8')
        pending.replace(path)
        time.sleep(120)


@unittest.skipUnless(os.name=='nt','native Windows required')
class BootstrapFailureTests(unittest.TestCase):
    def assert_failed_tree_cleaned(self,failure):
        import msvcrt
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
        kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT];kernel.CloseHandle.argtypes=[w.HANDLE]
        kernel.DuplicateHandle.argtypes=[w.HANDLE,w.HANDLE,w.HANDLE,ctypes.POINTER(w.HANDLE),w.DWORD,w.BOOL,w.DWORD]
        kernel.DuplicateHandle.restype=w.BOOL
        handles=[];pipes=[];bootstrap_streams=[];name='bootstrap-failure-fixture-'+uuid.uuid4().hex
        instance=runtime.ManagedProcess.__new__(runtime.ManagedProcess)
        original_pipe=os.pipe;original_write=runtime.write_frame;original_start=threading.Thread.start
        starts=0;bootstrap_read_handle=None
        with tempfile.TemporaryDirectory(prefix='center-bootstrap-failure-') as directory:
            def pipe():
                nonlocal bootstrap_read_handle
                result=original_pipe()
                if bootstrap_read_handle is None:bootstrap_read_handle=msvcrt.get_osfhandle(result[0])
                pipes.extend(result);return result

            def write_bootstrap(stream,bootstrap):
                bootstrap_streams.append(stream)
                deadline=time.monotonic()+8
                record=Path(directory)/'tree.json'
                while not record.is_file() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(record.is_file(),'Fixture did not own its tree before bootstrap injection.')
                identities=json.loads(record.read_text('utf-8'))
                for pid in identities.values():
                    handle=kernel.OpenProcess(0x100001,False,pid)
                    self.assertTrue(handle,'Could not retain the fixture creation identity.')
                    handles.append(handle)
                    self.assertEqual(kernel.WaitForSingleObject(handle,0),258)
                with self.assertRaises(LockBusy):ResourceLock(name).acquire()
                if failure=='pipe' and instance.p.pid!=identities['worker']:
                    # Windows venv python.exe is a redirector which retains its
                    # inherited bootstrap reader while the actual worker runs.
                    # Close that exact fixture handle as well, preserving the
                    # process tree and polling lock until the write fails.
                    duplicate=w.HANDLE()
                    self.assertTrue(kernel.DuplicateHandle(int(instance.p._handle),bootstrap_read_handle,
                        w.HANDLE(-1),ctypes.byref(duplicate),0,False,3),
                        'Could not close the fixture redirector bootstrap duplicate.')
                    kernel.CloseHandle(duplicate)
                return original_write(stream,bootstrap)

            def start_thread(thread):
                nonlocal starts
                starts+=1
                if failure=='thread' and starts==2:raise RuntimeError('fixture thread-start failure')
                return original_start(thread)

            bootstrap={'contract_version':1,'run_id':uuid.uuid4().hex,'manifest':{'id':'isolated-bootstrap-failure'}}
            if failure=='oversized':bootstrap['padding']='x'*65536
            environment={'FIXTURE_BOOTSTRAP_LOCK':name,'FIXTURE_BOOTSTRAP_DIRECTORY':directory,
                         'FIXTURE_CLOSE_BOOTSTRAP':'1' if failure=='pipe' else '0'}
            expected={'oversized':ProtocolError,'pipe':OSError,'thread':RuntimeError}[failure]
            started=time.monotonic()
            try:
                with patch.object(runtime.os,'pipe',side_effect=pipe),patch.object(runtime,'write_frame',side_effect=write_bootstrap),patch.object(runtime.threading.Thread,'start',start_thread):
                    with self.assertRaises(expected):
                        instance.__init__([sys.executable,str(Path(__file__).resolve()),'--bootstrap-failure-worker'],
                            bootstrap,cwd=directory,environment=environment)
                self.assertLess(time.monotonic()-started,12,'Failed construction did not clean up promptly.')
                self.assertIsNotNone(instance.p.poll())
                self.assertIsNone(instance.job.handle)
                for handle in handles:self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'A fixture process survived failed bootstrap.')
                with ResourceLock(name):pass
                for stream in [*bootstrap_streams,instance.control,instance.event_stream,instance.p.stdout,instance.p.stderr]:
                    self.assertTrue(stream.closed,'Failed construction left an open pipe or diagnostic stream.')
                for descriptor in pipes:
                    with self.assertRaises(OSError):os.fstat(descriptor)
                self.assertFalse(any(thread.is_alive() for thread in instance.threads),'Failed construction left a reader thread running.')
            finally:
                # Retain exact creation handles so a regression cleans only its
                # own fixture processes, even when no object was returned.
                job=getattr(instance,'job',None)
                if job:job.terminate();job.close()
                for handle in handles:
                    if kernel.WaitForSingleObject(handle,0)==258:kernel.TerminateProcess(handle,1)
                    kernel.WaitForSingleObject(handle,5000);kernel.CloseHandle(handle)
                process=getattr(instance,'p',None)
                if process and process.poll() is None:process.terminate();process.wait(timeout=5)
                for stream in [*bootstrap_streams,getattr(instance,'control',None),getattr(instance,'event_stream',None),getattr(process,'stdout',None),getattr(process,'stderr',None)]:
                    if stream and not stream.closed:stream.close()

    def test_oversized_bootstrap_terminates_tree_and_releases_lock(self):
        self.assert_failed_tree_cleaned('oversized')

    def test_broken_bootstrap_pipe_terminates_tree_and_releases_lock(self):
        self.assert_failed_tree_cleaned('pipe')

    def test_partial_thread_start_terminates_tree_and_closes_channels(self):
        self.assert_failed_tree_cleaned('thread')

    def test_partial_pipe_setup_closes_created_descriptors_and_job(self):
        instance=runtime.ManagedProcess.__new__(runtime.ManagedProcess)
        original_pipe=os.pipe;created=[]
        def pipe():
            if created:raise OSError('fixture pipe creation failure')
            result=original_pipe();created.extend(result);return result
        with patch.object(runtime.os,'pipe',side_effect=pipe):
            with self.assertRaisesRegex(OSError,'fixture pipe creation failure'):
                instance.__init__([sys.executable,'-c','pass'],{'run_id':uuid.uuid4().hex},cwd=Path.cwd())
        self.assertIsNone(instance.p);self.assertIsNone(instance.job.handle)
        for descriptor in created:
            with self.assertRaises(OSError):os.fstat(descriptor)


if __name__=='__main__':
    if '--bootstrap-failure-worker' in sys.argv:fixture_worker()
    else:unittest.main()
