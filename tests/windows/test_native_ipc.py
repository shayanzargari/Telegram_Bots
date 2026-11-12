from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest

from bot_center_runtime import ipc
from bot_center_runtime.ipc import PipeServer,PipeStream,IPCError,request,process_creation
from bot_center_runtime.protocol import read_frame,write_frame
from bot_center_runtime.locks import user_sid
ipc.K.WaitNamedPipeW.argtypes=[w.LPCWSTR,w.DWORD]


def wait_for(predicate,seconds=4):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.01)
    raise AssertionError('Owned IPC fixture did not become available.')


def connect(name):
    def attempt():
        handle=ipc.K.CreateFileW(name,0x80000000|0x40000000,0,None,3,0,None)
        return handle if handle!=ctypes.c_void_p(-1).value else None
    return wait_for(attempt)


class OwnedPipe:
    """Unrelated/stalled peer created only inside this test's isolated namespace."""
    def __init__(self,root,mode):
        self.root=Path(root);self.mode=mode;self.stop=threading.Event();self.connected=threading.Event();self.errors=[]
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        handle=None
        try:
            handle=ipc.K.CreateNamedPipeW(ipc.pipe_name(self.root),3|0x00080000,0x8,1,65536,65536,1000,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            ipc.K.ConnectNamedPipe(handle,None)
            with PipeStream(handle,timeout=3) as stream:
                handle=None;body=read_frame(stream);self.connected.set()
                if self.mode=='stall':self.stop.wait(2)
                elif self.mode=='fragment':
                    # Return a legal frame one byte at a time. The framing reader
                    # must not confuse a fragmented header with EOF.
                    import struct
                    packet=json.dumps({'value':42}).encode();framed=struct.pack('<I',len(packet))+packet
                    for octet in framed:stream.write(bytes([octet]));time.sleep(.005)
                    self.stop.wait(.5)
                else:
                    forged={'nonce':body['nonce'],'installation':str(self.root).casefold(),'instance':'unrelated',
                        'pid':os.getpid(),'creation':process_creation(os.getpid()),'body':{'ready':True,'ticket':'untrusted-fixture-ticket','origin':'http://127.0.0.1:9999'},'mac':'0'*64}
                    write_frame(stream,forged);self.stop.wait(.5)
        except BaseException as error:self.errors.append(type(error).__name__)
        finally:
            if handle and handle!=ctypes.c_void_p(-1).value:ipc.K.CloseHandle(handle)
    def close(self):
        self.stop.set()
        if not self.connected.is_set():
            try:ipc.K.CloseHandle(connect(ipc.pipe_name(self.root)))
            except AssertionError:pass
        self.thread.join(timeout=3)


@unittest.skipUnless(os.name=='nt','native Windows required')
class PipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name);self.effects=[]
        self.server=None
    def tearDown(self):
        if self.server:self.server.close()
        self.temporary.cleanup()
    def start(self):
        def handler(body):self.effects.append(body);return {'ready':True,'accepted':body.get('action')}
        self.server=PipeServer(self.root,'native-test-instance',handler)
        ipc.K.WaitNamedPipeW.argtypes=[w.LPCWSTR,w.DWORD]
        wait_for(lambda:ipc.K.WaitNamedPipeW(self.server.name,50))
    def frame(self,**fields):
        result={'installation':str(self.root).casefold(),'time':time.time(),'nonce':secrets.token_hex(24),'body':{'action':'identify'},**fields}
        return {**result,'mac':ipc._sign(result,ipc._key(self.root))}
    def raw(self,frame):
        with PipeStream(connect(ipc.pipe_name(self.root)),timeout=.8) as stream:
            write_frame(stream,frame);return read_frame(stream)

    def test_authenticated_server_is_bound_to_instance_pid_and_creation(self):
        self.start()
        result=request(self.root,{'action':'identify'})
        self.assertEqual(result['instance'],'native-test-instance')
        self.assertEqual(result['pid'],os.getpid());self.assertEqual(result['creation'],process_creation(os.getpid()))
        self.assertEqual(result['body']['accepted'],'identify')

    def test_one_hundred_authenticated_roundtrips_do_not_lose_buffered_replies(self):
        self.start()
        for index in range(100):
            with self.subTest(index=index):
                result=request(self.root,{'action':'identify','sequence':index})
                self.assertEqual(result['instance'],'native-test-instance')
                self.assertEqual(result['body']['accepted'],'identify')
        self.assertEqual(len(self.effects),100)

    def test_wrong_mac_stale_time_wrong_installation_and_malformed_signature_have_no_effect(self):
        self.start()
        cases=[{**self.frame(),'mac':'wrong'},{**self.frame(),'mac':42},self.frame(time=time.time()-120),self.frame(installation='another-installation')]
        for frame in cases:
            with self.subTest(kind=type(frame['mac']).__name__):self.assertIsNone(self.raw(frame))
        self.assertEqual(self.effects,[])

    def test_authenticated_nonce_replay_is_rejected_without_duplicate_effect(self):
        self.start();frame=self.frame()
        first=self.raw(frame);self.assertEqual(first['body']['accepted'],'identify')
        self.assertIsNone(self.raw(frame));self.assertEqual(len(self.effects),1)

    def test_stale_discovery_expectation_yields_no_authenticated_handoff(self):
        self.start()
        with self.assertRaises(IPCError):request(self.root,{'action':'identify'},expected={'instance':'stale-instance','pid':os.getpid(),'creation':process_creation(os.getpid())})

    def test_unrelated_peer_cannot_forge_browser_handoff(self):
        ipc._key(self.root,True);peer=OwnedPipe(self.root,'forged')
        try:
            wait_for(lambda:ipc.K.WaitNamedPipeW(ipc.pipe_name(self.root),50))
            with self.assertRaises(IPCError):request(self.root,{'action':'dashboard'},timeout=.5)
            self.assertTrue(peer.thread.is_alive(),'The fixture should remain alive; rejecting a peer must not terminate it.')
        finally:peer.close()

    def test_stalled_unrelated_peer_has_bounded_handshake(self):
        ipc._key(self.root,True);peer=OwnedPipe(self.root,'stall')
        try:
            wait_for(lambda:ipc.K.WaitNamedPipeW(ipc.pipe_name(self.root),50))
            started=time.monotonic()
            with self.assertRaises(IPCError):request(self.root,{'action':'dashboard'},timeout=.2)
            self.assertLess(time.monotonic()-started,1.5)
        finally:peer.close()

    def test_fragmented_pipe_frame_header_is_not_a_truncated_valid_message(self):
        ipc._key(self.root,True);peer=OwnedPipe(self.root,'fragment')
        try:
            with PipeStream(connect(ipc.pipe_name(self.root)),timeout=1) as stream:
                write_frame(stream,{'fixture':True})
                self.assertEqual(read_frame(stream),{'value':42})
        finally:peer.close()

    def test_private_pipe_dacl_denies_a_restricted_token(self):
        self.start()
        advapi=ctypes.WinDLL('advapi32',use_last_error=True)
        advapi.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
        advapi.CreateRestrictedToken.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.LPVOID,w.DWORD,w.LPVOID,ctypes.POINTER(w.HANDLE)]
        advapi.ImpersonateLoggedOnUser.argtypes=[w.HANDLE];advapi.RevertToSelf.argtypes=[]
        advapi.ConvertStringSidToSidW.argtypes=[w.LPCWSTR,ctypes.POINTER(w.LPVOID)]
        class SID_AND_ATTRIBUTES(ctypes.Structure):_fields_=[('Sid',w.LPVOID),('Attributes',w.DWORD)]
        token=w.HANDLE();restricted=w.HANDLE();sid=w.LPVOID()
        self.assertTrue(advapi.OpenProcessToken(w.HANDLE(-1),0xE,ctypes.byref(token)))
        try:
            self.assertTrue(advapi.ConvertStringSidToSidW('S-1-1-0',ctypes.byref(sid)))
            restricting=SID_AND_ATTRIBUTES(sid,0)
            self.assertTrue(advapi.CreateRestrictedToken(token,1,0,None,0,None,1,ctypes.byref(restricting),ctypes.byref(restricted)),
                'CreateRestrictedToken failed with Win32 error '+str(ctypes.get_last_error()))
            self.assertTrue(advapi.ImpersonateLoggedOnUser(restricted))
            try:
                handle=ipc.K.CreateFileW(self.server.name,0x80000000|0x40000000,0,None,3,0,None)
                error=ctypes.get_last_error()
            finally:advapi.RevertToSelf()
            if handle!=ctypes.c_void_p(-1).value:ipc.K.CloseHandle(handle)
            self.assertEqual(handle,ctypes.c_void_p(-1).value,'Restricted-token client unexpectedly opened the private pipe.')
            self.assertEqual(error,5,'The private DACL should deny the restricted client.')
        finally:
            if restricted:ipc.K.CloseHandle(restricted)
            if token:ipc.K.CloseHandle(token)
            if sid:
                ipc.K.LocalFree.argtypes=[w.HLOCAL];ipc.K.LocalFree(sid)

    def test_unrelated_http_listener_receives_no_ticket_and_is_not_terminated(self):
        from bot_center.launcher import launch
        config=self.root/'config';config.mkdir()
        (config/'installation.json').write_text('{"schema":1,"generation":"isolated-fixture"}',encoding='utf-8')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1)
            port=listener.getsockname()[1]
            (config/'settings.json').write_text(json.dumps({'port':port}),encoding='utf-8')
            # A stale discovery file is untrusted and carries no reusable secret.
            (self.root/'data/core').mkdir(parents=True)
            (self.root/'data/core/discovery.json').write_text(json.dumps({'pid':os.getpid(),'origin':f'http://127.0.0.1:{port}','instance':'stale'}),encoding='utf-8')
            with self.assertRaises(RuntimeError) as caught:launch(self.root,open_browser=False)
            self.assertIn('occupied',str(caught.exception))
            with socket.create_connection(('127.0.0.1',port),timeout=1) as client:
                accepted,_=listener.accept();accepted.settimeout(.1)
                try:
                    with self.assertRaises(socket.timeout):accepted.recv(1024)
                finally:accepted.close()

    def test_launcher_ticket_is_single_use_and_new_core_invalidates_sessions(self):
        from bot_center.security import Sessions
        sessions=Sessions();ticket=sessions.ticket();identity,csrf=sessions.exchange(ticket)
        self.assertTrue(sessions.session(identity));self.assertIsNone(sessions.exchange(ticket))
        restarted=Sessions();self.assertIsNone(restarted.session(identity));self.assertIsNone(restarted.exchange(ticket))


if __name__=='__main__':unittest.main()
