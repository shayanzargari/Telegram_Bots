"""Resumable local voice bundle. Uses saved material; never sends Telegram messages."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

from ..speech import FrenchSpeech
from .audio import audio_key
from .reader import Reader,chunks
from .refresh import LibraryManager
from ..store import atomic_json

ROOT=Path(__file__).resolve().parents[1]


def prepare(state,source,voice=None,speed=None,limit=None,current_page=False,stop=None,progress=None):
    manager=LibraryManager(source,state)
    if manager.current is None:raise ValueError('Start the bot or approve its source library first.')
    owner=next((u for u in state.readers() if u['owner']),None)
    profile=state.user(owner['id']) if owner else None
    voice=voice or (profile['voice'] if profile else 'denise')
    speed=speed or (profile['speed'] if profile else '1')
    entries=manager.current.entries
    if current_page and profile:
        view=state.session(owner['id'])['view'];engine=Reader(manager.current)
        if view.get('screen') in ('list','results'):
            page=view.get('page',0);entries=engine.sequence(view)[page*8:(page+1)*8]
        elif view.get('screen')=='entry':
            current=manager.current.entry(view.get('entry'))
            sequence=engine.sequence(view.get('origin',{}))
            index=next((i for i,e in enumerate(sequence) if e.id==view.get('entry')),0)
            entries=sequence[index:index+8] if sequence else (current,) if current else ()
        else:entries=entries[:8]
    phrases=list(dict.fromkeys(part.strip() for e in entries if e and e.speech for part in chunks(e.speech,3500) if part.strip()))
    if limit is not None:
        if limit<1:raise ValueError('Audio limit must be positive.')
        phrases=phrases[:limit]
    speech=FrenchSpeech(state.root,ROOT,python_executable=sys.executable)
    cached=[text for text in phrases if speech.cached(text,voice,speed) is not None]
    missing=[text for text in phrases if text not in set(cached)]
    print(f'Voice bundle: {voice.title()} / '+('Slow' if speed=='0.8' else 'Normal'),flush=True)
    print(f'{len(phrases)} recordings · {len(cached)} already prepared · {len(missing)} to prepare',flush=True)
    print('French text is sent to the existing speech service. No Telegram messages are sent. Ctrl+C stops; rerun to resume.',flush=True)
    completed=0;failed=0;consecutive_failures=0;interrupted=False
    executor=ThreadPoolExecutor(max_workers=2);futures=[]
    def checkpoint():
        ready=sum(speech.cached(text,voice,speed) is not None for text in phrases)
        atomic_json(state.root/'audio-preparation.json',{'source_version':manager.current.version,'voice':voice,'speed':speed,'ready':ready,'recordings':len(phrases),'complete':ready==len(phrases),'interrupted':interrupted})
        if progress:progress(ready,len(phrases))
    try:
        futures=[executor.submit(speech.synthesize,text,voice,speed) for text in missing]
        pending=set(futures)
        while pending:
            if stop and stop.is_set():interrupted=True;break
            done,pending=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                try:future.result();completed+=1;consecutive_failures=0
                except Exception:failed+=1;consecutive_failures+=1
                checkpoint()
                if (completed+failed)%10==0 or completed+failed==len(missing):
                    print(f'Prepared {len(cached)+completed}/{len(phrases)} · unavailable {failed}',flush=True)
            if consecutive_failures>=3:
                print('Speech service is unavailable. Stopping this pass; rerun later to resume.',flush=True);break
    except KeyboardInterrupt:
        interrupted=True;print('\nAudio preparation stopped. Completed files remain available.',flush=True)
    finally:
        for future in futures:future.cancel()
        speech.close();executor.shutdown(wait=True,cancel_futures=True)
    ready=sum(speech.cached(text,voice,speed) is not None for text in phrases)
    report={'date_utc':datetime.now(timezone.utc).isoformat(),'voice':voice,'speed':speed,'recordings':len(phrases),
            'already_cached':len(cached),'prepared_now':completed,'ready':ready,'failures':failed,'interrupted':interrupted,
            'telegram_messages_sent':0,'source_version':manager.current.version,'complete':ready==len(phrases)}
    atomic_json(state.root/'audio-preparation.json',report)
    print(f'Ready: {ready}/{len(phrases)}. Report: {state.root / "audio-preparation.json"}',flush=True)
    return report
