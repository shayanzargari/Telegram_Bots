"""Consistently stage the initial application relocation without activating it."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,sqlite3,subprocess,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def semantic(path):
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        con.text_factory=bytes
        result={'integrity':con.execute('PRAGMA integrity_check').fetchone()[0].decode(),'tables':{}}
        for raw, in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            name=raw.decode();rows=con.execute('SELECT * FROM "'+name.replace('"','""')+'"').fetchall()
            lines=sorted(json.dumps(row,ensure_ascii=True,default=lambda b:b.hex()) for row in rows)
            result['tables'][name]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(lines).encode()).hexdigest()}
        return result
    finally:con.close()

def writers():
    script="Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'python|msedge|chrome|wscript|cscript'} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    raw=subprocess.check_output(['powershell','-NoProfile','-Command',script],text=True,creationflags=0x08000000)
    items=json.loads(raw) if raw.strip() else []
    if isinstance(items,dict):items=[items]
    needles=[str(ROOT/'French Notes').casefold(),str(ROOT/'Our_Home_Telegram_Bot').casefold(),'d:\\french notes\\app.py','french_notes.app','french_notes.managed','home_bot.managed']
    return [{'pid':p['ProcessId'],'name':p['Name']} for p in items if p['ProcessId']!=os.getpid() and any(n in (p.get('CommandLine') or '').casefold() for n in needles)]

def stage(destination):
    if writers():raise RuntimeError('Application/editor writers are active. Close their owned sessions normally before staging; no process was terminated.')
    if destination.exists():raise RuntimeError('Staging output already exists; use a fresh --output path.')
    destination.mkdir(parents=True);state=destination/'state';state.mkdir()
    maps=[('home',ROOT/'Our_Home_Telegram_Bot/data',state/'home'),
          ('notes',ROOT/'French Notes/french',state/'french/notes'),
          ('reader',Path(os.environ['LOCALAPPDATA'])/'French Notes Telegram',state/'french/reader'),
          ('editor',Path(os.environ['LOCALAPPDATA'])/'French Notes/afc704eab9aa',state/'french/editor')]
    report={'schema':1,'created':time.time(),'sources':[],'files':[],'database_semantics':[],'omissions':[]}
    for label,source,target in maps:
        if not source.is_dir():raise RuntimeError('Expected source state is missing; no empty state was initialized.')
        report['sources'].append({'owner':label,'old':str(source),'new':str(target.relative_to(destination))})
        for base,dirs,names in os.walk(source,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink()]
            for name in sorted(names):
                p=Path(base)/name;relative=p.relative_to(source);q=target/relative
                if p.is_symlink():raise RuntimeError('Unexpected state link; stop and reconcile inventory.')
                if name.endswith(('-wal','-shm')):
                    report['omissions'].append({'source':str(p),'reason':'SQLite backup API includes committed WAL state; transient sidecars are not copied.'});continue
                q.parent.mkdir(parents=True,exist_ok=True)
                if label=='home' and name=='config.json':
                    config=json.loads(p.read_text('utf-8'));config.pop('bot_token',None)
                    q.write_text(json.dumps(config,indent=2),encoding='utf-8')
                    report['files'].append({'owner':label,'source':str(p),'target':str(q.relative_to(destination)),'transform':'credential removed; imported separately into DPAPI by coordinator','sha256':sha(q)})
                    continue
                with p.open('rb') as stream:header=stream.read(16)
                if header==b'SQLite format 3\0':
                    original=semantic(p);src=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(q)
                    try:src.backup(dst)
                    finally:dst.close();src.close()
                    restored=semantic(q)
                    if restored!=original:raise RuntimeError('Database semantic snapshot differs from fresh source.')
                    report['database_semantics'].append({'owner':label,'source':str(p),'target':str(q.relative_to(destination)),'before':original,'after':restored,'passed':True})
                else:
                    before=sha(p);shutil.copy2(p,q)
                    if sha(q)!=before or sha(p)!=before:raise RuntimeError('State changed while staging; activation must remain blocked.')
                report['files'].append({'owner':label,'source':str(p),'target':str(q.relative_to(destination)),'sha256':sha(q),'bytes':q.stat().st_size})
    # Existing reader history, if present, must be reconciled rather than lost.
    reader_history=state/'french/reader/history.json';editor_history=state/'french/editor/history.json'
    if reader_history.exists():
        reader=json.loads(reader_history.read_text('utf-8'));editor=json.loads(editor_history.read_text('utf-8')) if editor_history.exists() else []
        by_id={x['id']:x for x in editor}
        for item in reader:
            if item['id'] in by_id and by_id[item['id']]!=item:raise RuntimeError('Divergent French history identity; application-aware review is required.')
            if item['id'] not in by_id:editor.append(item);by_id[item['id']]=item
        editor_history.write_text(json.dumps(editor,ensure_ascii=False,indent=2),encoding='utf-8')
        report['history_reconciliation']={'distinct_preserved':len(editor),'reader_entries':len(reader),'canonical':'data/french/editor/history.json'}
    else:report['history_reconciliation']={'distinct_reader_history':False,'canonical':'data/french/editor/history.json','editor_bytes_preserved':True}
    # All executable consumers get exact canonical paths; source notes stay byte-identical.
    report['note_hashes']={p.name:sha(p) for p in (state/'french/notes').glob('*.txt')}
    report['verified']=True
    (destination/'staging-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'stage':str(destination),'files':len(report['files']),'databases':len(report['database_semantics']),'notes':len(report['note_hashes']),'verified':True,'activated':False}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'work/migration/initial');args=parser.parse_args();stage(args.output.resolve())

if __name__=='__main__':main()
