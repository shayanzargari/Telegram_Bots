"""Durable UTC occurrence claims. Only the command service accepts work.

Every occurrence is consumed once, independently of schedule revisions. A
dispatcher crash cannot turn an old claim into fresh intended operation.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone
import json
import math
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo
from bot_center_runtime.redaction import TOKEN

UTC = timezone.utc


class ScheduleInvalidated(ValueError):
    """A claim lost its enabled revision before command acceptance."""


def utc(value=None):
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        try:value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:raise ValueError('Schedule instant must be a valid timezone-aware ISO datetime.') from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Use an explicit UTC instant or a timezone-aware datetime.')
    return value.astimezone(UTC)


def instant(value):
    return utc(value).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError('Schedule specification must be an object.')
    spec = dict(spec)
    kind = spec.get('kind')
    if kind == 'once':
        if not spec.get('at'):raise ValueError('One-time schedule needs an explicit UTC instant.')
        spec = {'kind':kind, 'at':instant(spec.get('at'))}
    elif kind == 'interval':
        if not spec.get('anchor'):raise ValueError('Interval schedule needs an explicit fixed anchor.')
        seconds = spec.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 1:
            raise ValueError('Interval seconds must be a finite number of at least one second.')
        spec = {'kind':kind, 'anchor':instant(spec.get('anchor')), 'seconds':float(seconds)}
    elif kind in {'daily','weekly'}:
        weekday = spec.get('weekday')
        zone = spec.get('timezone', 'America/New_York')
        try:ZoneInfo(zone)  # tzdata is installed explicitly in the core environment.
        except (KeyError,ValueError,TypeError):raise ValueError('Schedule timezone must be an installed IANA timezone name.') from None
        text = spec.get('time', '')
        try:
            hour, minute = (int(v) for v in text.split(':'))
            day_time(hour,minute)
        except (TypeError,ValueError):
            raise ValueError('Daily/weekly time must be HH:MM.') from None
        spec = {'kind':kind,'timezone':zone,'time':f'{hour:02}:{minute:02}'}
        if kind == 'weekly':
            if isinstance(weekday, bool) or not isinstance(weekday, int) or weekday not in range(7):
                raise ValueError('Weekly weekday is 0 (Monday) through 6 (Sunday).')
            spec['weekday'] = weekday
    else:
        raise ValueError('Supported schedules: once, interval, daily, weekly.')
    return spec


def normalize_spec(spec):
    return validate_spec(spec)


def validate_payload(payload):
    if payload is None:return {}
    if not isinstance(payload,dict):raise ValueError('Schedule payload must be an object.')
    forbidden={'token','bot_token','password','secret','credential','api_key','access_token','authorization'}
    def inspect(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if not isinstance(key,str) or key.casefold().replace('-','_') in forbidden:
                    raise ValueError('Schedule payload contains a protected credential field; use local credential setup.')
                inspect(child)
        elif isinstance(value,list):
            for child in value:inspect(child)
    inspect(payload)
    try:encoded=json.dumps(payload,sort_keys=True,allow_nan=False,ensure_ascii=False)
    except (TypeError,ValueError):raise ValueError('Schedule payload must contain ordinary finite JSON values.') from None
    if len(encoded.encode('utf-8'))>32768:raise ValueError('Schedule payload is too large.')
    if TOKEN.search(encoded):raise ValueError('Schedule payload contains credential-like content; use local credential setup.')
    return json.loads(encoded)


def due_between(spec, lower, upper):
    """Yield UTC datetimes strictly after lower and at/before upper.

    ZoneInfo round trips reject spring gaps. fold=0 deliberately chooses the
    first occurrence of a repeated autumn wall time.
    """
    lower,upper = utc(lower),utc(upper)
    if upper <= lower:
        return
    if spec['kind'] == 'once':
        due=utc(spec['at'])
        if lower < due <= upper:yield due
    elif spec['kind'] == 'interval':
        anchor=utc(spec['anchor']);seconds=spec['seconds']
        index=max(0,math.floor((lower-anchor).total_seconds()/seconds)+1)
        while True:
            due=anchor+timedelta(seconds=index*seconds)
            if due>upper:break
            if due>lower:yield due
            index+=1
    else:
        zone=ZoneInfo(spec['timezone']);hour,minute=map(int,spec['time'].split(':'))
        day=lower.astimezone(zone).date()-timedelta(days=1)
        last=upper.astimezone(zone).date()+timedelta(days=1)
        while day<=last:
            if spec['kind']=='daily' or day.weekday()==spec['weekday']:
                wall=datetime.combine(day,day_time(hour,minute))
                local=wall.replace(tzinfo=zone,fold=0)
                due=local.astimezone(UTC)
                if due.astimezone(zone).replace(tzinfo=None)==wall and lower<due<=upper:
                    yield due
            day+=timedelta(days=1)


class Scheduler:
    tick_seconds=1.0
    active_gap_seconds=5.0
    lateness_seconds=30.0

    def __init__(self,service):
        self.service=service
        self.db=service.db
        self.lock=service.lock
        self._last_wall=None
        self._last_mono=None
        self._closed=False
        with self.lock:
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS schedules(
                id TEXT PRIMARY KEY,target TEXT NOT NULL,action TEXT NOT NULL,
                payload TEXT NOT NULL,spec TEXT NOT NULL,revision INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,boundary REAL NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS schedule_occurrences(
                schedule_id TEXT NOT NULL,due_utc TEXT NOT NULL,revision INTEGER NOT NULL,
                status TEXT NOT NULL,reason TEXT,operation_id TEXT NOT NULL,
                claimed_at REAL NOT NULL,updated REAL NOT NULL,
                PRIMARY KEY(schedule_id,due_utc));
              CREATE TABLE IF NOT EXISTS scheduler_runtime(
                id INTEGER PRIMARY KEY CHECK(id=1),last_wall REAL NOT NULL);
            ''')
        with self.transaction() as db:
            # A pre-acceptance claim may have unknown effects if an older core did
            # not update the occurrence atomically. Never automatically reissue it.
            db.execute("UPDATE schedule_occurrences SET status='Indeterminate',reason='interrupted before dispatch reconciliation',updated=? WHERE status='Claimed'",(time.time(),))

    @contextmanager
    def transaction(self):
        if callable(getattr(self.service,'transaction',None)):
            with self.service.transaction() as db:yield db
            return
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def create(self,target,action,spec,payload=None,schedule_id=None,now=None):
        payload=validate_payload(payload)
        if action not in {'start','stop','restart','job','retry'}:
            raise ValueError('Schedules support lifecycle actions and registered jobs.')
        if not isinstance(target,str) or not target:
            raise ValueError('Schedule needs a bot target.')
        if callable(getattr(self.service,'bot',None)):
            self.service.bot(target)
        if action=='job' and callable(getattr(getattr(self.service,'installation',None),'bot',None)):
            manifest=self.service.installation.bot(target)
            if (payload or {}).get('job') not in {job['id'] for job in manifest.get('jobs',[])}:
                raise ValueError('Schedule may invoke only a declared maintenance job.')
        normalized=normalize_spec(spec);stamp=utc(now).timestamp()
        schedule_id=schedule_id or uuid.uuid4().hex
        if not isinstance(schedule_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}',schedule_id):
            raise ValueError('Invalid schedule identity.')
        with self.transaction() as db:
            db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,1,0,?,?,?)',
                (schedule_id,target,action,json.dumps(payload,sort_keys=True),json.dumps(normalized,sort_keys=True),stamp,stamp,stamp))
        return self.get(schedule_id)

    def get(self,schedule_id):
        with self.lock:
            row=self.db.execute('SELECT * FROM schedules WHERE id=?',(schedule_id,)).fetchone()
        if row is None:raise KeyError(schedule_id)
        result=dict(row)
        result['payload']=json.loads(result['payload']);result['spec']=json.loads(result['spec']);result['enabled']=bool(result['enabled'])
        return result

    def list(self):
        with self.lock:ids=[r[0] for r in self.db.execute('SELECT id FROM schedules ORDER BY created,id')]
        results=[self.get(key) for key in ids]
        for result in results:result['next_due_utc']=self.next_due(result)
        return results

    def next_due(self,schedule_or_id,now=None):
        schedule=self.get(schedule_or_id) if isinstance(schedule_or_id,str) else schedule_or_id
        if not schedule.get('enabled'):return None
        threshold=max(utc(now).timestamp(),schedule['boundary']);spec=schedule['spec']
        if spec['kind']=='once':
            due=utc(spec['at']);return instant(due) if due.timestamp()>threshold else None
        if spec['kind']=='interval':
            anchor=utc(spec['anchor']);seconds=spec['seconds']
            index=max(0,math.floor((threshold-anchor.timestamp())/seconds)+1)
            return instant(anchor+timedelta(seconds=index*seconds))
        start=utc(threshold)
        due=next(due_between(spec,start,start+timedelta(days=15)),None)
        return instant(due) if due is not None else None

    def occurrences(self,schedule_id=None):
        with self.lock:
            query='SELECT * FROM schedule_occurrences'
            args=()
            if schedule_id is not None:query+=' WHERE schedule_id=?';args=(schedule_id,)
            return [dict(row) for row in self.db.execute(query+' ORDER BY due_utc,schedule_id',args)]

    def edit(self,schedule_id,spec=None,payload=None,enabled=None,now=None):
        stamp=utc(now).timestamp()
        if payload is not None:payload=validate_payload(payload)
        normalized=normalize_spec(spec) if spec is not None else None
        with self.transaction() as db:
            row=db.execute('SELECT * FROM schedules WHERE id=?',(schedule_id,)).fetchone()
            if row is None:raise KeyError(schedule_id)
            boundary=max(row['boundary'],stamp)
            chosen=json.loads(row['spec']) if normalized is None else normalized
            new_enabled=row['enabled'] if enabled is None else int(bool(enabled))
            db.execute('UPDATE schedules SET spec=?,payload=?,enabled=?,revision=revision+1,boundary=?,updated=? WHERE id=?',
                (json.dumps(chosen,sort_keys=True),row['payload'] if payload is None else json.dumps(payload,sort_keys=True),new_enabled,boundary,stamp,schedule_id))
            db.execute("UPDATE schedule_occurrences SET status='Skipped',reason='schedule disabled or edited before acceptance',updated=? WHERE schedule_id=? AND status='Claimed'",(stamp,schedule_id))
            if chosen['kind']=='once' and utc(chosen['at']).timestamp()<=boundary:
                self._record(db,schedule_id,utc(chosen['at']),row['revision']+1,'Skipped','expired before activation',stamp)
        return self.get(schedule_id)

    def _record(self,db,key,due,revision,status,reason,stamp):
        due_text=instant(due)
        op_id='schedule-'+uuid.uuid5(uuid.NAMESPACE_URL,key+'|'+due_text).hex
        cursor=db.execute('INSERT OR IGNORE INTO schedule_occurrences VALUES(?,?,?,?,?,?,?,?)',
            (key,due_text,revision,status,reason,op_id,stamp,stamp))
        if cursor.rowcount==0:return None
        return {'schedule_id':key,'due_utc':due_text,'revision':revision,'operation_id':op_id}

    def claim_due(self,now=None,monotonic=None):
        wall=utc(now);stamp=wall.timestamp();mono=time.monotonic() if monotonic is None else monotonic
        interrupted=self._last_wall is None
        if not interrupted:
            wall_delta=stamp-self._last_wall
            mono_delta=mono-self._last_mono
            interrupted=mono_delta<0 or mono_delta>self.active_gap_seconds or abs(wall_delta-mono_delta)>2
        claims=[]
        with self.transaction() as db:
            rows=db.execute('SELECT * FROM schedules ORDER BY id').fetchall()
            for row in rows:
                if stamp<=row['boundary']:continue
                spec=json.loads(row['spec'])
                for due in due_between(spec,row['boundary'],wall):
                    if not row['enabled']:status,reason='Skipped','disabled'
                    elif interrupted:status,reason='Skipped','core downtime or suspension'
                    elif stamp-due.timestamp()>self.lateness_seconds:status,reason='Skipped','late'
                    else:status,reason='Claimed',None
                    claim=self._record(db,row['id'],due,row['revision'],status,reason,stamp)
                    if claim and status=='Claimed':
                        claim.update(target=row['target'],action=row['action'],payload=json.loads(row['payload']))
                        claims.append(claim)
                db.execute('UPDATE schedules SET boundary=MAX(boundary,?),updated=? WHERE id=?',(stamp,stamp,row['id']))
            db.execute('INSERT OR REPLACE INTO scheduler_runtime VALUES(1,?)',(stamp,))
        self._last_wall=max(stamp,self._last_wall or stamp);self._last_mono=mono
        return claims

    @staticmethod
    def validate_claim(db,claim):
        """Called within CommandService's acceptance transaction, never separately."""
        schedule=db.execute('SELECT enabled,revision FROM schedules WHERE id=?',(claim['schedule_id'],)).fetchone()
        occurrence=db.execute('SELECT status,revision,operation_id FROM schedule_occurrences WHERE schedule_id=? AND due_utc=?',
            (claim['schedule_id'],claim['due_utc'])).fetchone()
        if schedule is None or not schedule['enabled'] or schedule['revision']!=claim['revision'] or occurrence is None or occurrence['status']!='Claimed' or occurrence['revision']!=claim['revision'] or occurrence['operation_id']!=claim['operation_id']:
            raise ScheduleInvalidated('Schedule claim was disabled, edited, or already consumed.')
        db.execute("UPDATE schedule_occurrences SET status='Accepted',reason=NULL,updated=? WHERE schedule_id=? AND due_utc=?",(time.time(),claim['schedule_id'],claim['due_utc']))

    def dispatch_claim(self,claim):
        try:
            result=self.service.submit(claim['target'],claim['action'],claim['payload'],operation_id=claim['operation_id'],schedule_claim=claim)
            status=str(result.get('status',''))
            if status.casefold() in {'busy','blocked','rejected','failed'}:
                self._skip(claim,'busy' if status.casefold()=='busy' else 'command '+status.casefold())
            return result
        except ScheduleInvalidated:
            self._skip(claim,'schedule disabled or edited before acceptance')
            return {'status':'Skipped','reason':'schedule invalidated'}
        except Exception as error:
            # Busy is a normal consumed occurrence; other errors are task faults
            # and must reach the core's essential task owner.
            if type(error).__name__.casefold() in {'busy','busyerror','commandbusy'}:
                self._skip(claim,'busy')
                return {'status':'Skipped','reason':'busy'}
            if type(error).__name__=='CommandError':
                self._skip(claim,'command rejected')
                return {'status':'Skipped','reason':'command rejected'}
            raise

    def _skip(self,claim,reason):
        with self.transaction() as db:
            db.execute("UPDATE schedule_occurrences SET status='Skipped',reason=?,updated=? WHERE schedule_id=? AND due_utc=? AND status='Claimed'",
                (reason,time.time(),claim['schedule_id'],claim['due_utc']))

    def tick(self,now=None,monotonic=None):
        if getattr(self.service,'capture_barrier',False) or getattr(self.service,'launch_barrier',False):
            # Paused dispatch does not claim work or write the captured database.
            # On resumption the first tick records the pause as downtime, so no
            # occurrence is queued for delayed execution behind the capture.
            self._last_wall=None;self._last_mono=None
            return []
        return [self.dispatch_claim(claim) for claim in self.claim_due(now,monotonic)]

    async def run(self):
        while not self._closed:
            self.tick()
            await asyncio.sleep(self.tick_seconds)

    def close(self):
        self._closed=True
