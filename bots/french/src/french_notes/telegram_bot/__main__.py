"""Windows entry point. Tokens are accepted only through hidden console input."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import getpass
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import ssl
import sys
import warnings

from . import BOT_NAME,BOT_USERNAME
from .refresh import LibraryManager
from .state import State
from .audit import desktop_state
from .guard import ReadOnlyGuard

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_STATE=Path(os.environ.get('LOCALAPPDATA',ROOT/'checks'))/'French Notes Telegram'


class StartupError(Exception):pass


class SafeFormatter(logging.Formatter):
    def __init__(self,token=''):
        super().__init__('%(asctime)s %(levelname)s %(message)s');self.token=token

    def format(self,record):
        output=super().format(record)
        if self.token:output=output.replace(self.token,'[REDACTED]')
        return re.sub(r'\b\d{5,}:[A-Za-z0-9_-]{20,}','[REDACTED]',output)


def logging_setup(state,token=''):
    handler=RotatingFileHandler(state.root/'bot.log',maxBytes=5*1024*1024,backupCount=2,encoding='utf-8')
    handler.setFormatter(SafeFormatter(token))
    logging.basicConfig(level=logging.WARNING,handlers=[handler],force=True)
    for name in ('httpx','httpcore','telegram'):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def hidden_token():
    if not sys.stdin.isatty():
        raise StartupError('Hidden token input needs a terminal. Double-click START_BOT.bat or run it from Windows Terminal.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:token=getpass.getpass('Paste the Telegram bot token (hidden), then press Enter: ').strip()
        except (getpass.GetPassWarning,EOFError) as error:
            raise StartupError('Secure terminal input is unavailable. Token input was cancelled.') from error
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}',token):
        raise StartupError('That token has an invalid format. Restart and paste the token from BotFather.')
    return token


class Instance:
    def __init__(self,name=BOT_USERNAME):self.name=name;self.handle=None
    def __enter__(self):
        if os.name=='nt':
            from ctypes import wintypes
            kernel=ctypes.windll.kernel32
            kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR]
            kernel.CreateMutexW.restype=wintypes.HANDLE
            self.handle=kernel.CreateMutexW(None,True,'Local\\FrenchNotesTelegram-'+self.name)
            if not self.handle:raise StartupError('Could not acquire the Windows instance lock.')
            if kernel.GetLastError()==183:
                kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(self.handle);self.handle=None
                raise StartupError('This bot is already running on this Windows account. Close the other bot window first.')
        return self
    def __exit__(self,*args):
        if self.handle:
            from ctypes import wintypes
            kernel=ctypes.windll.kernel32
            kernel.ReleaseMutex.argtypes=[wintypes.HANDLE];kernel.ReleaseMutex(self.handle)
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(self.handle)


def owner_pairing_link(state):
    """Local recovery only: issue a fresh pairing link without restarting polling."""
    if state.has_owner():
        raise StartupError('An owner is already paired. Use that Telegram account; ownership was left unchanged.')
    if not state.meta('bot_id'):
        raise StartupError('Start START_BOT.bat with the correct token first, then pair the owner.')
    return f'https://t.me/{BOT_USERNAME}?start={state.invitation("owner")}'


async def run(state,manager,token):
    from telegram import BotCommand,MenuButtonCommands
    from telegram.error import Conflict,InvalidToken,NetworkError
    from telegram.ext import Application
    from telegram.request import HTTPXRequest
    from ..speech import FrenchSpeech
    from .service import Service,attach

    context=ssl.create_default_context()
    request=HTTPXRequest(connection_pool_size=32,read_timeout=15,connect_timeout=8,write_timeout=30,
                         pool_timeout=5,httpx_kwargs={'verify':context})
    polling_request=HTTPXRequest(read_timeout=35,connect_timeout=8,httpx_kwargs={'verify':context})
    application=Application.builder().token(token).request(request).get_updates_request(polling_request).concurrent_updates(32).build()
    speech=FrenchSpeech(state.root,ROOT,python_executable=sys.executable)
    service=Service(state,manager,speech);attach(application,service)
    initialized=False;started=False;polling=False
    loop=asyncio.get_running_loop();stop=asyncio.Event()
    def poll_error(error):
        service.health['polling']='disconnected; reconnecting'
        if isinstance(error,Conflict):
            print('Polling conflict: another instance is using this bot token. Close it, then restart.',flush=True)
            stop.set()
        else:print('Telegram connection interrupted. Reconnecting automatically.',flush=True)
    try:
        for attempt in range(5):
            try:
                await application.initialize();initialized=True;break
            except InvalidToken as error:
                raise StartupError('Telegram rejected the token. Restart with the correct BotFather token.') from error
            except NetworkError:
                if attempt==4:raise StartupError('Could not reach Telegram. Check the internet/proxy connection and restart.')
                print('Cannot reach Telegram yet; retrying…',flush=True)
                await asyncio.sleep(min(2**attempt,15))
        me=application.bot
        if me.username.casefold()!=BOT_USERNAME.casefold():
            raise StartupError('This token belongs to a different bot. Use the token for @'+BOT_USERNAME+'.')
        state.bind_bot(me.id)
        webhook=await application.bot.get_webhook_info()
        if webhook.url:raise StartupError('An existing webhook is configured. It was left unchanged. See BOT_README.md before switching to polling.')
        await application.bot.set_my_name(BOT_NAME)
        await application.bot.set_my_commands([BotCommand('menu','Main menu'),BotCommand('search','Search material'),BotCommand('settings','Voice and speed'),BotCommand('help','How to use this reader')])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        state.backup()
        if not state.has_owner():
            print('\nOWNER PAIRING — open this link yourself within ten minutes:',flush=True)
            print(owner_pairing_link(state)+'\n',flush=True)
            print('Open the link in your Telegram account and tap Start. Opening the bot by username alone does not pair you.',flush=True)
            print('If the link expires, run PAIR_OWNER.bat; the bot can keep running.\n',flush=True)
        await application.start();started=True
        await application.updater.start_polling(timeout=25,poll_interval=0,allowed_updates=['message','callback_query'],
                                                 drop_pending_updates=False,bootstrap_retries=0,error_callback=poll_error)
        polling=True;service.health['polling']='connected'
        service.watcher=asyncio.create_task(service.watch())
        # Imports/SSL setup can warm while the reader opens the first category.
        if hasattr(speech,'worker'):await asyncio.to_thread(speech.worker.warm)
        print(f'Running @{BOT_USERNAME} — {len(manager.current.documents)} views, {len(manager.current.entries)} entries.',flush=True)
        print('Polling connected | Library verified | Access database healthy | Speech ready',flush=True)
        print('Keep this PC awake and online. Press Ctrl+C to stop.',flush=True)
        await stop.wait()
    finally:
        service.closing=True
        if polling:await application.updater.stop()
        if started:await application.stop()
        await service.close()
        if initialized:await application.shutdown()
        else:
            await request.shutdown()
            await polling_request.shutdown()


def main():
    finite={'--prepare-audio','--preflight','--review-source','--pair-owner','--offline-smoke','--offline-hold','--credential-check'}
    if not finite.intersection(sys.argv[1:]) and '--help' not in sys.argv[1:] and not {'--data-dir','--state-dir'}.intersection(sys.argv[1:]):
        from bot_center_runtime.runner import main as standalone
        return standalone(['--bot','french',*sys.argv[1:]])
    parser=argparse.ArgumentParser(description='Read-only French_Learning Telegram bot')
    parser.add_argument('--root')
    parser.add_argument('--data-dir')
    parser.add_argument('--state-dir')
    parser.add_argument('--review-source',action='store_true')
    parser.add_argument('--pair-owner',action='store_true',help='Create a fresh owner-pairing link locally, without restarting the running bot')
    parser.add_argument('--prepare-audio',action='store_true',help='Prepare a reusable local pronunciation bundle without a Telegram token')
    parser.add_argument('--voice',choices=['denise','henri','vivienne'])
    parser.add_argument('--speed',choices=['normal','slow'])
    parser.add_argument('--limit',type=int)
    parser.add_argument('--current-page',action='store_true')
    parser.add_argument('--preflight',action='store_true',help='Validate local startup without contacting Telegram')
    parser.add_argument('--offline-smoke',action='store_true',help='Run simulated Telegram on isolated notes/state')
    parser.add_argument('--credential-check',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--offline-hold',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    canonical=None;installation=None
    if not (args.offline_smoke and args.data_dir and args.state_dir) and (args.data_dir is None or args.state_dir is None or args.root):
        from bot_center_runtime.paths import Installation
        installation=Installation(args.root);canonical=installation.require_state('french')
        if args.data_dir and Path(args.data_dir).resolve()!=Path(canonical['notes_dir']).resolve():
            raise StartupError('Canonical French maintenance requires the registered notes folder; copy notes for isolated tests without --root.')
        if args.state_dir and Path(args.state_dir).resolve()!=Path(canonical['data_dir']).resolve():
            raise StartupError('Canonical French maintenance requires the preserved reader state; copy notes for isolated tests without --root.')
        args.data_dir=args.data_dir or canonical['notes_dir']
        args.state_dir=args.state_dir or canonical['data_dir']
    if canonical is None:
        from bot_center_runtime.paths import Installation,ConfigurationError
        try:possible=Installation();declared=possible.bot('french')
        except (ConfigurationError,OSError):possible=None
        if possible and Path(args.data_dir).resolve()==Path(declared['notes_dir']).resolve():
            installation=possible;canonical=possible.require_state('french')
            if Path(args.state_dir).resolve()!=Path(canonical['data_dir']).resolve():
                raise StartupError('Canonical French notes require the preserved reader state; copy notes for isolated tests.')
    if not finite.intersection(sys.argv[1:]):
        if not canonical:raise StartupError('Polling requires registered canonical state; use --root for the registered installation.')
        from bot_center_runtime.runner import main as standalone
        return standalone(['--bot','french','--root',str(installation.root)])
    if not args.offline_smoke and (not (Path(args.state_dir)/'reader.sqlite3').is_file() or not (Path(args.state_dir)/'identity.json').is_file()):
        raise StartupError('Expected French reader state is missing. Restore or explicitly create/register a new project; ownership was not initialized.')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    ReadOnlyGuard(args.data_dir,canonical['editor_state_dir'] if canonical else desktop_state(args.data_dir)).install()
    if args.offline_smoke:
        if canonical or Path(args.data_dir).resolve()==Path('D:/french').resolve() or Path(args.state_dir).resolve()==DEFAULT_STATE.resolve():
            raise StartupError('Offline smoke tests require isolated --data-dir and --state-dir paths.')
        if args.credential_check:
            hidden_token()
            print('Hidden input received; not printed or stored.',flush=True)
        from .offline import smoke
        with Instance(BOT_USERNAME+'-offline'):
            asyncio.run(smoke(args.data_dir,args.state_dir,hold=args.offline_hold))
        return
    ownership=None
    if canonical:
        from contextlib import ExitStack
        from bot_center_runtime.locks import FileLease,ResourceLock
        ownership=ExitStack();ownership.enter_context(FileLease(installation.root/'data/maintenance.lock'))
        if args.prepare_audio:ownership.enter_context(ResourceLock('job:french:prepare_audio'))
    state=State(args.state_dir,args.data_dir)
    try:
        if args.prepare_audio:
            from .prepare_audio import prepare
            prepare(state,args.data_dir,args.voice,{'normal':'1','slow':'0.8'}.get(args.speed),args.limit,args.current_page)
            return
        if args.pair_owner:
            link=owner_pairing_link(state)
            print('OWNER ACTIVATION — expires in ten minutes; single use.\n',flush=True)
            print(link,flush=True)
            print('\nOpen this link in your Telegram account and tap Start. The bot can stay running.',flush=True)
            return
        manager=LibraryManager(args.data_dir,state,ROOT/'telegram_bot/approved-source.json',journal=canonical['history_path'] if canonical else None)
        if args.review_source:
            manager.review()
            return
        if manager.current is None:
            raise StartupError('Source changed since the release audit. Run START_BOT.bat --review-source, review the diff, and accept the exact candidate.')
        manager.scan()
        if args.preflight:
            import telegram,edge_tts
            print(f'PREFLIGHT PASS | {len(manager.current.sources)} files | {len(manager.current.documents)} views | {len(manager.current.entries)} entries')
            print('Library:',manager.status)
            print('Telegram:',telegram.__version__,'| Speech package:',edge_tts.__version__)
            print('No Telegram connection or phone verification was performed.')
            return
        with Instance():
            token=hidden_token();logging_setup(state,token)
            asyncio.run(run(state,manager,token))
    finally:
        state.close()
        if ownership:ownership.close()


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nBot stopped cleanly.')
    except StartupError as error:
        print('STARTUP:',str(error),file=sys.stderr);sys.exit(2)
    except Exception as error:
        print('STARTUP:',type(error).__name__,'— no access state was reset. See BOT_README.md.',file=sys.stderr);sys.exit(2)
