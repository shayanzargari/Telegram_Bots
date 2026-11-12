"""Lifetime-owning standalone runner and common managed worker bootstrap."""
from __future__ import annotations
import argparse,importlib,os,signal,sys,time,uuid
from pathlib import Path
from .paths import Installation,ConfigurationError
from .credentials import CredentialStore,verify_identity,CredentialError,ConnectivityError
from .locks import ResourceLock,FileLease,LockBusy
from .protocol import WorkerClient
from .process import ManagedProcess
from .redaction import redact

def worker():
    client=WorkerClient.from_environment()
    try:
        installation=Installation(client.bootstrap['root']);installation.require_state(client.bootstrap['bot_id'])
        with FileLease(installation.root/'data/maintenance.lock'):
            module=importlib.import_module(client.manifest['module'])
            return module.run(client,client.manifest) or 0
    except BaseException as error:
        if isinstance(error,KeyboardInterrupt):return 0
        client.emit('failure',category='configuration' if isinstance(error,ConfigurationError) else 'unexpected',message=redact(error,secrets=[client.bootstrap.get('token')]))
        return 2
    finally:client.close()

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--bot');parser.add_argument('--worker',action='store_true');args=parser.parse_args(argv)
    if args.worker:return worker()
    if not args.bot:parser.error('--bot is required')
    installation=Installation(args.root);manifest=installation.require_state(args.bot)
    try:
        token=CredentialStore(installation.root).load(manifest['credential_ref'])
        identity=verify_identity(token,manifest.get('telegram_id'),manifest.get('expected_username'))
        if not manifest.get('telegram_id'):raise ConfigurationError('Expected numeric bot identity must be enrolled before running.')
        with ResourceLock('poll:'+str(identity['id'])):
            process=ManagedProcess([manifest['python'],'-m','bot_center_runtime.runner','--worker'],{'contract_version':1,'root':str(installation.root),'bot_id':args.bot,'run_id':uuid.uuid4().hex,'token':token,'manifest':manifest},cwd=manifest['cwd'],environment={'BOT_CENTER_ROOT':str(installation.root)},on_event=lambda event:print(redact(event.get('message','')) or event['kind'],flush=True))
            stop_deadline=None
            def stop(*_):
                nonlocal stop_deadline
                if stop_deadline is None:stop_deadline=time.monotonic()+45
                process.request_stop()
            signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
            try:
                while process.poll() is None:
                    if stop_deadline is not None and time.monotonic()>=stop_deadline:process.terminate()
                    time.sleep(.2)
                return process.poll()
            finally:process.close()
    except (CredentialError,ConnectivityError,ConfigurationError,LockBusy) as error:
        print(redact(error));return 2

if __name__=='__main__':raise SystemExit(main())
