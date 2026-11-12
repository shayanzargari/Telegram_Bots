"""Exercise the actual batch via ShellExecuteEx (Explorer's launch mechanism)."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import argparse

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work-dir',required=True);parser.add_argument('--root');args=parser.parse_args()
    from bot_center_runtime.paths import Installation
    installation=Installation(args.root)
    work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    notes=work/'launcher-notes';notes.mkdir(exist_ok=True)
    (notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
    state=work/'shell-launch-state';state.mkdir(exist_ok=True)
    ready=state/'offline-ready.json'
    if ready.exists():ready.unlink()
    nonce=secrets.token_hex(16)
    os.environ['FRENCH_BOT_HARNESS_NONCE']=nonce
    os.environ['FRENCH_BOT_NO_PAUSE']='1'
    class ShellInfo(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('fMask',ctypes.c_ulong),('hwnd',wintypes.HWND),
                  ('lpVerb',wintypes.LPCWSTR),('lpFile',wintypes.LPCWSTR),('lpParameters',wintypes.LPCWSTR),
                  ('lpDirectory',wintypes.LPCWSTR),('nShow',ctypes.c_int),('hInstApp',wintypes.HINSTANCE),
                  ('lpIDList',wintypes.LPVOID),('lpClass',wintypes.LPCWSTR),('hkeyClass',wintypes.HKEY),
                  ('dwHotKey',wintypes.DWORD),('hIcon',wintypes.HANDLE),('hProcess',wintypes.HANDLE)]
    shell=ctypes.windll.shell32;shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ShellInfo)]
    kernel=ctypes.windll.kernel32
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    def launch():
        args=['--offline-smoke','--offline-hold','--data-dir',str(work/'launcher-notes'),'--state-dir',str(state)]
        info=ShellInfo();info.cbSize=ctypes.sizeof(info);info.fMask=0x40;info.lpVerb='open'
        info.lpFile=str(installation.root/'launchers/french/START_BOT.cmd');info.lpParameters=subprocess.list2cmdline(args)
        info.lpDirectory=str(work);info.nShow=0  # Test helper windows stay hidden.
        if not shell.ShellExecuteExW(ctypes.byref(info)):raise ctypes.WinError()
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if ready.exists():
                try:
                    data=json.loads(ready.read_text('utf-8'))
                    if data.get('nonce')==nonce:return info.hProcess,data['pid']
                except (ValueError,OSError):pass
            time.sleep(.1)
        raise RuntimeError('Shell launcher did not reach offline polling.')
    def terminate_fixture(pid):
        # Verify the exact child command before terminating this isolated test.
        command=f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}').CommandLine"
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,text=True,check=True)
        if '--offline-hold' not in result.stdout or str(state) not in result.stdout:
            raise RuntimeError('Refusing to stop a process outside the isolated launcher test.')
        process=kernel.OpenProcess
        process.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];process.restype=wintypes.HANDLE
        handle=process(0x0001|0x00100000,False,pid)
        if not handle:raise ctypes.WinError()
        try:
            kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
            if not kernel.TerminateProcess(handle,91):raise ctypes.WinError()
            kernel.WaitForSingleObject(handle,5000)
        finally:kernel.CloseHandle(handle)
    process,pid=launch();terminate_fixture(pid);kernel.WaitForSingleObject(process,10000);kernel.CloseHandle(process)
    # Same state and actual launcher must recover after abrupt process termination.
    ready.unlink()
    process,pid=launch()
    from .state import State
    db=State(state,work/'launcher-notes')
    try:preserved=db.has_owner() and db.allowed(1)
    finally:db.close()
    terminate_fixture(pid);kernel.WaitForSingleObject(process,10000);kernel.CloseHandle(process)
    result={'shell_execute_launch':True,'working_directory_independence':True,'forced_termination':True,
            'restart_after_termination':True,'owner_preserved':preserved,'real_telegram':False}
    if not preserved:raise AssertionError('Owner did not survive restart.')
    (work/'windows-shell-check.json').write_text(json.dumps(result,indent=2),'utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
