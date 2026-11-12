"""Owned native fixtures only; no imports of production app state/credentials."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def atomic(path,body):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(body),encoding='utf-8');os.replace(temporary,path)


def owner(directory):
    from bot_center_runtime.process import ManagedProcess
    ready=threading.Event();events=[]
    def event(value):
        events.append(value)
        if value.get('kind')=='progress' and value.get('fixture')=='tree':ready.set()
    boot={'contract_version':1,'run_id':uuid.uuid4().hex,'manifest':{'id':'fixture-tree'},'fixture_directory':str(directory)}
    process=ManagedProcess([sys.executable,__file__,'--mode','tree'],boot,cwd=directory,on_event=event)
    try:
        if not ready.wait(8):raise RuntimeError('Isolated tree fixture did not become ready.')
        tree=next(e for e in events if e.get('fixture')=='tree')
        atomic(directory/'owner.json',{'owner':os.getpid(),'child':tree['worker_pid'],'grandchild':tree['pid'],'child_wrapper':process.pid})
        while True:time.sleep(.2)
    finally:process.close()


def worker(mode):
    from bot_center_runtime.protocol import WorkerClient,write_frame
    client=WorkerClient.from_environment();client.ready()
    try:
        if mode=='pressure':
            def flood(stream):
                block=b'fixture-output '+b'X'*8192+b'\n'
                while not client.stop.is_set():
                    try:os.write(stream.fileno(),block)
                    except OSError:return
            for stream in (sys.stdout,sys.stderr):threading.Thread(target=flood,args=(stream,),daemon=True).start()
        elif mode=='leakstream':
            secret=client.bootstrap['canary'].encode()
            data=b'A'*73210+secret+b'B'*(150000-73210-len(secret))+b'\n'
            for index in range(0,len(data),8192):os.write(sys.stdout.fileno(),data[index:index+8192])
            sys.stdout.flush();client.emit('progress',fixture='output_complete')
        elif mode=='stale':
            write_frame(client.events,{'contract_version':1,'run_id':'previous-run','seq':900,'kind':'ready'})
        elif mode=='sequence':
            write_frame(client.events,{'contract_version':1,'run_id':client.run_id,'seq':1,'kind':'ready'})
        elif mode=='malformed':
            import struct
            client.events.write(struct.pack('<I',70000));client.events.flush()
        elif mode=='missing-kind':
            write_frame(client.events,{'contract_version':1,'run_id':client.run_id,'seq':999})
        elif mode=='wrong-contract':
            write_frame(client.events,{'contract_version':99,'run_id':client.run_id,'seq':999,'kind':'ready'})
        elif mode=='boolean-contract':
            write_frame(client.events,{'contract_version':True,'run_id':client.run_id,'seq':999,'kind':'ready'})
        elif mode=='invalid-kind':
            write_frame(client.events,{'contract_version':1,'run_id':client.run_id,'seq':999,'kind':['malformed']})
        elif mode=='failure-fields':
            write_frame(client.events,{'contract_version':1,'run_id':client.run_id,'seq':999,'kind':'failure','category':['malformed'],'message':'fixture failure'})
        elif mode=='health-fields':
            write_frame(client.events,{'contract_version':1,'run_id':client.run_id,'seq':999,'kind':'health','connection':{'bad':'value'},'progress':time.monotonic()})
        elif mode=='inheritance':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,ctypes.POINTER(w.BOOL)]
            result=w.BOOL()
            success=kernel.IsProcessInJob(w.HANDLE(-1),w.HANDLE(client.bootstrap['forbidden_job']),ctypes.byref(result))
            client.emit('progress',fixture='inheritance',forbidden_job_valid=bool(success),error=ctypes.get_last_error())
        elif mode=='tree':
            directory=Path(client.bootstrap['fixture_directory'])
            child=subprocess.Popen([sys.executable,__file__,'--mode','sleep','--directory',str(directory)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=0x08000000)
            deadline=time.monotonic()+4
            while not (directory/'descendant.json').is_file():
                if time.monotonic()>=deadline:raise RuntimeError('Owned descendant did not report its actual interpreter identity.')
                time.sleep(.01)
            actual=json.loads((directory/'descendant.json').read_text())['pid']
            client.emit('progress',fixture='tree',pid=actual,worker_pid=os.getpid(),wrapper_pid=child.pid)
        client.stop.wait(30)
        client.emit('progress',fixture='stopped')
    finally:client.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',required=True);parser.add_argument('--directory',type=Path);parser.add_argument('--name');args=parser.parse_args()
    if args.mode=='owner':return owner(args.directory)
    if args.mode=='entry-marker':
        (args.directory/'entered.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf-8')
        return worker('control')
    if args.mode=='sleep':
        if args.directory:atomic(args.directory/'descendant.json',{'pid':os.getpid()})
        while True:time.sleep(1)
    if args.mode=='acquire':
        from bot_center_runtime.locks import ResourceLock,LockBusy
        try:
            with ResourceLock(args.name):print('acquired',flush=True)
            return 0
        except LockBusy:print('busy',flush=True);return 3
    if args.mode=='hold':
        from bot_center_runtime.locks import ResourceLock
        with ResourceLock(args.name):
            atomic(args.directory/'lock.json',{'owner':os.getpid()})
            while True:time.sleep(.2)
    return worker(args.mode)


if __name__=='__main__':raise SystemExit(main())
