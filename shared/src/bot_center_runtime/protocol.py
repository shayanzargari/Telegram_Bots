"""Version 1 dedicated bounded protocol; stdout/stderr are never parsed."""
from __future__ import annotations
import json,os,struct,threading,time

MAX_FRAME=65536
class ProtocolError(RuntimeError):pass
def read_frame(stream):
    header=b''
    while len(header)<4:
        chunk=stream.read(4-len(header))
        if not chunk:
            if not header:return None
            raise ProtocolError('Truncated protocol frame.')
        header+=chunk
    size=struct.unpack('<I',header)[0]
    if size>MAX_FRAME:raise ProtocolError('Protocol frame exceeds limit.')
    body=bytearray()
    while len(body)<size:
        chunk=stream.read(size-len(body))
        if not chunk:raise ProtocolError('Truncated protocol frame.')
        body.extend(chunk)
    try:
        data=json.loads(body)
        if not isinstance(data,dict):raise ValueError()
        return data
    except (ValueError,UnicodeError,RecursionError):raise ProtocolError('Malformed protocol frame.') from None
def write_frame(stream,body):
    raw=json.dumps(body,separators=(',',':'),ensure_ascii=False).encode()
    if len(raw)>MAX_FRAME:raise ProtocolError('Protocol frame exceeds limit.')
    data=struct.pack('<I',len(raw))+raw;offset=0
    while offset<len(data):
        count=stream.write(data[offset:])
        if not count:raise ProtocolError('Protocol write did not complete.')
        offset+=count
    stream.flush()

def inherited_stream(variable,mode):
    import msvcrt
    handle=int(os.environ.pop(variable))
    descriptor=msvcrt.open_osfhandle(handle,os.O_BINARY|(os.O_RDONLY if mode=='rb' else os.O_WRONLY))
    return os.fdopen(descriptor,mode,buffering=0)

class WorkerClient:
    def __init__(self,bootstrap,control,events):
        self.bootstrap=bootstrap;self.control=control;self.events=events;self.stop=threading.Event()
        self.lock=threading.Lock();self.sequence=0;self.last_progress=time.monotonic();self.last_poll=None;self.connection='Unknown';self.closed=False
        self.run_id=bootstrap['run_id'];self.manifest=bootstrap['manifest']
        threading.Thread(target=self._control,daemon=True).start()
        threading.Thread(target=self._telemetry,daemon=True).start()
    @classmethod
    def from_environment(cls):
        with inherited_stream('BOT_CENTER_BOOTSTRAP','rb') as stream:bootstrap=read_frame(stream)
        if not bootstrap or bootstrap.get('contract_version')!=1:raise ProtocolError('Unsupported bootstrap contract.')
        return cls(bootstrap,inherited_stream('BOT_CENTER_CONTROL','rb'),inherited_stream('BOT_CENTER_EVENTS','wb'))
    def emit(self,kind,**fields):
        if self.closed:return
        with self.lock:
            self.sequence+=1
            try:write_frame(self.events,{'contract_version':1,'run_id':self.run_id,'seq':self.sequence,'kind':kind,**fields})
            except (OSError,ValueError):self.stop.set()
    def progress(self,poll_ok=False,**fields):
        self.last_progress=time.monotonic()
        if poll_ok:self.last_poll=self.last_progress;self.connection='Connected'
        if 'connection' in fields:self.connection=fields.pop('connection')
        self.emit('health',progress=self.last_progress,last_poll=self.last_poll,connection=self.connection,**fields)
    def ready(self):self.progress();self.emit('ready')
    def _control(self):
        try:
            while not self.closed:
                frame=read_frame(self.control)
                if frame is None:break
                if frame.get('action') in ('stop','cancel'):self.stop.set();self.progress();break
        except (OSError,ProtocolError):pass
        finally:self.stop.set()
    def _telemetry(self):
        while not self.stop.wait(5):
            self.emit('telemetry',progress=self.last_progress,last_poll=self.last_poll,connection=self.connection)
    def close(self):
        if self.closed:return
        self.emit('completed');self.closed=True;self.stop.set()
        self.events.close()
