"""Own managed trees, current-run health and one bounded restart policy."""
from __future__ import annotations
import asyncio,logging,math,time,uuid
from dataclasses import dataclass,field
from pathlib import Path
from logging.handlers import RotatingFileHandler
from bot_center_runtime.credentials import CredentialStore,verify_identity,CredentialError,IdentityError,ConnectivityError
from bot_center_runtime.paths import ConfigurationError
from bot_center_runtime.locks import ResourceLock,LockBusy
from bot_center_runtime.process import ManagedProcess
from bot_center_runtime.redaction import RedactFilter,redact
from .commands import PersistenceError

@dataclass
class Run:
    bot_id:str
    run_id:str
    revision:int
    process:ManagedProcess
    lock:object
    started:float=field(default_factory=time.monotonic)
    last_telemetry:float=field(default_factory=time.monotonic)
    progress:float=field(default_factory=time.monotonic)
    last_poll:float|None=None
    ready:bool=False
    stopping:float|None=None
    failure:dict|None=None
    interrupted:bool=False
    job_operation:str|None=None
    timeout_seconds:float|None=None
    connection:str='Unknown'

class Supervisor:
    def __init__(self,commands):
        self.service=commands;self.root=commands.root;self.runs={};self.jobs={};self.stop=asyncio.Event();self.healthy=True;self.error=None;self.shutdown_requested=False
        self.loggers={};self.last_tick=time.monotonic();self.recovery_until=0
    def logger(self,identity):
        if identity not in self.loggers:
            directory=self.root/'logs'/identity;directory.mkdir(parents=True,exist_ok=True)
            logger=logging.getLogger('bot-center.worker.'+identity);logger.setLevel(logging.INFO);logger.propagate=False
            handler=RotatingFileHandler(directory/'runtime.log',maxBytes=1024*1024,backupCount=3,encoding='utf-8');handler.addFilter(RedactFilter());handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler);self.loggers[identity]=logger
        return self.loggers[identity]
    async def launch(self,bot,job_operation=None):
        identity=bot['id'];lock=None
        try:
            manifest=self.service.installation.require_state(identity)
            if not Path(manifest['python']).is_file():raise ConfigurationError('Installed bot environment is missing. Reconstruct locked environments.')
            declaration=next((job for job in manifest.get('jobs',[]) if job_operation and job['id']==job_operation['payload']['job']),{})
            timeout=declaration.get('timeout_seconds',120)
            if job_operation and (not declaration or type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0):
                raise ConfigurationError('Maintenance job needs a positive finite deadline and a declared contract.')
            token=CredentialStore(self.root).load(manifest['credential_ref']) if not job_operation else ''
            if not job_operation:
                if not manifest.get('telegram_id'):raise ConfigurationError('Expected numeric Telegram identity has not been enrolled.')
                await asyncio.to_thread(verify_identity,token,manifest['telegram_id'],manifest.get('expected_username'))
                lock=ResourceLock('poll:'+str(manifest['telegram_id']))
                try:lock.acquire()
                except LockBusy:
                    self.service.observed(identity,'Running outside core',external_hold=1,last_error='Standalone polling owner detected. Explicit Start is required after it exits.');return
            latest=self.service.bot(identity)
            if (self.service.launch_barrier or self.service.capture_barrier or not self.service.persistence_healthy or
                any(run.bot_id==identity and run.stopping is not None for run in self.jobs.values()) or
                (job_operation and (latest['revision']!=bot['revision'] or self.service.operation(job_operation['id'])['status']!='Accepted' or any(run.bot_id==identity for run in self.jobs.values()))) or
                (not job_operation and (latest['revision']!=bot['revision'] or not latest['enabled'] or latest['desired']!='running' or latest['attention'] or latest['external_hold']))):
                if lock:lock.close()
                return
            run_id=uuid.uuid4().hex
            bootstrap={'contract_version':1,'root':str(self.root),'bot_id':identity,'run_id':run_id,'token':token,'manifest':manifest}
            if job_operation:
                bootstrap['job']=job_operation['payload']['job'];bootstrap['payload']={k:v for k,v in job_operation['payload'].items() if k!='job'};bootstrap['operation_id']=job_operation['id']
            process=ManagedProcess([manifest['python'],'-m','bot_center_runtime.runner','--worker'],bootstrap,cwd=manifest['cwd'],environment={'BOT_CENTER_ROOT':str(self.root)},on_output=lambda stream,text:self.logger(identity).info('%s: %s',stream,text.rstrip()))
            run=Run(identity,run_id,latest['revision'],process,lock,job_operation=job_operation['id'] if job_operation else None,timeout_seconds=float(timeout) if job_operation else None)
            if job_operation:
                self.jobs[job_operation['id']]=run;self.service.update_operation(job_operation['id'],'Running',{'run_id':run_id})
            else:
                self.runs[identity]=run;self.service.observed(identity,'Running',run_id=run_id,retry_at=None)
                with self.service.transaction() as db:db.execute("UPDATE operations SET status='Running',updated=? WHERE target=? AND revision=? AND status='Accepted'",(time.time(),identity,latest['revision']))
        except ConnectivityError:
            # Connectivity is neither an invalid credential nor a process failure.
            self.service.observed(identity,'Disconnected',last_error='Telegram identity verification is temporarily unreachable; credentials were preserved.',retry_at=time.time()+15)
            if lock:lock.close()
        except (CredentialError,ConfigurationError) as error:
            if lock:lock.close()
            category='identity' if isinstance(error,IdentityError) else ('credentials' if isinstance(error,CredentialError) else 'configuration')
            self.service.record_failure(identity,uuid.uuid4().hex,category,str(error))
        except BaseException:
            if lock:lock.close()
            raise
    def begin_stop(self,run,interrupted=False):
        if run.stopping is None:
            run.stopping=time.monotonic();run.interrupted=interrupted;run.process.request_stop()
            if run.job_operation and not run.failure and self.service.persistence_healthy:
                self.service.update_operation(run.job_operation,'Indeterminate' if interrupted else 'Superseded',{'message':'Interrupted by core loss.' if interrupted else 'Cancellation requested; worker resources are draining.'})
    def close_owned(self,run):
        try:run.process.close()
        except BaseException as error:
            self.error=self.error or 'Owned worker cleanup failed: '+type(error).__name__
            try:run.process.terminate()
            except BaseException:pass
            try:run.process.job.close()
            except BaseException:pass
        finally:
            if run.lock:
                try:run.lock.close()
                except BaseException:pass
    def _events(self,run):
        while run.process.events:
            event=run.process.events.popleft();kind=event['kind'];now=time.monotonic();run.last_telemetry=now
            if kind=='ready':
                run.ready=True
                if not run.job_operation:
                    self.service.observed(run.bot_id,'Ready');self.service.mark_satisfied(run.bot_id,run.revision,'Ready')
            elif kind in {'telemetry','health','progress'}:
                if kind=='progress':run.progress=now
                if isinstance(event.get('progress'),(int,float)):run.progress=max(run.progress,min(now,event['progress']))
                if isinstance(event.get('last_poll'),(int,float)):run.last_poll=min(now,event['last_poll'])
                if isinstance(event.get('connection'),str):run.connection=event['connection']
                if run.ready and not run.job_operation:
                    connection=self.connection_state(run,now)
                    self.service.observed(run.bot_id,'Ready' if connection=='Connected' else ('Disconnected' if 'disconnected' in connection.lower() else 'Unknown'))
                if run.job_operation and kind=='progress':
                    self.service.update_operation(run.job_operation,'Running',{k:redact(v) if isinstance(v,str) else v for k,v in event.items() if k not in {'contract_version','run_id','seq','kind'}})
            elif kind=='failure':run.failure={'category':event.get('category','unexpected'),'message':redact(event.get('message','Worker reported a typed failure.'))}
            elif kind=='connection':
                run.connection=event.get('state','Unknown')
                if not run.job_operation:self.service.observed(run.bot_id,'Disconnected' if event.get('state')=='Disconnected' else 'Unknown')
        if run.process.protocol_error and not run.failure:
            run.failure={'category':'unexpected','message':run.process.protocol_error};self.begin_stop(run)
    async def _observe(self,run):
        self._events(run);now=time.monotonic();code=run.process.poll()
        if code is None:
            if run.stopping is not None and now-run.stopping>=45:run.process.terminate()
            elif run.job_operation and run.stopping is None and run.timeout_seconds is not None and now-run.started>=run.timeout_seconds:
                run.failure={'category':'timeout','message':'Maintenance job exceeded its declared deadline; cancellation requested and resources remain reserved until exit.'};self.begin_stop(run)
            elif now>=self.recovery_until and not run.stopping and (now-run.progress>90 or now-run.last_telemetry>90):
                run.failure=run.failure or {'category':'unexpected','message':'Application stopped making progress; watchdog initiated shutdown.'};self.begin_stop(run)
            elif now-run.last_telemetry>20 and not run.job_operation:self.service.observed(run.bot_id,'Unknown/Unresponsive')
            return False
        self._events(run);self.close_owned(run)
        if run.job_operation:
            operation=self.service.operation(run.job_operation)
            if operation['status'] not in {'Superseded','Indeterminate'}:
                self.service.update_operation(run.job_operation,'Completed' if code==0 and not run.failure else 'Failed',run.failure or {'exit_code':code})
        else:
            latest=self.service.bot(run.bot_id)
            if run.failure:
                self.service.record_failure(run.bot_id,run.run_id,run.failure['category'],run.failure['message'])
            elif run.interrupted or self.service.launch_barrier:
                self.service.observed(run.bot_id,'Interrupted by core loss' if run.interrupted else 'Stopped',run_id=None)
            elif run.stopping is not None:
                self.service.observed(run.bot_id,'Stopped',run_id=None)
            else:self.service.record_failure(run.bot_id,run.run_id,'unexpected','Worker exited unexpectedly (exit '+str(code)+').')
        return True
    async def tick(self):
        now=time.monotonic()
        if now-self.last_tick>10:self.recovery_until=now+90
        self.last_tick=now
        for identity,run in list(self.runs.items()):
            latest=self.service.bot(identity)
            if latest['revision']!=run.revision and latest['desired']=='running' and latest['enabled']:
                restart_requested=self.service.latest_lifecycle(identity,latest['revision']) in {'restart','retry'}
                if not restart_requested:
                    run.revision=latest['revision']
                    if run.ready:self.service.mark_satisfied(identity,run.revision,'Ready')
            if self.service.launch_barrier or self.service.capture_barrier or latest['desired']=='stopped' or not latest['enabled'] or latest['revision']!=run.revision:
                self.begin_stop(run)
            if await self._observe(run):self.runs.pop(identity,None)
        for operation_id,run in list(self.jobs.items()):
            latest=self.service.bot(run.bot_id)
            if self.service.launch_barrier or latest['revision']!=run.revision or self.service.operation(operation_id)['status']=='Superseded':self.begin_stop(run)
            if await self._observe(run):self.jobs.pop(operation_id,None)
        if self.service.capture_barrier:return
        if not self.healthy or not self.service.persistence_healthy:return
        for bot in self.service.bots():
            if bot['id'] in self.runs:continue
            try:manifest=self.service.installation.bot(bot['id'])
            except ConfigurationError:
                if not bot['attention']:self.service.record_failure(bot['id'],uuid.uuid4().hex,'configuration','Bot declaration is missing or invalid; reconcile registration recovery before starting.')
                continue
            if manifest.get('telegram_id'):
                probe=ResourceLock('poll:'+str(manifest['telegram_id']))
                try:probe.acquire()
                except LockBusy:
                    if bot['observed']!='Running outside core' or not bot['external_hold']:
                        self.service.observed(bot['id'],'Running outside core',external_hold=1,last_error='An outside polling instance owns this identity. Explicit Start is required after it exits.')
                    continue
                else:probe.close()
            if bot['external_hold']:
                if bot['observed']!='Outside core hold':self.service.observed(bot['id'],'Outside core hold',last_error='The outside instance has exited. Choose Start explicitly to let the core take control.')
                continue
            if bot['desired']=='stopped' or not bot['enabled']:
                if bot['observed'] not in {'Stopped','Running outside core'}:self.service.observed(bot['id'],'Stopped',run_id=None)
                if not any(run.bot_id==bot['id'] for run in self.jobs.values()):self.service.mark_satisfied(bot['id'],bot['revision'],'Stopped')
                continue
            if bot['attention'] or bot['external_hold'] or self.service.launch_barrier or self.service.capture_barrier:continue
            if bot['retry_at'] and bot['retry_at']>time.time():continue
            await self.launch(bot)
        if not self.service.launch_barrier and not self.service.capture_barrier:
            for operation in self.service.pending_operations():
                if operation['action']=='job' and operation['status']=='Accepted':await self.launch(self.service.bot(operation['target']),job_operation=operation)
        for operation in self.service.pending_operations():
            if operation['status']=='Accepted' and operation['action'] in {'stop_all','start_all','quit'}:
                relevant=self.service.bots()
                done=not self.runs and not self.jobs if operation['action'] in {'stop_all','quit'} else all(b['observed']=='Ready' or b['attention'] or b['external_hold'] or not b['enabled'] for b in relevant)
                if done:self.service.update_operation(operation['id'],'Completed',{**operation['result'],'bots':{b['id']:b['observed'] for b in relevant}})
    async def run(self):
        try:
            while not self.stop.is_set():
                await self.tick();await asyncio.sleep(.25)
        except BaseException as error:
            if isinstance(error,asyncio.CancelledError) and self.shutdown_requested:return
            self.healthy=False;self.error='Supervisor failed: '+type(error).__name__;self.service.launch_barrier=True
            await self.shutdown(interrupted=True)
            raise
    async def quiesce(self):
        for run in [*self.runs.values(),*self.jobs.values()]:self.begin_stop(run)
        deadline=time.monotonic()+46
        while (self.runs or self.jobs) and time.monotonic()<deadline:
            for mapping in (self.runs,self.jobs):
                for key,run in list(mapping.items()):
                    try:
                        if self.service.persistence_healthy:
                            done=await self._observe(run)
                        else:
                            done=run.process.poll() is not None
                            if done:
                                self.close_owned(run)
                    except PersistenceError:
                        self.service.persistence_healthy=False;done=False
                    if done:mapping.pop(key,None)
            await asyncio.sleep(.1)
        for mapping in (self.runs,self.jobs):
            for run in list(mapping.values()):
                self.close_owned(run)
            mapping.clear()
    async def shutdown(self,interrupted=False):
        self.shutdown_requested=True;self.service.launch_barrier=True;self.stop.set()
        for run in [*self.runs.values(),*self.jobs.values()]:self.begin_stop(run,interrupted=interrupted)
        try:await self.quiesce()
        finally:
            for mapping in (self.runs,self.jobs):
                for run in list(mapping.values()):
                    self.close_owned(run)
                mapping.clear()
    @staticmethod
    def connection_state(run,now):
        if now-run.last_telemetry>20 or now-run.progress>90:return 'Unknown/Unresponsive'
        if run.connection=='Disconnected':return 'Disconnected'
        return 'Connected' if run.last_poll is not None and now-run.last_poll<=90 else 'Unknown'
    def status(self,identity):
        run=self.runs.get(identity)
        now=time.monotonic()
        return {'process_id':run.process.pid if run else None,'uptime_seconds':max(0,int(now-run.started)) if run else 0,'connection':self.connection_state(run,now) if run else 'Stopped','ready':bool(run and run.ready),'last_poll_age_seconds':round(max(0,now-run.last_poll),2) if run and run.last_poll is not None else None,'telemetry_age_seconds':round(max(0,now-run.last_telemetry),2) if run else None}
