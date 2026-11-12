"""Current-user machine-wide kernel locks, independent of aliases and paths."""
from __future__ import annotations
import ctypes,hashlib,os
from functools import lru_cache
from pathlib import Path
from ctypes import wintypes

class LockBusy(RuntimeError):pass

@lru_cache(maxsize=1)
def user_sid():
    import subprocess
    text=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,creationflags=0x08000000)
    return text.strip().split(',')[-1].strip('"')

class ResourceLock:
    def __init__(self,name):self.name=name;self.handle=None
    def acquire(self):
        if os.name!='nt':raise RuntimeError('This installation requires native Windows resource protection.')
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        directory=Path(os.environ['LOCALAPPDATA'])/'Bot Control Center Locks'/user_sid()
        if not directory.exists():
            from .credentials import private_directory
            private_directory(directory)
        path=directory/(hashlib.sha256(self.name.encode()).hexdigest()+'.lock')
        self.handle=kernel.CreateFileW(str(path),0x80000000|0x40000000,0,None,4,0x80,None)
        if self.handle in (None,ctypes.c_void_p(-1).value):
            error=ctypes.get_last_error();self.handle=None
            if error in (32,33):raise LockBusy('Resource is already owned by another instance.')
            raise ctypes.WinError(error)
        self.kernel=kernel;return self
    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self.acquire()
    def __exit__(self,*args):self.close()

class FileLease:
    """Cross-process shared ordinary access or exclusive maintenance reservation."""
    def __init__(self,path,exclusive=False):self.path=path;self.exclusive=exclusive;self.stream=None
    def __enter__(self):
        import msvcrt
        from pathlib import Path
        path=Path(self.path);path.parent.mkdir(parents=True,exist_ok=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        handle=kernel.CreateFileW(str(path),0x80000000|0x40000000,0 if self.exclusive else 3,None,4,0x80,None)
        if handle in (None,ctypes.c_void_p(-1).value):raise LockBusy('Maintenance reservation conflicts with an active writer.')
        self.stream=os.fdopen(msvcrt.open_osfhandle(handle,os.O_RDWR),'r+b');return self
    def __exit__(self,*args):
        if self.stream:self.stream.close()
