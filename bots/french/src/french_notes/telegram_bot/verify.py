"""Corpus render audit, 10,000 real-handler interactions, and measurable evidence."""
from __future__ import annotations

import argparse
import asyncio
import copy
import html
import json
from pathlib import Path
import random
import socket
import statistics
import time
from unittest.mock import patch

from .guard import ReadOnlyGuard
from .library import Snapshot
from .offline import Harness
from .reader import Reader,chunks,entry_text

ROOT=Path(__file__).resolve().parents[1]


def rendered_coverage(snapshot,user):
    reader=Reader(snapshot)
    issues=[];pages=0
    for document in snapshot.documents:
        for record in document.records:
            if record.kind=='heading':
                section_screen=reader.render({'screen':'list','document':document.id,'section':record.section,'page':0},user)
                displayed=html.unescape(section_screen.text)
                for value in record.cells or (record.raw.lstrip('#').strip(),):
                    if value.strip() and value.strip() not in displayed:
                        issues.append(document.id+': heading field absent')
        for entry in document.entries:
            expected=entry_text(entry)
            texts=[]
            for part in range(len(chunks(expected))):
                screen=reader.render({'screen':'entry','entry':entry.id,'part':part},user)
                text=html.unescape(screen.continuation) if screen.continuation is not None else html.unescape(screen.text).split('\n\n',1)[1]
                texts.append(text);pages+=1
                if len(text.encode('utf-16-le'))//2>4096:issues.append(entry.id+': message too long')
                if not all(isinstance(label,str) and isinstance(action,dict) for row in screen.rows for label,action in row):issues.append(entry.id+': invalid keyboard')
            rendered=''.join(texts)
            if rendered!=expected:issues.append(entry.id+': missing or altered page text')
            # Independently check that every source field is present in the rendered
            # output, rather than merely comparing against the same formatter.
            for record in entry.records:
                for cell in record.cells or (record.raw,):
                    if cell.strip() and cell.strip() not in rendered:
                        issues.append(entry.id+': source field absent')
                if record.secondary and record.secondary not in rendered:issues.append(entry.id+': secondary tense absent')
    return {'entries':len(snapshot.entries),'rendered_pages':pages,'issues':issues,'passed':not issues}


def preview(snapshot,user,work):
    reader=Reader(snapshot)
    expression=next(d for d in snapshot.documents if d.id=='Expressions.txt')
    words=next(d for d in snapshot.documents if d.id=='@words')
    views=[('Main menu',{'screen':'menu'}),('Browse',{'screen':'browse'}),
           ('Words',{'screen':'list','document':words.id,'page':0}),
           ('Entry',{'screen':'entry','entry':words.entries[0].id}),
           ('Expression',{'screen':'entry','entry':expression.entries[0].id}),
           ('Settings',{'screen':'settings'}),('Access',{'screen':'access'})]
    cards=[]
    for title,view in views:
        screen=reader.render(view,user)
        buttons=''.join('<div class="buttons">'+''.join('<span>'+html.escape(label)+'</span>' for label,_ in row)+'</div>' for row in screen.rows)
        cards.append('<section><h2>'+title+'</h2><div class="phone"><div class="chat">'+html.escape('French_Learning')+'</div><article><div class="text" dir="auto">'+screen.text+'</div>'+buttons+'</article></div></section>')
    style='''body{font-family:Segoe UI,Tahoma,sans-serif;background:#e9eff1;color:#182a32;margin:24px}h1{font-size:22px}main{display:flex;gap:24px;flex-wrap:wrap}h2{font-size:16px}.phone{width:350px;min-height:500px;background:#dfe9df;padding:12px;border:1px solid #9ab0a9;border-radius:20px}.chat{background:white;padding:16px;border-radius:12px;margin-bottom:12px;font-weight:600}article{background:white;padding:14px;border-radius:12px}.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}.buttons{display:flex;gap:5px;margin-top:6px}.buttons span{flex:1;background:#eef5f9;color:#205778;text-align:center;padding:10px 6px;border-radius:6px;font-size:13px}'''
    page='<!doctype html><html lang="en"><meta charset="utf-8"><title>Offline Telegram screen previews</title><style>'+style+'</style><h1>Offline screen previews — simulated, not Telegram phone verification</h1><main>'+''.join(cards)+'</main></html>'
    (work/'screen-preview.html').write_text(page,'utf-8')


async def simulate(work,baseline,count=10000):
    snapshot=Snapshot.loads(baseline.read_text('utf-8'))
    notes=work/'simulation-notes';notes.mkdir(parents=True,exist_ok=True)
    for source in snapshot.sources:(notes/source.name).write_bytes(source.raw)
    harness=await Harness(notes,work/'simulation-state').start()
    snapshot=harness.manager.current
    guard=ReadOnlyGuard(notes).install()
    try:
        user=harness.state.user(1)
        corpus=rendered_coverage(snapshot,user)
        (work/'rendered-coverage.json').write_text(json.dumps(corpus,indent=2),'utf-8')
        if not corpus['passed']:raise AssertionError(json.dumps(corpus))
        preview(snapshot,user,work)
        _=snapshot.search_index;_ = snapshot.entry_map
        timings=[]
        reader=Reader(snapshot)
        for i in range(500):
            start=time.perf_counter()
            reader.render({'screen':'results','query':('etre','فارسی','English','de','avoir')[i%5],'page':i%4},user)
            timings.append((time.perf_counter()-start)*1000)
        benchmark={'samples':len(timings),'p50_ms':statistics.median(timings),
                   'p95_ms':sorted(timings)[int(len(timings)*.95)-1],'scope':'Warmed pure search and render; excludes Telegram and speech'}
        before={p.name:p.read_bytes() for p in notes.glob('*.txt')}
        for uid in range(2,21):harness.reader(uid)
        progress=0
        async def journey(uid):
            nonlocal progress
            rng=random.Random(20261110+uid)
            for step in range(count//20):
                # Deliberately inject API failures, not just ordinary tap traffic.
                if step and step%83==0:
                    current=harness.state.session(uid)['anchor']
                    if current:harness.request.messages.pop((uid,current),None)
                if step and step%127==0:
                    harness.request.failures['editmessagetext'].append({'error_code':503,'description':'Injected unavailable'})
                if step%19==0:
                    await harness.message('/menu',uid)
                elif step%19==1:
                    await harness.message('/search',uid)
                elif step%19==2:
                    await harness.message(rng.choice(['avoir','etre','de','سلام','English','unmatchedXYZ']),uid)
                else:
                    choices=[b for b in harness.buttons(uid) if b['text'] not in ('Create invitation','Listen') and not b['text'].startswith(('Listen ','Cancel invitation','Confirm revoke'))]
                    if choices:
                        chosen=rng.choice(choices)
                        await harness.callback(chosen['callback_data'],uid)
                    else:await harness.message('/menu',uid)
                message=harness.current_message(uid)
                if message:
                    text=html.unescape(message.get('text',''))
                    if len(text.encode('utf-16-le'))//2>4096:raise AssertionError('Oversized Telegram message')
                progress+=1
                if progress%1000==0:print('Simulated interactions:',progress,flush=True)
        started=time.perf_counter()
        with patch.object(socket.socket,'connect',side_effect=AssertionError('External network forbidden')):
            await asyncio.gather(*(journey(uid) for uid in range(1,21)))
        after={p.name:p.read_bytes() for p in notes.glob('*.txt')}
        if before!=after or guard.rejected:raise AssertionError('Source write boundary was violated.')
        if harness.service.errors:raise AssertionError('Reader errors: '+str(harness.service.errors))
        result={'passed':True,'seed':20261110,'readers':20,'interactions':progress,'seconds':time.perf_counter()-started,
                'fake_api_calls':len(harness.request.calls),'source_writes':len(guard.rejected),'reader_errors':harness.service.errors,'benchmark':benchmark,
                'rendered_coverage':corpus,'real_telegram':'Pending: no real token used','phone':'Pending: requires actual phone'}
        (work/'simulation.json').write_text(json.dumps(result,indent=2),'utf-8')
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return result
    finally:
        guard.active=False
        await harness.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work-dir',required=True);parser.add_argument('--count',type=int,default=10000)
    args=parser.parse_args()
    work=Path(args.work_dir)
    asyncio.run(simulate(work,work/'baseline-snapshot.json',args.count))


if __name__=='__main__':main()
