"""Private local management pipe with instance/process-bound authenticated replies."""
from __future__ import annotations
import ctypes,hashlib,hmac,json,os,secrets,struct,threading,time
from pathlib import Path
from ctypes import wintypes as w
from .credentials import protect,private_directory
from .locks import user_sid
from .protocol import read_frame,write_frame,ProtocolError

class IPCError(RuntimeError):pass
K=ctypes.WinDLL('kernel32',use_last_error=True)
K.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];K.CreateFileW.restype=w.HANDLE
K.CreateNamedPipeW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.LPVOID];K.CreateNamedPipeW.restype=w.HANDLE
K.ConnectNamedPipe.argtypes=[w.HANDLE,w.LPVOID];K.CloseHandle.argtypes=[w.HANDLE]
K.GetNamedPipeServerProcessId.argtypes=[w.HANDLE,ctypes.POINTER(w.ULONG)]
K.GetNamedPipeClientProcessId.argtypes=[w.HANDLE,ctypes.POINTER(w.ULONG)]
K.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];K.OpenProcess.restype=w.HANDLE
K.GetProcessTimes.argtypes=[w.HANDLE,ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME)]
K.SetNamedPipeHandleState.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD),w.LPVOID,w.LPVOID]
K.PeekNamedPipe.argtypes=[w.HANDLE,w.LPVOID,w.DWORD,w.LPVOID,ctypes.POINTER(w.DWORD),w.LPVOID]
K.ReadFile.argtypes=[w.HANDLE,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID]
K.WriteFile.argtypes=[w.HANDLE,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD),w.LPVOID]
class SecurityAttributes(ctypes.Structure):_fields_=[('length',w.DWORD),('descriptor',w.LPVOID),('inherit',w.BOOL)]

def pipe_name(root):return '\\\\.\\pipe\\BotCenter-'+hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:32]
def process_creation(pid):
    handle=K.OpenProcess(0x1000,False,pid)
    if not handle:raise IPCError('Cannot verify management process identity.')
    values=[w.FILETIME() for _ in range(4)]
    try:
        if not K.GetProcessTimes(handle,*[ctypes.byref(x) for x in values]):raise IPCError('Cannot verify process creation identity.')
        return (values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
    finally:K.CloseHandle(handle)
def _key(root,create=False):
    target=Path(root)/'.secrets/management.dpapi'
    if not target.exists():
        if not create:raise IPCError('Management authentication is not initialized.')
        private_directory(target.parent);target.write_bytes(protect(secrets.token_bytes(32)))
    return protect(target.read_bytes(),True)
def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def _sign(value,key):return hmac.new(key,_canonical(value),hashlib.sha256).hexdigest()
class PipeStream:
    def __init__(self,handle,timeout=5):
        self.handle=handle;self.deadline=time.monotonic()+timeout
        mode=w.DWORD(1)
        if not K.SetNamedPipeHandleState(handle,ctypes.byref(mode),None,None):self.close();raise IPCError('Cannot establish bounded local pipe exchange.')
    def _wait(self):
        if time.monotonic()>=self.deadline:raise IPCError('Management handshake timed out; no browser credentials were released.')
        time.sleep(.01)
    def read(self,size):
        while True:
            available=w.DWORD()
            if not K.PeekNamedPipe(self.handle,None,0,None,ctypes.byref(available),None):return b''
            if available.value:
                data=ctypes.create_string_buffer(min(size,available.value));count=w.DWORD()
                if not K.ReadFile(self.handle,data,len(data),ctypes.byref(count),None):return b''
                return data.raw[:count.value]
            self._wait()
    def write(self,data):
        offset=0
        while offset<len(data):
            chunk=ctypes.create_string_buffer(data[offset:offset+8192]);count=w.DWORD()
            if not K.WriteFile(self.handle,chunk,len(chunk)-1,ctypes.byref(count),None):
                if ctypes.get_last_error() not in (232,536):raise IPCError('Local management pipe closed.')
            if count.value:offset+=count.value
            else:self._wait()
        return offset
    def flush(self):pass
    def close(self):
        if self.handle:K.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
def _stream(handle):return PipeStream(handle,timeout=180)

class PipeServer:
    def __init__(self,root,instance,handler):
        self.root=Path(root);self.instance=instance;self.handler=handler;self.key=_key(root,True);self.name=pipe_name(root);self.stop=threading.Event();self.seen={};self.lock=threading.Lock();self.error=None
        descriptor=w.LPVOID()
        convert=ctypes.WinDLL('advapi32',use_last_error=True).ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(w.LPVOID),ctypes.POINTER(w.ULONG)]
        if not convert('D:P(A;;GA;;;'+user_sid()+')',1,ctypes.byref(descriptor),None):raise ctypes.WinError(ctypes.get_last_error())
        self.descriptor=descriptor;self.attributes=SecurityAttributes(ctypes.sizeof(SecurityAttributes),descriptor,False)
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def _run(self):
        try:
            first=True
            while not self.stop.is_set():
                handle=K.CreateNamedPipeW(self.name,3|(0x00080000 if first else 0),0x8,8,65536,65536,1000,ctypes.byref(self.attributes))
                if handle==ctypes.c_void_p(-1).value:raise IPCError('Could not create private management pipe.')
                first=False
                connected=K.ConnectNamedPipe(handle,None)
                if not connected and ctypes.get_last_error()!=535:K.CloseHandle(handle);continue
                if self.stop.is_set():K.CloseHandle(handle);break
                threading.Thread(target=self._handle,args=(handle,),daemon=True).start()
        except BaseException:self.error='Management pipe listener failed.'
    def _handle(self,handle):
        try:
            client_pid=w.ULONG()
            if not K.GetNamedPipeClientProcessId(handle,ctypes.byref(client_pid)):raise IPCError('Cannot verify local pipe client.')
            with _stream(handle) as stream:
                request=read_frame(stream)
                if request is None:return
                signature=request.pop('mac','')
                if not hmac.compare_digest(signature,_sign(request,self.key)):raise IPCError('Unauthorized management request.')
                if request.get('installation')!=str(self.root).casefold() or abs(time.time()-request.get('time',0))>60:raise IPCError('Stale or mismatched management request.')
                with self.lock:
                    self.seen={n:t for n,t in self.seen.items() if time.time()-t<60}
                    if request.get('nonce') in self.seen:raise IPCError('Management request replay rejected.')
                    self.seen[request['nonce']]=time.time()
                body=self.handler(request['body'])
                response={'nonce':request['nonce'],'installation':str(self.root).casefold(),'instance':self.instance,'pid':os.getpid(),'creation':process_creation(os.getpid()),'body':body}
                write_frame(stream,{**response,'mac':_sign(response,self.key)})
                # Keep the server end alive until the client consumes the reply
                # and closes. Closing immediately can discard unread pipe data.
                stream.deadline=time.monotonic()+5
                read_frame(stream)
        except (OSError,ValueError,KeyError,TypeError,IPCError,ProtocolError):pass
    def close(self):
        self.stop.set()
        handle=K.CreateFileW(self.name,0x80000000|0x40000000,0,None,3,0,None)
        if handle!=ctypes.c_void_p(-1).value:K.CloseHandle(handle)
        self.thread.join(timeout=2)
        if not self.thread.is_alive():
            K.LocalFree.argtypes=[w.HLOCAL];K.LocalFree(self.descriptor)

def request(root,body,expected=None,timeout=5):
    root=Path(root).resolve();key=_key(root);name=pipe_name(root)
    handle=K.CreateFileW(name,0x80000000|0x40000000,0,None,3,0,None)
    if handle==ctypes.c_void_p(-1).value:raise IPCError('Authenticated core is unavailable. Start the core explicitly.')
    pid=w.ULONG()
    if not K.GetNamedPipeServerProcessId(handle,ctypes.byref(pid)):K.CloseHandle(handle);raise IPCError('Cannot verify management server identity.')
    creation=process_creation(pid.value)
    frame={'installation':str(root).casefold(),'time':time.time(),'nonce':secrets.token_hex(24),'body':body}
    with PipeStream(handle,timeout=timeout) as stream:
        write_frame(stream,{**frame,'mac':_sign(frame,key)});response=read_frame(stream)
    if response is None:raise IPCError('Management authentication failed.')
    mac=response.pop('mac','')
    if not hmac.compare_digest(mac,_sign(response,key)) or response.get('nonce')!=frame['nonce'] or response.get('installation')!=str(root).casefold() or response.get('pid')!=pid.value or response.get('creation')!=creation:
        raise IPCError('Management server identity mismatch; no browser credentials were released.')
    if expected and any(response.get(k)!=expected.get(k) for k in ('instance','pid','creation')):raise IPCError('Stale management discovery; reconnect through the launcher.')
    return response
