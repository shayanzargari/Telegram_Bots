"""Durable contract-v1 reader: the owning loop alone advances Telegram offset."""
from __future__ import annotations
import asyncio, ssl, sys
from pathlib import Path
from types import SimpleNamespace

async def run_async(client,manifest):
    from telegram.ext import Application
    from telegram.request import HTTPXRequest
    from telegram.error import Conflict,InvalidToken,NetworkError,RetryAfter
    from .telegram_bot.state import State
    from .telegram_bot.refresh import LibraryManager
    from .telegram_bot.service import Service
    from .telegram_bot.guard import ReadOnlyGuard
    from .speech import FrenchSpeech
    directory=Path(manifest['data_dir']);notes=Path(manifest['notes_dir'])
    editor_state=Path(manifest['editor_state_dir'])
    if not (directory/'reader.sqlite3').is_file() or not (directory/'identity.json').is_file() or not notes.is_dir():
        client.emit('failure',category='configuration',message='French expected state or notes are missing; restore the verified set.')
        return 2
    context=ssl.create_default_context();context.load_default_certs()
    request=HTTPXRequest(connection_pool_size=32,read_timeout=15,connect_timeout=8,write_timeout=8,pool_timeout=5,httpx_kwargs={'verify':context})
    polling=HTTPXRequest(read_timeout=15,connect_timeout=8,write_timeout=8,pool_timeout=5,httpx_kwargs={'verify':context})
    application=Application.builder().token(client.bootstrap['token']).request(request).get_updates_request(polling).concurrent_updates(False).build()
    state=None;service=None;guard=None;initialized=False
    try:
        # The runner already owns the numeric poll lock. Revalidate before
        # writable application state or Telegram-side mutations.
        while not client.stop.is_set():
            try:
                await asyncio.wait_for(application.initialize(),30);initialized=True;break
            except (NetworkError,TimeoutError):
                client.progress(connection='Disconnected')
                for _ in range(10):
                    if client.stop.is_set():break
                    await asyncio.sleep(.2)
        if not initialized:return 0
        if application.bot.id!=int(manifest['telegram_id']):
            client.emit('failure',category='identity',message='French token belongs to a different bot; ownership was left unchanged.')
            return 2
        while not client.stop.is_set():
            try:webhook=await asyncio.wait_for(application.bot.get_webhook_info(),30);break
            except (NetworkError,TimeoutError):
                client.progress(connection='Disconnected')
                for _ in range(10):
                    if client.stop.is_set():break
                    await asyncio.sleep(.2)
        if client.stop.is_set():return 0
        if webhook.url:
            client.emit('failure',category='configuration',message='French has an existing webhook. Deliberately remove it before using polling.')
            return 2
        guard=ReadOnlyGuard(notes,editor_state).install()
        state=State(directory,notes)
        saved=state.meta('bot_id')
        if saved and int(saved)!=application.bot.id:
            client.emit('failure',category='identity',message='French state belongs to another Telegram bot; ownership was left unchanged.')
            return 2
        manager=LibraryManager(notes,state,Path(__file__).parent/'telegram_bot/approved-source.json',journal=manifest['history_path'])
        if manager.current is None:
            client.emit('failure',category='configuration',message='French library needs a local source review; accepted notes were left unchanged.')
            return 2
        speech=FrenchSpeech(directory,Path(__file__).parent,python_executable=sys.executable)
        service=Service(state,manager,speech)
        client.ready()
        last_scan=0
        while not client.stop.is_set():
            client.progress()
            try:
                # No Updater is used: PTB's queue acknowledgement can otherwise
                # advance before handlers commit. A completed empty poll counts.
                async with asyncio.timeout(30):
                    updates=await application.bot.get_updates(offset=int(state.meta('polling_offset') or 0),timeout=10,allowed_updates=['message','callback_query'],read_timeout=15,connect_timeout=8,write_timeout=8,pool_timeout=5)
                client.progress(poll_ok=True)
                for update in updates:
                    if client.stop.is_set():break
                    await service.handle(update,SimpleNamespace(bot=application.bot))
                    client.progress()
                now=asyncio.get_running_loop().time()
                if now-last_scan>=2:
                    await asyncio.to_thread(manager.scan);last_scan=now;client.progress()
            except (NetworkError,RetryAfter,TimeoutError) as error:
                if isinstance(error,Conflict):
                    client.emit('failure',category='conflict',message='French polling conflicts with another process. Stop that instance before retrying.')
                    return 2
                client.emit('connection',state='Disconnected');client.progress(connection='Disconnected')
                # Reconnect inside this same worker, while processing control.
                for _ in range(10):
                    if client.stop.is_set():break
                    await asyncio.sleep(.2)
        return 0
    except InvalidToken:
        client.emit('failure',category='credentials',message='French credential was rejected. Replace it through masked local input.')
        return 2
    except Conflict:
        client.emit('failure',category='conflict',message='French polling is already active elsewhere.')
        return 2
    except NetworkError:
        client.emit('failure',category='network',message='French initialization could not reach Telegram; connectivity is not credential validity.')
        return 3
    except (ValueError,PermissionError):
        client.emit('failure',category='configuration',message='French library, state or resource validation failed; no state was reset.')
        return 2
    finally:
        if service:await service.close()
        if state:state.close()
        if guard:guard.active=False
        if initialized:await application.shutdown()
        else:
            await request.shutdown();await polling.shutdown()

def run_job(client,manifest,job,payload):
    from bot_center_runtime.locks import ResourceLock
    from .telegram_bot.state import State
    from .telegram_bot.refresh import LibraryManager
    from .speech import FrenchSpeech
    import json,re
    directory=Path(manifest['data_dir']);notes=Path(manifest['notes_dir'])
    if not (directory/'reader.sqlite3').is_file():raise ValueError('Required French database is missing.')
    with ResourceLock('job:'+manifest['id']+':'+job):
        state=State(directory,notes)
        try:
            manager=LibraryManager(notes,state,Path(__file__).parent/'telegram_bot/approved-source.json',journal=manifest['history_path'])
            if manager.current is None:raise ValueError('No accepted French source snapshot is available.')
            if job=='integrity':
                from .telegram_bot.library import validate
                validate(manager.current)
                if manager.current.manifest()!={s.name:s.hash for s in manager.reader.capture()}:raise ValueError('French source differs from the accepted snapshot; review required.')
                if state.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('French reader integrity failed.')
                if state.db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('French reader relation check failed.')
                local_audio=0
                for path in (directory/'speech').glob('*.mp3'):
                    if not re.fullmatch(r'[0-9a-f]{64}\.mp3',path.name):
                        # Unpublished worker temporaries cannot be claimed as
                        # completed backup assets.
                        continue
                    if not FrenchSpeech.valid(path.read_bytes()):raise ValueError('A prepared French recording is invalid.')
                    local_audio+=1;client.progress()
                references=0
                for key,value in state.db.execute("SELECT key,value FROM meta WHERE key LIKE 'audio:%'"):
                    if not re.fullmatch(r'audio:[0-9a-f]{64}',key) or not isinstance(value,str) or len(value)>4096:
                        raise ValueError('A saved French audio reference is malformed.')
                    references+=bool(value)
                checkpoint=directory/'audio-preparation.json'
                if checkpoint.exists():
                    saved=json.loads(checkpoint.read_text('utf-8'))
                    if saved.get('voice') not in {'denise','henri','vivienne'} or saved.get('speed') not in {'1','0.8'}:
                        raise ValueError('Prepared audio checkpoint has an unsupported voice profile.')
                    if not isinstance(saved.get('ready'),int) or saved['ready']<0 or saved['ready']>saved.get('recordings',-1):
                        raise ValueError('Prepared audio checkpoint has invalid completion counts.')
                    if saved['ready']>local_audio:raise ValueError('Prepared audio checkpoint references missing completed recordings.')
                    # cached() validates each asset again on prepare/resume;
                    # a stale source fingerprint is never trusted as current.
                    client.emit('progress',audio_checkpoint_current=saved.get('source_version')==manager.current.version,local_recordings=local_audio,telegram_file_references=references)
                client.emit('progress',message='French source and state integrity verified.');return 0
            if job=='prepare_audio':
                from .telegram_bot.prepare_audio import prepare
                report=prepare(state,notes,voice=payload.get('voice'),speed=payload.get('speed'),limit=payload.get('limit'),current_page=bool(payload.get('current_page')),stop=client.stop,progress=lambda ready,total:client.emit('progress',ready=ready,total=total))
                return 0 if report['complete'] else 4 if report['interrupted'] else 2
            if job=='validate_audio':
                invalid=[];count=0
                for path in (directory/'speech').glob('*.mp3'):
                    if client.stop.is_set():return 4
                    count+=1
                    if not FrenchSpeech.valid(path.read_bytes()):invalid.append(path.name)
                    client.progress()
                if invalid:raise ValueError('French audio cache includes invalid recordings; regenerate the affected cache entries.')
                client.emit('progress',message='French audio cache verified.',recordings=count);return 0
            raise ValueError('Unsupported French maintenance job.')
        finally:state.close()

def run(client,manifest):
    if client.bootstrap.get('job'):return run_job(client,manifest,client.bootstrap['job'],client.bootstrap.get('payload',{}))
    return asyncio.run(run_async(client,manifest))
