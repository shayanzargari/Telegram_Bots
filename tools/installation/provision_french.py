"""One local masked credential step; never polls or changes reader ownership."""
from __future__ import annotations
import argparse,getpass,json,sqlite3,sys,warnings
from pathlib import Path
from bot_center_runtime.credentials import CredentialStore,verify_identity
from bot_center_runtime.redaction import redact

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);args=parser.parse_args();root=args.root.resolve()
    manifest=root/'config/bots/french.json'
    if manifest.exists():expected=int(json.loads(manifest.read_text('utf-8'))['telegram_id'])
    else:
        path=root/'work/migration/initial/state/french/reader/reader.sqlite3'
        connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:expected=int(connection.execute("SELECT value FROM meta WHERE key='bot_id'").fetchone()[0])
        finally:connection.close()
    if not sys.stdin.isatty():raise SystemExit('Open SETUP_FRENCH_CREDENTIAL.cmd in a local terminal. No token was read or displayed.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        token=getpass.getpass('French Telegram token (hidden; local only): ').strip()
    try:
        identity=CredentialStore(root).stage_replace('french',token,expected,'Easy_French_Learning_bot')
        print('French identity verified and credential protected for the current Windows user. No polling or ownership changes occurred.')
        directory=root/'work/setup';directory.mkdir(parents=True,exist_ok=True)
        (directory/'french-credential.json').write_text(json.dumps({'status':'Verified','telegram_id':identity['id'],'credential_ref':'french','polling_started':False}),encoding='utf-8')
        return 0
    except Exception as error:print(redact(error),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
