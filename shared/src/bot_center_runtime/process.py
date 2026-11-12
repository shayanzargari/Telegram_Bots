"""Native suspended-create, Job Object containment, explicit handle inheritance."""
from __future__ import annotations
import ctypes,math,os,re,subprocess,threading,time
from ctypes import wintypes as w
from collections import deque
from .protocol import read_frame,write_frame,ProtocolError
from .redaction import redact

class BasicLimit(ctypes.Structure):
    _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',w.DWORD),('MinWorking',ctypes.c_size_t),('MaxWorking',ctypes.c_size_t),('Active',w.DWORD),('Affinity',ctypes.c_size_t),('Priority',w.DWORD),('Scheduling',w.DWORD)]
class IOCounters(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
class ExtendedLimit(ctypes.Structure):
    _fields_=[('Basic',BasicLimit),('IO',IOCounters),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]

class Job:
    def __init__(self):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[w.LPVOID,w.LPCWSTR];self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        limits=ExtendedLimit();limits.Basic.Flags=0x2000
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close();raise ctypes.WinError(ctypes.get_last_error())
    def assign(self,process):
        if not self.kernel.AssignProcessToJobObject(self.handle,int(process._handle)):raise ctypes.WinError(ctypes.get_last_error())
    def terminate(self):
        if self.handle:self.kernel.TerminateJobObject(self.handle,1)
    def close(self):
        if getattr(self,'handle',None):self.kernel.CloseHandle(self.handle);self.handle=None

class ManagedProcess:
    def __init__(self,args,bootstrap,cwd,environment=None,on_event=None,on_output=None):
        import msvcrt
        self.run_id=bootstrap['run_id'];self.started=time.monotonic();self.events=deque(maxlen=1024);self.output={'stdout':bytearray(),'stderr':bytearray()}
        self.on_event=on_event;self.on_output=on_output;self.protocol_error=None
        self.job=None;self.p=None;self.control=None;self.event_stream=None;self.threads=[]
        descriptors=set();bootstrap_stream=None
        try:
            self.job=Job()
            br,bw=os.pipe();descriptors.update((br,bw))
            cr,cw=os.pipe();descriptors.update((cr,cw))
            er,ew=os.pipe();descriptors.update((er,ew))
            child=[br,cr,ew]
            handles=[msvcrt.get_osfhandle(fd) for fd in child]
            for fd in child:os.set_inheritable(fd,True)
            env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('TELEGRAM_BOT_TOKEN',None)
            env.update(environment or {});env.update(BOT_CENTER_BOOTSTRAP=str(handles[0]),BOT_CENTER_CONTROL=str(handles[1]),BOT_CENTER_EVENTS=str(handles[2]),PYTHONIOENCODING='utf-8')
            startup=subprocess.STARTUPINFO();startup.lpAttributeList={'handle_list':handles}
            self.p=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,startupinfo=startup,creationflags=0x4|0x08000000)
            self.job.assign(self.p)
            resume=ctypes.WinDLL('ntdll').NtResumeProcess;resume.argtypes=[w.HANDLE];resume.restype=ctypes.c_long
            if resume(int(self.p._handle))!=0:raise RuntimeError('Could not resume contained worker.')
            for fd in child:
                os.close(fd);descriptors.remove(fd)
            self.control=os.fdopen(cw,'wb',buffering=0);descriptors.remove(cw)
            self.event_stream=os.fdopen(er,'rb',buffering=0);descriptors.remove(er)
            bootstrap_stream=os.fdopen(bw,'wb',buffering=0);descriptors.remove(bw)
            write_frame(bootstrap_stream,bootstrap)
            bootstrap_stream.close();bootstrap_stream=None
            self.threads=[threading.Thread(target=self._events,daemon=True)]
            for name in ('stdout','stderr'):self.threads.append(threading.Thread(target=self._drain,args=(name,),daemon=True))
            for thread in self.threads:thread.start()
        except BaseException:
            # A failed bootstrap or thread start is still an owned process tree.
            # Close the Job even if termination fails, including when the parent
            # has exited but a descendant still owns inherited channels/locks.
            if self.job:
                try:self.job.terminate()
                except OSError:pass
                finally:self.job.close()
            if self.p:
                try:
                    if self.p.poll() is None:self.p.terminate()
                    self.p.wait(timeout=10)
                except (OSError,subprocess.TimeoutExpired):pass
            for stream in (bootstrap_stream,self.control):
                if stream:
                    try:stream.close()
                    except OSError:pass
            for thread in self.threads:
                if thread.ident is not None:thread.join(timeout=2)
            for stream in (self.event_stream,getattr(self.p,'stdout',None),getattr(self.p,'stderr',None)):
                if stream:
                    try:stream.close()
                    except OSError:pass
            for fd in descriptors:
                try:os.close(fd)
                except OSError:pass
            handle=getattr(self.p,'_handle',None)
            if handle:handle.Close()
            raise
    @property
    def pid(self):return self.p.pid
    def poll(self):return self.p.poll()
    def _events(self):
        last_seq=0
        try:
            while True:
                event=read_frame(self.event_stream)
                if event is None:break
                kind=event.get('kind')
                if not isinstance(kind,str):raise ProtocolError('Invalid current-run protocol identity/schema.')
                valid=(type(event.get('contract_version')) is int and event['contract_version']==1 and event.get('run_id')==self.run_id and type(event.get('seq')) is int and event['seq']>last_seq and
                    isinstance(kind,str) and kind in {'ready','health','telemetry','failure','progress','completed','connection'})
                if kind=='failure':valid=valid and isinstance(event.get('category'),str) and isinstance(event.get('message'),str)
                if kind in {'health','telemetry','progress'}:
                    valid=valid and isinstance(event.get('connection','Unknown'),str)
                    for field in ('progress','last_poll'):
                        value=event.get(field)
                        if value is not None:valid=valid and type(value) in (int,float) and math.isfinite(value)
                if kind=='connection':valid=valid and isinstance(event.get('state'),str)
                if not valid:raise ProtocolError('Invalid current-run protocol identity/schema.')
                last_seq=event['seq'];self.events.append(event)
                if self.on_event:self.on_event(event)
        except (OSError,ProtocolError,TypeError,ValueError,OverflowError):self.protocol_error='Invalid or truncated worker protocol.'
    def _drain(self,name):
        stream=getattr(self.p,name);pending=bytearray()
        try:
            while chunk:=stream.read(8192):
                pending.extend(chunk)
                while b'\n' in pending or len(pending)>65536:
                    cut=pending.find(b'\n')+1
                    if not cut:cut=len(pending)-512
                    for match in re.finditer(rb'\d{5,}:[A-Za-z0-9_-]*',pending):
                        if match.start()<cut<match.end():cut=match.start();break
                    if cut==0:
                        self._output(name,bytes(pending));pending.clear();break
                    self._output(name,bytes(pending[:cut]));del pending[:cut]
        finally:
            if pending:self._output(name,bytes(pending))
            stream.close()
    def _output(self,name,chunk):
        safe=redact(chunk.decode('utf-8','replace')).encode()
        self.output[name].extend(safe)
        if len(self.output[name])>1024*1024:del self.output[name][:-1024*1024]
        if self.on_output:self.on_output(name,safe.decode())
    def request_stop(self):
        try:write_frame(self.control,{'action':'stop','run_id':self.run_id})
        except (OSError,ValueError):pass
    def terminate(self):self.job.terminate()
    def close(self):
        if self.poll() is None:self.terminate()
        try:self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:pass
        self.job.close();self.control.close()
        for thread in self.threads:thread.join(timeout=2)
        self.event_stream.close()
