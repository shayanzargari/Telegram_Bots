"""Transactional intent and operation acceptance; no process/network waits here."""
from __future__ import annotations
import contextlib,hashlib,json,sqlite3,threading,time,uuid
from pathlib import Path
from bot_center_runtime.paths import Installation
from bot_center_runtime.redaction import redact,TOKEN

class CommandError(RuntimeError):pass
class Busy(CommandError):pass
class PersistenceError(CommandError):pass
class OperationConflict(CommandError):pass

LIFECYCLE={'start','stop','restart','retry','enable','disable'}
PRIORITY={'stop','disable','stop_all','quit'}
TERMINAL={'Completed','Failed','Blocked','Superseded','Interrupted','Indeterminate','Skipped'}

class CommandService:
    def __init__(self,root,database=None,recover=True):
        self.root=Path(root).resolve();self.installation=Installation(self.root);self.lock=threading.RLock();self.persistence_healthy=True
        self.launch_barrier=False;self.capture_barrier=False;self.scheduler=None;self.wake=None;self.quit_requested=False
        path=Path(database) if database else self.root/'data/core/core.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=2,isolation_level=None,check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,desired TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0,attention TEXT,external_hold INTEGER NOT NULL DEFAULT 0,
          observed TEXT NOT NULL DEFAULT 'Stopped',run_id TEXT,last_error TEXT,retry_at REAL,budget_start REAL NOT NULL DEFAULT 0,created REAL,updated REAL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,target TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,
          status TEXT NOT NULL,revision INTEGER,result TEXT NOT NULL DEFAULT '{}',created REAL,updated REAL);
        CREATE TABLE IF NOT EXISTS failure_history(run_id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,at REAL NOT NULL,category TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS core_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise PersistenceError('Core state integrity failed; new work is blocked.')
        if recover:self.recover_operations()
    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE');yield self.db;self.db.execute('COMMIT')
            except sqlite3.Error as error:
                if self.db.in_transaction:self.db.execute('ROLLBACK')
                self.persistence_healthy=False
                raise PersistenceError('Core persistence failed; new launches are suspended. Immediate shutdown remains available.') from error
            except BaseException:
                if self.db.in_transaction:self.db.execute('ROLLBACK')
                raise
    def enroll(self,manifest,enabled=False,desired='stopped'):
        with self.transaction() as db:
            now=time.time();db.execute('INSERT OR IGNORE INTO bots(id,enabled,desired,created,updated) VALUES(?,?,?,?,?)',(manifest['id'],int(enabled),desired,now,now))
        return self.bot(manifest['id'])
    def bot(self,target):
        with self.lock:
            row=self.db.execute('SELECT * FROM bots WHERE id=?',(target,)).fetchone()
            if row is None:raise CommandError('Bot is not enrolled.')
            return dict(row)
    def bots(self):
        with self.lock:return [dict(row) for row in self.db.execute('SELECT * FROM bots ORDER BY created,id')]
    def operation(self,operation_id):
        with self.lock:
            row=self.db.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
            if row is None:raise CommandError('Unknown operation.')
            result=dict(row);result['payload']=json.loads(result['payload']);result['result']=json.loads(result['result']);return result
    def operations(self,limit=50):
        with self.lock:ids=[row[0] for row in self.db.execute('SELECT id FROM operations ORDER BY created DESC LIMIT ?',(min(limit,500),))]
        return [self.operation(op) for op in ids]
    def pending_operations(self):
        with self.lock:ids=[row[0] for row in self.db.execute("SELECT id FROM operations WHERE status IN ('Accepted','Running') ORDER BY created")]
        return [self.operation(op) for op in ids]
    def latest_lifecycle(self,target,revision):
        with self.lock:
            row=self.db.execute("SELECT action FROM operations WHERE target=? AND revision=? AND action IN ('start','stop','restart','retry','enable','disable') ORDER BY created DESC LIMIT 1",(target,revision)).fetchone()
            return row[0] if row else None
    def submit(self,target,action,payload=None,operation_id=None,schedule_claim=None):
        if not isinstance(target,str) or not isinstance(action,str):raise CommandError('Operation target and action must be strings.')
        if TOKEN.search(target) or TOKEN.search(action) or (isinstance(operation_id,str) and TOKEN.search(operation_id)):
            raise CommandError('Credential-like request metadata is rejected without being saved.')
        if len(target)>64 or not (target=='*' or __import__('re').fullmatch(r'[a-z][a-z0-9_-]{0,63}',target)):raise CommandError('Invalid operation target.')
        payload={} if payload is None else payload
        if not isinstance(payload,dict):raise CommandError('Operation payload must be an object.')
        canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        if TOKEN.search(canonical) or any(key in canonical.casefold() for key in ('"bot_token"','"password"','"credential"','"secret"')):
            raise CommandError('Credentials are accepted only through masked local setup; this request was not saved.')
        if len(canonical)>32768:raise CommandError('Operation payload is too large.')
        if action not in LIFECYCLE|{'start_all','stop_all','quit','job','backup','open_editor'}:raise CommandError('Unsupported operation.')
        operation_id=operation_id or uuid.uuid4().hex
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=160:raise CommandError('Invalid operation identity.')
        with self.transaction() as db:
            old=db.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
            if old:
                if (old['target'],old['action'],old['payload'])!=(target,action,canonical):raise OperationConflict('Operation ID was already used with a different request.')
                return self.operation(operation_id)
            if self.capture_barrier:raise Busy('Consistent capture is in progress; this request is not yet accepted.')
            if self.launch_barrier and action not in {'stop','disable','stop_all','quit'}:raise Busy('Core is shutting down; new work is blocked.')
            if action=='backup' and db.execute("SELECT 1 FROM operations WHERE action='backup' AND status IN ('Accepted','Running')").fetchone():raise Busy('A consistent backup operation is already active.')
            if schedule_claim:
                from .scheduler import Scheduler
                Scheduler.validate_claim(db,schedule_claim)
            if action in {'stop_all','quit'}:
                db.execute("UPDATE operations SET status='Superseded',updated=?,result=? WHERE action='start_all' AND status IN ('Accepted','Running')",(time.time(),json.dumps({'by':operation_id}),))
            ids=[r['id'] for r in self.bots()] if action in {'start_all','stop_all'} else [target]
            revision=None;now=time.time();blocked={}
            for identity in ids:
                if action in {'quit','backup','open_editor'}:break
                bot=self.bot(identity)
                if action=='start_all' and (not bot['enabled'] or bot['attention']):
                    blocked[identity]=bot['attention'] or 'Disabled';continue
                pending=db.execute("SELECT action,payload FROM operations WHERE target=? AND status IN ('Accepted','Running')",(identity,)).fetchall()
                if action=='start_all' and pending and any(r['action'] not in {'start','retry'} for r in pending):
                    blocked[identity]='Busy: a conflicting bot operation is active';continue
                if action not in PRIORITY:
                    if action=='job' and pending:raise Busy('This bot has an active operation.')
                    if action in {'start','restart','retry'} and pending and any(r['action'] not in {'start','retry'} or action not in {'start','retry'} for r in pending):raise Busy('A conflicting bot operation is active.')
                    if action in {'start','restart','retry','start_all'} and not bot['enabled']:raise CommandError('Enable this bot first; Enable leaves it stopped.')
                    if action in {'start','restart','start_all'} and bot['attention']:raise CommandError('This bot needs attention. Resolve the issue and use Retry.')
                    if action=='job':
                        manifest=self.installation.bot(identity)
                        jobs={job['id']:job for job in manifest.get('jobs',[])}
                        if payload.get('job') not in jobs:raise CommandError('Only declared maintenance jobs may be executed.')
                if action in LIFECYCLE|{'start_all','stop_all'}:
                    revision=bot['revision']+1
                    desired=bot['desired'] if action=='retry' else ('stopped' if action in {'stop','disable','enable','stop_all'} else 'running')
                    enabled=0 if action=='disable' else (1 if action=='enable' else bot['enabled'])
                    clear=action in {'start','restart','retry','start_all'}
                    db.execute('UPDATE bots SET enabled=?,desired=?,revision=?,external_hold=?,attention=?,retry_at=NULL,budget_start=?,updated=? WHERE id=?',
                               (enabled,desired,revision,0 if clear else bot['external_hold'],None if action=='retry' else bot['attention'],now if action=='retry' else bot['budget_start'],now,identity))
                    db.execute("UPDATE operations SET status='Superseded',updated=?,result=? WHERE target=? AND status IN ('Accepted','Running') AND action IN ('start','restart','retry','stop','enable','disable')",(now,json.dumps({'by':operation_id}),identity))
                    if action in PRIORITY:
                        db.execute("UPDATE operations SET status='Superseded',updated=?,result=? WHERE target=? AND action='job' AND status IN ('Accepted','Running')",(now,json.dumps({'by':operation_id,'message':'Cancellation requested; owned resources remain reserved until the worker exits.'}),identity))
            db.execute('INSERT INTO operations(id,target,action,payload,status,revision,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(operation_id,target,action,canonical,'Accepted',revision,json.dumps({'blocked':blocked}) if blocked else '{}',now,now))
        if action=='quit':self.launch_barrier=True;self.quit_requested=True
        if self.wake:self.wake()
        return self.operation(operation_id)
    def update_operation(self,identity,status,result=None):
        with self.transaction() as db:
            db.execute('UPDATE operations SET status=?,result=?,updated=? WHERE id=? AND status NOT IN (\'Superseded\',\'Indeterminate\')',(status,json.dumps(result or {},ensure_ascii=False),time.time(),identity))
    def observed(self,identity,state,**fields):
        allowed={'run_id','last_error','attention','external_hold','retry_at'}
        if set(fields)-allowed:raise ValueError('Unsupported observed field.')
        with self.transaction() as db:
            columns=['observed=?','updated=?']+[k+'=?' for k in fields]
            values=[state,time.time()]+[redact(v) if isinstance(v,str) else v for v in fields.values()]+[identity]
            db.execute('UPDATE bots SET '+','.join(columns)+' WHERE id=?',values)
    def mark_satisfied(self,identity,revision,state):
        actions={'Ready':('start','restart','retry'),'Stopped':('stop','disable','enable','retry')}.get(state,())
        if not actions:return
        with self.transaction() as db:
            db.execute('UPDATE operations SET status=\'Completed\',result=?,updated=? WHERE target=? AND revision=? AND action IN ('+','.join('?' for _ in actions)+') AND status IN (\'Accepted\',\'Running\')',(json.dumps({'observed':state}),time.time(),identity,revision,*actions))
    def record_failure(self,identity,run_id,category,message):
        now=time.time();terminal=category in {'credentials','identity','conflict','configuration'}
        with self.transaction() as db:
            bot=self.bot(identity);retry=None;attention=None
            if terminal:attention=redact(message)
            else:
                db.execute('INSERT OR IGNORE INTO failure_history VALUES(?,?,?,?)',(run_id,identity,now,category))
                count=db.execute('SELECT COUNT(*) FROM failure_history WHERE bot_id=? AND at>=?',(identity,max(now-600,bot['budget_start']))).fetchone()[0]
                if count>=5:attention='Five unexpected failures in ten minutes. Resolve the issue and use Retry.'
                else:retry=now+(2,5,15,30)[max(0,count-1)]
            db.execute('UPDATE bots SET observed=?,last_error=?,attention=?,retry_at=?,run_id=NULL,updated=? WHERE id=?',('Needs attention' if attention else 'Waiting to retry',redact(message),attention,retry,now,identity))
            db.execute("UPDATE operations SET status='Failed',result=?,updated=? WHERE target=? AND status IN ('Accepted','Running')",(json.dumps({'category':category,'message':redact(message)}),now,identity))
        return retry
    def recover_operations(self):
        with self.transaction() as db:
            db.execute("UPDATE operations SET status=CASE WHEN action IN ('job','backup') THEN 'Indeterminate' ELSE 'Interrupted' END,result=?,updated=? WHERE status IN ('Accepted','Running')",(json.dumps({'reason':'Core interrupted before completion; latest persisted intent is authoritative.'}),time.time()))
            db.execute("UPDATE bots SET observed=CASE WHEN run_id IS NOT NULL THEN 'Interrupted by core loss' WHEN retry_at IS NOT NULL THEN 'Waiting to retry' ELSE 'Stopped' END,retry_at=CASE WHEN run_id IS NULL THEN retry_at ELSE NULL END,run_id=NULL")
    def close(self):self.db.close()
