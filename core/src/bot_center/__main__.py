"""Persistent core and finite protected-IPC management commands."""
from __future__ import annotations
import argparse,asyncio,json,logging,os,socket,sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from bot_center_runtime.paths import Installation
from bot_center_runtime.locks import ResourceLock,LockBusy
from bot_center_runtime.ipc import request
from bot_center_runtime.redaction import RedactFilter,redact

async def serve(root):
    import uvicorn
    from .api import Core,create_app
    installation=Installation(root);installation.assert_active()
    if not (root/'data/core/core.sqlite3').is_file():raise RuntimeError('Expected core state is missing. Restore the saved intent database; startup will not initialize empty state.')
    settings=json.loads((root/'config/settings.json').read_text('utf-8')) if (root/'config/settings.json').exists() else {}
    port=int(settings.get('port',8765))
    if not 1024<=port<=65535:raise RuntimeError('Core port must be between 1024 and 65535.')
    with ResourceLock('core:'+str(root).casefold()):
        directory=root/'logs/core';directory.mkdir(parents=True,exist_ok=True)
        handler=RotatingFileHandler(directory/'core.log',maxBytes=1024*1024,backupCount=3,encoding='utf-8');handler.addFilter(RedactFilter())
        logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
        # Bind before constructing/startup tasks so a collision cannot launch bots.
        sock=socket.socket();sock.bind(('127.0.0.1',port));sock.listen(128);sock.setblocking(False)
        core=Core(root,port);app=create_app(core)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,workers=1,reload=False,access_log=False,log_config=None))
        async def quit_watch():
            await core.quit.wait();server.should_exit=True
        watcher=asyncio.create_task(quit_watch())
        try:await server.serve(sockets=[sock])
        finally:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True);sock.close()

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('command',choices=['serve','open','start-core','status','start','stop','restart','retry','enable','disable','start_all','stop_all','quit','backup','open_editor','job']);parser.add_argument('--bot',default='*');parser.add_argument('--job');parser.add_argument('--operation-id');args=parser.parse_args(argv)
    try:
        root=Installation(args.root).root
        if args.command=='serve':asyncio.run(serve(root));return 0
        if args.command in {'open','start-core'}:
            from .launcher import launch
            launch(root,open_browser=args.command=='open');return 0
        payload={'job':args.job} if args.command=='job' else {}
        response=request(root,{'action':args.command,'target':args.bot,'payload':payload,'operation_id':args.operation_id},timeout=180)['body']
        print(json.dumps(response,ensure_ascii=False,indent=2));return 2 if response.get('error') else 0
    except LockBusy:return 0
    except Exception as error:
        message=redact(error)
        if sys.stderr:print(message,file=sys.stderr)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'Bot Control Center',0x10)
        return 2

if __name__=='__main__':raise SystemExit(main())
