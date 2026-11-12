import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from bot_center.commands import CommandService,Busy,OperationConflict,PersistenceError,CommandError

class Commands(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'config/registry.json').write_text('{"schema":1,"bots":[]}',encoding='utf-8')
        self.service=CommandService(self.root);self.service.enroll({'id':'alpha'},True,'running')
    def tearDown(self):self.service.close();self.temp.cleanup()
    def test_idempotency_includes_canonical_payload(self):
        first=self.service.submit('alpha','start',{'a':1,'b':2},'same')
        revision=self.service.bot('alpha')['revision']
        self.assertEqual(first,self.service.submit('alpha','start',{'b':2,'a':1},'same'))
        self.assertEqual(revision,self.service.bot('alpha')['revision'])
        for target,action,payload in [('alpha','stop',{'a':1,'b':2}),('alpha','start',{'a':3}),('other','start',{'a':1,'b':2})]:
            with self.assertRaises(OperationConflict):self.service.submit(target,action,payload,'same')
    def test_stop_supersedes_restart_and_cancels_future_launch(self):
        restart=self.service.submit('alpha','restart')
        stop=self.service.submit('alpha','stop')
        self.assertEqual('Accepted',stop['status']);self.assertEqual('Superseded',self.service.operation(restart['id'])['status'])
        self.assertEqual('stopped',self.service.bot('alpha')['desired'])
        self.assertGreater(stop['revision'],restart['revision'])
    def test_newer_stop_survives_reopen_without_replaying_restart(self):
        restart=self.service.submit('alpha','restart');stop=self.service.submit('alpha','stop')
        self.service.close();self.service=CommandService(self.root)
        self.assertEqual('stopped',self.service.bot('alpha')['desired']);self.assertEqual('Interrupted',self.service.operation(stop['id'])['status'])
        self.assertEqual('Superseded',self.service.operation(restart['id'])['status'])
    def test_restart_after_acceptance_crash_reconciles_running_intent(self):
        op=self.service.submit('alpha','restart');revision=op['revision']
        self.service.close();self.service=CommandService(self.root)
        self.assertEqual('running',self.service.bot('alpha')['desired']);self.assertEqual(revision,self.service.bot('alpha')['revision'])
        self.assertEqual('Interrupted',self.service.operation(op['id'])['status'])
    def test_unknown_maintenance_remains_indeterminate(self):
        with self.service.transaction() as db:db.execute("INSERT INTO operations(id,target,action,payload,status,created,updated) VALUES('job','alpha','job','{}','Running',0,0)")
        self.service.close();self.service=CommandService(self.root)
        self.assertEqual('Indeterminate',self.service.operation('job')['status']);self.service.update_operation('job','Completed')
        self.assertEqual('Indeterminate',self.service.operation('job')['status'])
    def test_conflicting_restart_busy_and_stop_priority(self):
        self.service.submit('alpha','restart')
        with self.assertRaises(Busy):self.service.submit('alpha','restart')
        self.assertEqual('Accepted',self.service.submit('alpha','disable')['status'])
        self.assertFalse(self.service.bot('alpha')['enabled'])
    def test_enable_leaves_stopped_and_preserves_on_reenrollment(self):
        self.service.submit('alpha','disable');self.service.submit('alpha','enable')
        self.assertEqual('stopped',self.service.bot('alpha')['desired']);self.assertTrue(self.service.bot('alpha')['enabled'])
        self.service.enroll({'id':'alpha'},True,'running')
        self.assertEqual('stopped',self.service.bot('alpha')['desired'])
    def test_external_hold_survives_reopen_until_explicit_action(self):
        self.service.observed('alpha','Running outside core',external_hold=1)
        self.service.close();self.service=CommandService(self.root)
        self.assertTrue(self.service.bot('alpha')['external_hold'])
        self.service.submit('alpha','start');self.assertFalse(self.service.bot('alpha')['external_hold'])
    def test_failure_budget_exact_delays_terminal_not_counted(self):
        with patch('bot_center.commands.time.time',return_value=1000):
            for index,delay in enumerate([2,5,15,30]):
                self.assertEqual(1000+delay,self.service.record_failure('alpha','run'+str(index),'unexpected','failure'))
            self.assertIsNone(self.service.record_failure('alpha','run4','unexpected','failure'))
        self.assertTrue(self.service.bot('alpha')['attention'])
        count=self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0]
        self.service.record_failure('alpha','credential','credentials','unavailable')
        self.assertEqual(count,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0])
    def test_watchdog_same_run_counted_once(self):
        self.service.record_failure('alpha','watchdog','unexpected','stall');self.service.record_failure('alpha','watchdog','unexpected','termination')
        self.assertEqual(1,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0])
    def test_retry_new_budget_preserves_diagnostic_history(self):
        self.service.record_failure('alpha','bad','unexpected','details')
        self.service.submit('alpha','retry');self.assertEqual(1,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0])
        self.assertIsNone(self.service.bot('alpha')['attention']);self.assertEqual('details',self.service.bot('alpha')['last_error'])
    def test_retry_preserves_stopped_intent(self):
        self.service.submit('alpha','stop');self.service.mark_satisfied('alpha',self.service.bot('alpha')['revision'],'Stopped')
        self.service.record_failure('alpha','bad','credentials','missing')
        operation=self.service.submit('alpha','retry')
        self.assertEqual('stopped',self.service.bot('alpha')['desired']);self.assertIsNone(self.service.bot('alpha')['attention'])
        self.service.mark_satisfied('alpha',operation['revision'],'Stopped')
        self.assertEqual('Completed',self.service.operation(operation['id'])['status'])
    def test_global_start_reports_conflicting_work_without_superseding_it(self):
        operation=self.service.submit('alpha','restart')
        all_operation=self.service.submit('*','start_all')
        self.assertIn('Busy',all_operation['result']['blocked']['alpha'])
        self.assertEqual('Accepted',self.service.operation(operation['id'])['status'])
    def test_non_object_payload_is_rejected_before_acceptance(self):
        for payload in ([],1,'text',False):
            with self.assertRaises(CommandError):self.service.submit('alpha','start',payload)
        self.assertEqual([],self.service.operations())
    def test_quit_preserves_saved_intent(self):
        self.service.submit('*','quit');self.assertTrue(self.service.launch_barrier);self.assertEqual('running',self.service.bot('alpha')['desired'])
    def test_storage_denial_never_acknowledged(self):
        before=self.service.bot('alpha');self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(PersistenceError):self.service.submit('alpha','stop',operation_id='denied')
        self.assertFalse(self.service.persistence_healthy);self.assertEqual(before,self.service.bot('alpha'))
        self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='denied'").fetchone());self.service.db.execute('PRAGMA query_only=OFF')
    def test_diskfull_at_commit_rolls_back_intent_and_operation(self):
        underlying=self.service.db
        class FailingCommit:
            def __getattr__(self,name):return getattr(underlying,name)
            def execute(self,sql,*args):
                if sql=='COMMIT':raise sqlite3.OperationalError('database or disk is full')
                return underlying.execute(sql,*args)
        self.service.db=FailingCommit();before=self.service.bot('alpha')
        with self.assertRaises(PersistenceError):self.service.submit('alpha','stop',operation_id='full')
        self.assertEqual(before,self.service.bot('alpha'));self.assertFalse(self.service.persistence_healthy)
        self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='full'").fetchone());self.service.db=underlying
    def test_capture_queue_has_no_durable_acceptance(self):
        self.service.capture_barrier=True
        with self.assertRaises(Busy):self.service.submit('alpha','stop',operation_id='queued')
        self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='queued'").fetchone())
    def test_global_start_reports_disabled_and_launches_eligible(self):
        self.service.enroll({'id':'disabled'},False,'stopped')
        operation=self.service.submit('*','start_all')
        self.assertEqual({'disabled':'Disabled'},operation['result']['blocked'])
        self.assertEqual('running',self.service.bot('alpha')['desired']);self.assertEqual('stopped',self.service.bot('disabled')['desired'])
    def test_credential_canary_is_never_persisted_or_echoed(self):
        canary=str(123456789)+':'+'synthetic_test_value_'*3
        with self.assertRaises(CommandError) as result:self.service.submit('alpha','start',{'value':canary},'canary')
        self.assertNotIn(canary,str(result.exception));self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='canary'").fetchone())
    def test_distinct_backup_is_busy_before_durable_acceptance(self):
        self.service.submit('*','backup',operation_id='first')
        with self.assertRaises(Busy):self.service.submit('*','backup',operation_id='other')
        self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='other'").fetchone())
    def test_pending_work_is_not_limited_to_activity_window(self):
        with self.service.transaction() as db:
            for index in range(80):db.execute("INSERT INTO operations(id,target,action,payload,status,created,updated) VALUES(?,?,'open_editor','{}','Accepted',?,?)",('older'+str(index),'alpha',index,index))
        self.assertEqual(50,len(self.service.operations()));self.assertEqual(80,len(self.service.pending_operations()))
    def test_backoff_survives_reopening_after_already_exited_run(self):
        self.service.observed('alpha','Running',run_id='failed')
        retry=self.service.record_failure('alpha','failed','unexpected','failure')
        self.assertIsNone(self.service.bot('alpha')['run_id'])
        self.service.close();self.service=CommandService(self.root)
        self.assertEqual(retry,self.service.bot('alpha')['retry_at'])
    def test_canary_in_metadata_is_rejected_without_echo_or_store(self):
        canary=str(123456789)+':'+'synthetic_test_value_'*3
        for target,identity in [('bot'+canary,'ordinary'),('*',canary)]:
            with self.assertRaises(CommandError) as error:self.service.submit(target,'backup',operation_id=identity)
            self.assertNotIn(canary,str(error.exception))
        self.assertEqual([],self.service.operations())

if __name__=='__main__':unittest.main()
