import asyncio,json,sys,tempfile,time,unittest
from pathlib import Path
from collections import deque
from unittest.mock import patch
from bot_center.commands import CommandService
from bot_center.supervisor import Supervisor,Run
from bot_center.api import Core
from bot_center_runtime.locks import LockBusy

class FakeProcess:
    counter=0
    def __init__(self,*args,**kwargs):
        FakeProcess.counter+=1;self.pid=FakeProcess.counter;self.events=deque();self.protocol_error=None;self.code=None;self.closed=False;self.stops=0;self.terminations=0
        self.run_id=args[1]['run_id'] if len(args)>1 else 'run'
    def poll(self):return self.code
    def request_stop(self):self.stops+=1;self.code=0
    def terminate(self):self.terminations+=1;self.code=1
    def close(self):self.closed=True
class FakeLock:
    def __init__(self,*args):self.closed=False
    def acquire(self):return self
    def close(self):self.closed=True

class Supervision(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'config').mkdir();(self.root/'data/alpha').mkdir(parents=True)
        (self.root/'config/installation.json').write_text('{"generation":"fixture"}')
        manifest={'id':'alpha','schema_version':1,'contract_version':1,'module':'fake.managed','python':sys.executable,'cwd':'data/alpha','data_dir':'data/alpha','log_dir':'logs/alpha','telegram_id':123456,'credential_ref':'alpha','required_state':[],'jobs':[{'id':'integrity'}]}
        (self.root/'config/registry.json').write_text(json.dumps({'schema':1,'bots':[manifest]}))
        self.service=CommandService(self.root);self.service.enroll(manifest,True,'running');self.supervisor=Supervisor(self.service)
        self.patches=[patch('bot_center.supervisor.CredentialStore.load',return_value='fixture'),patch('bot_center.supervisor.verify_identity',return_value={'id':123456}),patch('bot_center.supervisor.ResourceLock',FakeLock),patch('bot_center.supervisor.ManagedProcess',FakeProcess)]
        for mock in self.patches:mock.start()
    async def asyncTearDown(self):
        await self.supervisor.shutdown()
        for mock in self.patches:mock.stop()
        self.service.close();self.temp.cleanup()
    async def test_stop_after_network_validation_before_launch_wins(self):
        def validation(*args):self.service.submit('alpha','stop');return {'id':123456}
        with patch('bot_center.supervisor.verify_identity',side_effect=validation):await self.supervisor.launch(self.service.bot('alpha'))
        self.assertNotIn('alpha',self.supervisor.runs)
    async def test_start_is_running_only_after_process_observation_ready_later(self):
        operation=self.service.submit('alpha','start');await self.supervisor.tick()
        self.assertEqual('Running',self.service.bot('alpha')['observed']);self.assertEqual('Running',self.service.operation(operation['id'])['status'])
        run=self.supervisor.runs['alpha'];run.process.events.append({'kind':'ready'})
        await self.supervisor.tick();self.assertTrue(run.ready);self.assertEqual('Completed',self.service.operation(operation['id'])['status'])
    async def test_repeated_start_does_not_replace_running_process(self):
        await self.supervisor.tick();run=self.supervisor.runs['alpha'];run.ready=True
        self.service.submit('alpha','start');await self.supervisor.tick()
        self.assertIs(run,self.supervisor.runs['alpha']);self.assertEqual(0,run.process.stops)
    async def test_restart_stops_old_tree_before_one_replacement(self):
        await self.supervisor.tick();old=self.supervisor.runs['alpha']
        self.service.submit('alpha','restart');await self.supervisor.tick();new=self.supervisor.runs['alpha']
        self.assertTrue(old.process.closed);self.assertIsNot(old,new);self.assertEqual(1,old.process.stops)
        await self.supervisor.tick();self.assertIs(new,self.supervisor.runs['alpha'])
    async def test_newer_stop_prevents_restart_replacement(self):
        await self.supervisor.tick();old=self.supervisor.runs['alpha']
        self.service.submit('alpha','restart');self.service.submit('alpha','stop');await self.supervisor.tick()
        self.assertTrue(old.process.closed);self.assertNotIn('alpha',self.supervisor.runs)
        self.assertEqual('stopped',self.service.bot('alpha')['desired'])
    async def test_external_lock_persists_hold_and_never_takeover_automatically(self):
        class Occupied(FakeLock):
            def acquire(self):raise LockBusy('external')
        with patch('bot_center.supervisor.ResourceLock',Occupied):await self.supervisor.tick()
        self.assertTrue(self.service.bot('alpha')['external_hold']);self.assertEqual('Running outside core',self.service.bot('alpha')['observed'])
        await self.supervisor.tick();self.assertNotIn('alpha',self.supervisor.runs)
        self.service.submit('alpha','start');await self.supervisor.tick();self.assertIn('alpha',self.supervisor.runs)
    async def test_core_interruption_does_not_increment_bot_failure(self):
        await self.supervisor.tick();await self.supervisor.shutdown(interrupted=True)
        self.assertEqual(0,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0]);self.assertEqual('running',self.service.bot('alpha')['desired'])
    async def test_watchdog_records_one_failure_despite_termination(self):
        await self.supervisor.tick();run=self.supervisor.runs['alpha'];run.progress=time.monotonic()-100
        await self.supervisor.tick();await self.supervisor.tick()
        self.assertEqual(1,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0]);self.assertNotIn('alpha',self.supervisor.runs)
    async def test_disconnect_with_actual_progress_does_not_restart(self):
        await self.supervisor.tick();run=self.supervisor.runs['alpha'];run.ready=True
        run.process.events.append({'kind':'health','progress':time.monotonic(),'connection':'Disconnected','last_poll':None})
        await self.supervisor.tick();self.assertIs(run,self.supervisor.runs['alpha']);self.assertEqual(0,run.process.stops)
    async def test_essential_supervisor_failure_blocks_launch_and_owns_shutdown(self):
        with patch.object(self.supervisor,'tick',side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):await self.supervisor.run()
        self.assertFalse(self.supervisor.healthy);self.assertTrue(self.service.launch_barrier)
    async def test_scheduler_failure_is_degraded_without_stopping_controls(self):
        core=Core(self.root,service=self.service);core.loop=asyncio.get_running_loop()
        async def failed():raise RuntimeError('scheduler fixture')
        task=asyncio.create_task(failed());await asyncio.gather(task,return_exceptions=True);core.task_done('scheduler',task)
        self.assertTrue(core.schedule_dispatch_paused);self.assertTrue(core.supervisor.healthy);self.assertFalse(self.service.launch_barrier)
    async def test_queued_capture_stop_is_accepted_before_resume(self):
        core=Core(self.root,service=self.service);self.service.capture_barrier=True
        request=asyncio.create_task(core.submit('alpha','stop',operation_id='queued-stop'));await asyncio.sleep(0)
        self.assertFalse(request.done());self.assertIsNone(self.service.db.execute("SELECT 1 FROM operations WHERE id='queued-stop'").fetchone())
        core.release_capture();result=await request;self.assertEqual('Accepted',result['status']);self.assertEqual('stopped',self.service.bot('alpha')['desired'])
    async def test_capture_newer_stop_supersedes_earlier_queued_start(self):
        from bot_center.commands import Busy
        core=Core(self.root,service=self.service);self.service.capture_barrier=True
        start=asyncio.create_task(core.submit('alpha','start',operation_id='queued-start'));stop=asyncio.create_task(core.submit('alpha','stop',operation_id='queued-stop'))
        await asyncio.sleep(0);core.release_capture()
        with self.assertRaises(Busy):await start
        self.assertEqual('Accepted',(await stop)['status']);self.assertEqual('stopped',self.service.bot('alpha')['desired'])
    async def test_persistence_failure_still_closes_every_owned_tree(self):
        await self.supervisor.tick();first=self.supervisor.runs['alpha']
        second=Run('alpha','second',0,FakeProcess(),FakeLock());self.supervisor.jobs['second']=second
        self.service.db.execute('PRAGMA query_only=ON')
        await self.supervisor.shutdown(interrupted=True)
        self.assertTrue(first.process.closed);self.assertTrue(second.process.closed)
        self.assertEqual({},self.supervisor.runs);self.assertEqual({},self.supervisor.jobs);self.service.db.execute('PRAGMA query_only=OFF')
    async def test_unsaved_stop_requests_immediate_stop_and_reports_failure(self):
        from bot_center.commands import PersistenceError
        core=Core(self.root,service=self.service);core.supervisor=self.supervisor
        await self.supervisor.tick();run=self.supervisor.runs['alpha']
        self.service.db.execute('PRAGMA query_only=ON')
        with self.assertRaisesRegex(PersistenceError,'could not be saved'):await core.submit('alpha','stop')
        self.assertEqual(1,run.process.stops);self.assertEqual('running',self.service.bot('alpha')['desired']);self.assertFalse(self.service.persistence_healthy)
        self.service.db.execute('PRAGMA query_only=OFF')
    async def test_maintenance_deadline_cancels_then_drains_without_bot_failure(self):
        operation=self.service.submit('alpha','job',{'job':'integrity'})
        await self.supervisor.tick();run=self.supervisor.jobs[operation['id']]
        run.timeout_seconds=1;run.started=time.monotonic()-2
        await self.supervisor.tick()
        self.assertEqual(1,run.process.stops);self.assertIn(operation['id'],self.supervisor.jobs)
        await self.supervisor.tick()
        self.assertNotIn(operation['id'],self.supervisor.jobs)
        self.assertEqual('Failed',self.service.operation(operation['id'])['status'])
        self.assertEqual('timeout',self.service.operation(operation['id'])['result']['category'])
        self.assertEqual(0,self.service.db.execute('SELECT count(*) FROM failure_history').fetchone()[0])
    async def test_stop_completion_and_new_launch_wait_for_cancelled_job_exit(self):
        operation=self.service.submit('alpha','job',{'job':'integrity'})
        await self.supervisor.tick();job=self.supervisor.jobs[operation['id']]
        def request_stop():job.process.stops+=1
        job.process.request_stop=request_stop
        stop=self.service.submit('alpha','stop');await self.supervisor.tick()
        self.assertEqual('Superseded',self.service.operation(operation['id'])['status'])
        self.assertEqual('Accepted',self.service.operation(stop['id'])['status'])
        await self.supervisor.launch(self.service.bot('alpha'),job_operation=operation)
        self.assertIs(job,self.supervisor.jobs[operation['id']]);self.assertNotIn('alpha',self.supervisor.runs)
        job.process.code=0;await self.supervisor.tick()
        self.assertEqual('Completed',self.service.operation(stop['id'])['status'])
        self.assertEqual('Superseded',self.service.operation(operation['id'])['status'])
    async def test_connection_requires_current_telemetry_and_responsive_progress(self):
        await self.supervisor.tick();run=self.supervisor.runs['alpha'];run.last_poll=time.monotonic()
        self.assertEqual('Connected',self.supervisor.status('alpha')['connection'])
        run.connection='Disconnected';self.assertEqual('Disconnected',self.supervisor.status('alpha')['connection'])
        run.last_telemetry=time.monotonic()-21
        self.assertEqual('Unknown/Unresponsive',self.supervisor.status('alpha')['connection'])
    async def test_unexpected_essential_completion_blocks_launches(self):
        core=Core(self.root,service=self.service);core.loop=asyncio.get_running_loop()
        async def complete():return
        task=asyncio.create_task(complete());await task;core.task_done('operations',task)
        self.assertFalse(core.readiness());self.assertTrue(self.service.launch_barrier)
        await asyncio.sleep(0)

if __name__=='__main__':unittest.main()
