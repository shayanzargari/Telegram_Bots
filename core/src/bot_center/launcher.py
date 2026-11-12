"""Both Windows launchers use this authenticated, concurrency-safe startup."""
from __future__ import annotations
import argparse,json,os,socket,subprocess,sys,time,webbrowser
from pathlib import Path
from bot_center_runtime.paths import Installation,ConfigurationError
from bot_center_runtime.locks import ResourceLock,LockBusy
from bot_center_runtime.ipc import request,IPCError

def launch(root,open_browser=True):
    installation=Installation(root);installation.assert_active();root=installation.root
    lock=ResourceLock('launch:'+str(root).casefold());owned=False
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                response=request(root,{'action':'dashboard' if open_browser else 'identify'})
                body=response['body']
                if body.get('error'):raise RuntimeError(body['error'])
                if open_browser:
                    if not body.get('ready') or not body.get('ticket'):raise RuntimeError('Core is not ready; inspect management status.')
                    webbrowser.open(body['origin']+'/#ticket='+body['ticket'])
                return response
            except IPCError:
                if not owned:
                    try:lock.acquire();owned=True
                    except LockBusy:time.sleep(.2);continue
                    # Recheck under launcher ownership before spawning.
                    try:request(root,{'action':'identify'});continue
                    except IPCError:pass
                    settings=json.loads((root/'config/settings.json').read_text('utf-8')) if (root/'config/settings.json').exists() else {}
                    port=settings.get('port',8765)
                    with socket.socket() as probe:
                        try:probe.bind(('127.0.0.1',port))
                        except OSError:raise RuntimeError('Core port is occupied by an unauthenticated listener. Close the conflicting application or configure another port; no process was terminated.') from None
                    python=root/'.venvs/core/Scripts/pythonw.exe'
                    env=dict(os.environ);env['BOT_CENTER_ROOT']=str(root);env.pop('PYTHONPATH',None)
                    subprocess.Popen([str(python),'-m','bot_center','--root',str(root),'serve'],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=0x08000000)
                time.sleep(.2)
        raise RuntimeError('Core did not complete authenticated startup. See logs/core/core.log and recovery instructions.')
    finally:
        if owned:lock.close()

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--no-browser',action='store_true');args=parser.parse_args(argv)
    try:launch(Installation(args.root).root,not args.no_browser);return 0
    except Exception as error:
        from bot_center_runtime.redaction import redact
        message=redact(error)
        if sys.stderr:print(message,file=sys.stderr)
        elif os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'Bot Control Center could not open',0x10)
        return 2

if __name__=='__main__':raise SystemExit(main())
