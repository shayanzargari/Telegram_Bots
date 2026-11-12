"""Masked local credential provisioning; shared runtime remains core-independent."""
from __future__ import annotations
import argparse,getpass,sys,warnings
from bot_center_runtime.paths import Installation
from bot_center_runtime.credentials import CredentialStore,verify_identity
from bot_center_runtime.locks import ResourceLock,LockBusy
from bot_center_runtime.redaction import redact
from .registration import RegistrationService

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--bot',required=True);args=parser.parse_args(argv)
    lock=None
    try:
        installation=Installation(args.root);installation.assert_active();manifest=installation.bot(args.bot)
        if manifest.get('telegram_id') is None:
            lock=ResourceLock('core:'+str(installation.root).casefold())
            try:lock.acquire()
            except LockBusy:raise RuntimeError('Quit the core before enrolling a previously unknown numeric identity. No credential was requested or changed.') from None
        if not sys.stdin.isatty():raise RuntimeError('Open the supported local credential setup command in Windows Terminal; input cannot be accepted securely here.')
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=getpass.getpass('Telegram token (hidden, local only): ').strip()
        identity=verify_identity(token,manifest.get('telegram_id'),manifest.get('expected_username'))
        if manifest.get('telegram_id') is None:RegistrationService(installation.root).bind_identity(args.bot,identity['id'])
        CredentialStore(installation.root).store_protected(manifest['credential_ref'],token)
        print('Credential identity verified and saved with current-user Windows encryption. Bot ID:',identity['id'])
        print('Return to the dashboard. Enable if needed, then choose Retry or Start explicitly.')
        return 0
    except (Exception,KeyboardInterrupt) as error:
        print(redact(error) if not isinstance(error,KeyboardInterrupt) else 'Credential setup cancelled; prior credential was preserved.',file=sys.stderr);return 2
    finally:
        if lock:lock.close()

if __name__=='__main__':raise SystemExit(main())
