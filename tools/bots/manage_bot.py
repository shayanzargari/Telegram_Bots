"""CLI adapter for the installed shared registration service.

Inspection/staging is offline. Publication requires a stopped core or its
protected IPC; this adapter refuses direct mutation while the core is active.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description='Create, inspect or register a Bot Control Center project')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create');create.add_argument('bot_id');create.add_argument('--name',required=True);create.add_argument('--telegram-id',type=int)
    create.add_argument('--stage-only',action='store_true');create.add_argument('--shared-wheel',type=Path)
    create.add_argument('--capability',action='append',choices=['polling','maintenance'])
    register=sub.add_parser('register');register.add_argument('manifest',type=Path)
    inspect=sub.add_parser('inspect');inspect.add_argument('manifest',type=Path)
    validate=sub.add_parser('validate');validate.add_argument('manifest',type=Path)
    recover=sub.add_parser('recover');recover.add_argument('operation_id');recover.add_argument('--complete',action='store_true')
    identity=sub.add_parser('set-identity');identity.add_argument('bot_id');identity.add_argument('--telegram-id',type=int,required=True)
    args=parser.parse_args(argv)
    try:
        from bot_center.registration import RegistrationService,RegistrationError
        from bot_center_runtime.redaction import redact
        root=args.root.resolve(strict=True)
        service=RegistrationService(root)
        if args.command in {'create','register','recover','set-identity'}:
            from bot_center_runtime.locks import ResourceLock,LockBusy
            # The exact installation lock is shared with the core; a finite CLI
            # must never become the sole lifetime owner of a managed bot.
            try:
                lock=ResourceLock('core:'+str(root).casefold());lock.__enter__()
            except LockBusy:
                raise RegistrationError('Core is running. Use Add bot in its authenticated dashboard, or Quit core before offline publication.') from None
        else:lock=None
        try:
            if args.command=='create':
                result=service.create(args.bot_id,args.name,args.telegram_id,capabilities=args.capability,install=not args.stage_only,shared_wheel=args.shared_wheel)
            elif args.command in {'register','inspect','validate'}:
                manifest=json.loads(args.manifest.read_text('utf-8'))
                if args.command=='register':result=service.register(args.manifest)
                elif args.command=='inspect':result=service.inspect(manifest)
                else:result={'valid':True,'manifest':service.validate(manifest,existing=True),'online_identity':'Not performed'}
            elif args.command=='set-identity':result=service.bind_identity(args.bot_id,args.telegram_id)
            else:result=service.recover(args.operation_id,complete=args.complete)
            print(redact(json.dumps(result,ensure_ascii=False,indent=2)))
            return 0
        finally:
            if lock is not None:lock.__exit__(None,None,None)
    except (ValueError,OSError,RuntimeError,ImportError) as error:
        # Never echo malformed manifest values or token-bearing request objects.
        if type(error).__name__ in {'RegistrationError','LockBusy'}:message=str(error)
        else:message='Management failed ('+type(error).__name__+'). Check the installed environment and local manifests.'
        print(message,file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
