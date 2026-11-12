from __future__ import annotations
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from bot_center.scheduler import Scheduler,ScheduleInvalidated,due_between,normalize_spec,utc

UTC=timezone.utc
BASE=datetime(2026,11,11,12,tzinfo=UTC)


class Service:
    """Real SQLite acceptance boundary with fake process/job effects."""
    def __init__(self,path=':memory:'):
        self.db=sqlite3.connect(path,isolation_level=None,check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock();self.accepted=[];self.busy=False
        self.db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,target TEXT,action TEXT,payload TEXT,status TEXT)')

    def submit(self,target,action,payload=None,operation_id=None,schedule_claim=None):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if self.busy:
                    self.db.rollback();return {'status':'Busy'}
                Scheduler.validate_claim(self.db,schedule_claim)
                self.db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(operation_id,target,action,json.dumps(payload),'Accepted'))
                self.db.commit()
            except BaseException:self.db.rollback();raise
        self.accepted.append((target,action,payload));return {'id':operation_id,'status':'Accepted'}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.service=Service();self.scheduler=Scheduler(self.service)
        self.scheduler.tick(BASE,monotonic=0)

    def tearDown(self):self.service.db.close()

    def schedule(self,spec=None):
        result=self.scheduler.create('fixture','start',spec or {'kind':'interval','anchor':BASE.isoformat(),'seconds':2},now=BASE)
        return result['id']

    def enable(self,key,now=BASE):self.scheduler.edit(key,enabled=True,now=now)

    def test_create_disabled_enable_changes_no_intent_and_no_execution(self):
        key=self.schedule();self.assertFalse(self.scheduler.get(key)['enabled'])
        self.enable(key);self.assertEqual(self.service.accepted,[])
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.assertEqual(len(self.service.accepted),1)
        self.assertEqual(self.scheduler.occurrences(key)[0]['status'],'Accepted')

    def test_busy_occurrence_is_consumed_with_no_delayed_execution(self):
        key=self.schedule();self.enable(key);self.service.busy=True
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        occurrence=self.scheduler.occurrences(key)[0]
        self.assertEqual((occurrence['status'],occurrence['reason']),('Skipped','busy'))
        self.service.busy=False
        self.scheduler.tick(BASE+timedelta(seconds=3),monotonic=3)
        self.assertEqual(self.service.accepted,[])

    def test_disable_between_claim_and_acceptance_invalidates(self):
        key=self.schedule();self.enable(key)
        claim=self.scheduler.claim_due(BASE+timedelta(seconds=2),monotonic=2)[0]
        self.scheduler.edit(key,enabled=False,now=BASE+timedelta(seconds=2))
        self.scheduler.dispatch_claim(claim)
        self.assertEqual(self.service.accepted,[])
        self.assertEqual(self.scheduler.occurrences(key)[0]['status'],'Skipped')

    def test_edit_between_claim_and_acceptance_invalidates(self):
        key=self.schedule();self.enable(key)
        claim=self.scheduler.claim_due(BASE+timedelta(seconds=2),monotonic=2)[0]
        self.scheduler.edit(key,spec={'kind':'interval','anchor':BASE.isoformat(),'seconds':10},now=BASE+timedelta(seconds=2))
        self.scheduler.dispatch_claim(claim)
        self.assertEqual(self.service.accepted,[])

    def test_acceptance_before_disable_remains_visible(self):
        key=self.schedule();self.enable(key)
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.scheduler.edit(key,enabled=False,now=BASE+timedelta(seconds=2))
        self.assertEqual(self.scheduler.occurrences(key)[0]['status'],'Accepted')
        self.assertEqual(self.service.db.execute('SELECT status FROM operations').fetchone()[0],'Accepted')

    def test_suspension_skips_before_lateness(self):
        key=self.schedule();self.enable(key)
        self.scheduler.tick(BASE+timedelta(seconds=8),monotonic=8)
        self.assertEqual(len(self.scheduler.occurrences(key)),4)
        self.assertTrue(all(o['reason']=='core downtime or suspension' for o in self.scheduler.occurrences(key)))
        self.assertEqual(self.service.accepted,[])

    def test_capture_pause_does_not_dispatch_and_resume_skips_paused_time(self):
        key=self.schedule();self.enable(key);self.service.capture_barrier=True
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.assertEqual(self.scheduler.occurrences(key),[])
        self.service.capture_barrier=False
        self.scheduler.tick(BASE+timedelta(seconds=3),monotonic=3)
        self.assertEqual(self.scheduler.occurrences(key)[0]['reason'],'core downtime or suspension')
        self.assertEqual(self.service.accepted,[])

    def test_wall_forward_jump_skips_even_if_monotonic_continuous(self):
        key=self.schedule();self.enable(key)
        self.scheduler.tick(BASE+timedelta(seconds=20),monotonic=1)
        self.assertEqual(self.service.accepted,[])
        self.assertTrue(all(o['status']=='Skipped' for o in self.scheduler.occurrences(key)))

    def test_wall_backward_jump_never_decreases_boundary_or_replays(self):
        key=self.schedule();self.enable(key)
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        boundary=self.scheduler.get(key)['boundary']
        self.scheduler.tick(BASE-timedelta(seconds=20),monotonic=3)
        self.assertEqual(self.scheduler.get(key)['boundary'],boundary)
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=4)
        self.assertEqual(len(self.service.accepted),1)

    def test_disabled_time_and_past_once_not_backfilled(self):
        key=self.schedule({'kind':'once','at':(BASE+timedelta(seconds=2)).isoformat()})
        self.enable(key,BASE+timedelta(seconds=4))
        self.scheduler.tick(BASE+timedelta(seconds=4),monotonic=4)
        self.assertEqual(self.service.accepted,[])
        self.assertEqual(self.scheduler.occurrences(key)[0]['reason'],'expired before activation')

    def test_future_edit_does_not_reexecute_same_due_across_revisions(self):
        key=self.schedule();self.enable(key)
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.scheduler.edit(key,spec={'kind':'interval','anchor':BASE.isoformat(),'seconds':2},now=BASE+timedelta(seconds=2))
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.assertEqual(len(self.service.accepted),1)
        self.assertEqual(len(self.scheduler.occurrences(key)),1)

    def test_unaccepted_claim_interrupted_is_indeterminate_not_reissued(self):
        key=self.schedule();self.enable(key)
        self.scheduler.claim_due(BASE+timedelta(seconds=2),monotonic=2)
        replacement=Scheduler(self.service)
        replacement.tick(BASE+timedelta(seconds=4),monotonic=4)
        entries=replacement.occurrences(key)
        self.assertEqual(entries[0]['status'],'Indeterminate')
        self.assertEqual(entries[1]['reason'],'core downtime or suspension')
        self.assertEqual(self.service.accepted,[])

    def test_persistence_survives_reopening(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'core.sqlite3';first=Service(path);scheduler=Scheduler(first)
            key=scheduler.create('fixture','stop',{'kind':'once','at':(BASE+timedelta(hours=1)).isoformat()},now=BASE)['id']
            scheduler.edit(key,enabled=True,now=BASE);first.db.close()
            second=Service(path)
            try:
                reopened=Scheduler(second);self.assertTrue(reopened.get(key)['enabled'])
                reopened.tick(BASE+timedelta(hours=2),monotonic=0)
                self.assertEqual(reopened.occurrences(key)[0]['status'],'Skipped')
            finally:second.db.close()

    def test_invalid_spec_does_not_change_database(self):
        for bad in ({'kind':'once'},{'kind':'interval','seconds':0},{'kind':'weekly','time':'12:00','weekday':True},{'kind':'daily','time':'25:00'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.schedule(bad)
        self.assertEqual(self.scheduler.list(),[])

    def test_credential_like_payload_create_and_edit_rejected_before_persistence(self):
        secret='123456789'+':'+'schedule_fixture_'+'D'*30
        for payload in ({'url':'https://api.telegram.org/bot'+secret+'/getMe'},{'nested':[{'password':'fixture'}]},{'secret':'fixture'}):
            with self.subTest(payload_kind=list(payload)),self.assertRaises(ValueError) as caught:
                self.scheduler.create('fixture','job',{'kind':'once','at':(BASE+timedelta(hours=1)).isoformat()},payload=payload,now=BASE)
            self.assertFalse(secret in str(caught.exception),'Schedule error echoed a canary.')
        self.assertEqual(self.scheduler.list(),[])
        key=self.schedule();before=self.scheduler.get(key)
        with self.assertRaises(ValueError):self.scheduler.edit(key,payload={'url':'/bot'+secret},now=BASE)
        self.assertEqual(self.scheduler.get(key),before)
        dump='\n'.join(self.service.db.iterdump())
        self.assertFalse(secret in dump,'A rejected schedule persisted a token canary.')

    def test_dst_spring_nonexistent_time_skipped(self):
        spec=normalize_spec({'kind':'daily','timezone':'America/New_York','time':'02:30'})
        result=list(due_between(spec,'2026-03-08T00:00:00Z','2026-03-09T00:00:00Z'))
        self.assertEqual(result,[])

    def test_dst_fall_repeated_time_first_occurrence_once(self):
        spec=normalize_spec({'kind':'daily','timezone':'America/New_York','time':'01:30'})
        result=list(due_between(spec,'2026-11-01T00:00:00Z','2026-11-02T00:00:00Z'))
        self.assertEqual([d.isoformat() for d in result],['2026-11-01T05:30:00+00:00'])

    def test_weekly_uses_explicit_timezone_and_weekday(self):
        spec=normalize_spec({'kind':'weekly','timezone':'America/New_York','time':'09:00','weekday':2})
        result=list(due_between(spec,'2026-11-09T00:00:00Z','2026-11-16T00:00:00Z'))
        self.assertEqual([d.isoformat() for d in result],['2026-11-11T14:00:00+00:00'])

    def test_fixed_anchor_interval_ignores_timezone(self):
        spec=normalize_spec({'kind':'interval','anchor':'2026-11-01T00:00:00Z','seconds':3600})
        result=list(due_between(spec,'2026-11-01T04:00:00Z','2026-11-01T07:00:00Z'))
        self.assertEqual(len(result),3)
        self.assertEqual([(result[i+1]-result[i]).total_seconds() for i in range(2)],[3600,3600])

    def test_next_due_disabled_expired_and_fixed_anchor_are_truthful(self):
        key=self.schedule();self.assertIsNone(self.scheduler.next_due(key,now=BASE))
        self.enable(key);self.assertEqual(self.scheduler.next_due(key,now=BASE),'2026-11-11T12:00:02.000000Z')
        once=self.schedule({'kind':'once','at':(BASE+timedelta(seconds=1)).isoformat()})
        self.enable(once);self.assertIsNone(self.scheduler.next_due(once,now=BASE+timedelta(seconds=2)))
        future=self.schedule({'kind':'interval','anchor':'2030-01-01T00:00:00Z','seconds':1})
        self.enable(future);self.assertEqual(self.scheduler.next_due(future,now=BASE),'2030-01-01T00:00:00.000000Z')

    def test_next_due_skips_dst_gap_and_selects_first_autumn_fold(self):
        gap_start=utc('2026-03-08T00:00:00Z')
        gap=self.scheduler.create('fixture','start',{'kind':'daily','time':'02:30','timezone':'America/New_York'},now=gap_start)['id']
        self.scheduler.edit(gap,enabled=True,now=gap_start)
        self.assertEqual(self.scheduler.next_due(gap,now=gap_start),'2026-03-09T06:30:00.000000Z')
        fold_start=utc('2026-11-01T00:00:00Z')
        fold=self.scheduler.create('fixture','start',{'kind':'daily','time':'01:30','timezone':'America/New_York'},now=fold_start)['id']
        self.scheduler.edit(fold,enabled=True,now=fold_start)
        self.assertEqual(self.scheduler.next_due(fold,now=fold_start),'2026-11-01T05:30:00.000000Z')
        self.assertEqual(self.scheduler.next_due(fold,now='2026-11-01T05:45:00Z'),'2026-11-02T06:30:00.000000Z')

    def test_next_due_never_crosses_consumed_boundary_after_backward_wall_time(self):
        key=self.schedule();self.enable(key);self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.assertEqual(self.scheduler.next_due(key,now=BASE-timedelta(days=1)),'2026-11-11T12:00:04.000000Z')


class CommandIntegrationTests(unittest.TestCase):
    def setUp(self):
        from bot_center.commands import CommandService
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        (self.root/'config').mkdir()
        manifest={'id':'fixture','schema_version':1,'contract_version':1,'python':'.venvs/fixture/Scripts/python.exe','cwd':'data/fixture','data_dir':'data/fixture','log_dir':'logs/fixture','jobs':[{'id':'slow-job'}]}
        (self.root/'config/registry.json').write_text(json.dumps({'schema':1,'bots':[manifest]}))
        self.commands=CommandService(self.root)
        self.commands.enroll(manifest,enabled=True,desired='stopped')
        self.scheduler=Scheduler(self.commands);self.commands.scheduler=self.scheduler
        self.scheduler.tick(BASE,monotonic=0)

    def tearDown(self):self.commands.close();self.temporary.cleanup()

    def make(self,action='start',payload=None):
        key=self.scheduler.create('fixture',action,{'kind':'interval','anchor':BASE.isoformat(),'seconds':2},payload=payload,now=BASE)['id']
        self.scheduler.edit(key,enabled=True,now=BASE)
        return key

    def test_real_command_acceptance_updates_intent_and_claim_atomically(self):
        key=self.make();self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.assertEqual(self.commands.bot('fixture')['desired'],'running')
        occurrence=self.scheduler.occurrences(key)[0]
        operation=self.commands.operation(occurrence['operation_id'])
        self.assertEqual((occurrence['status'],operation['status']),('Accepted','Accepted'))

    def test_real_job_busy_through_next_due_is_skipped_and_manual_distinct_request_busy(self):
        from bot_center.commands import Busy
        key=self.make('job',{'job':'slow-job'})
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        self.scheduler.tick(BASE+timedelta(seconds=4),monotonic=4)
        entries=self.scheduler.occurrences(key)
        self.assertEqual([(v['status'],v['reason']) for v in entries],[('Accepted',None),('Skipped','busy')])
        with self.assertRaises(Busy):self.commands.submit('fixture','job',{'job':'slow-job'})
        first=self.commands.operation(entries[0]['operation_id'])
        self.commands.submit('fixture','stop')
        self.assertEqual(self.commands.operation(first['id'])['status'],'Superseded')
        self.assertEqual(self.commands.bot('fixture')['desired'],'stopped')

    def test_normal_rejection_for_disabled_bot_is_consumed_without_scheduler_failure(self):
        key=self.make()
        operation=self.commands.submit('fixture','disable');self.commands.update_operation(operation['id'],'Completed')
        self.scheduler.tick(BASE+timedelta(seconds=2),monotonic=2)
        occurrence=self.scheduler.occurrences(key)[0]
        self.assertEqual((occurrence['status'],occurrence['reason']),('Skipped','command rejected'))
        self.assertTrue(self.commands.persistence_healthy)
        self.assertEqual(self.commands.bot('fixture')['desired'],'stopped')


if __name__=='__main__':unittest.main()
