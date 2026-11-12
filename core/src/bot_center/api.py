"""Authenticated loopback UI and command adapters."""
from __future__ import annotations
import asyncio,contextlib,json,os,secrets,subprocess,time,uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,FileResponse,Response
from bot_center_runtime.paths import Installation
from bot_center_runtime.locks import FileLease,LockBusy
from bot_center_runtime.ipc import PipeServer,process_creation
from bot_center_runtime.redaction import redact
from .commands import CommandService,CommandError,Busy,OperationConflict,PersistenceError
from .supervisor import Supervisor
from .security import Sessions

class Core:
    def __init__(self,root,port=8765,service=None):
        self.root=Path(root).resolve();self.port=port;self.origin='http://127.0.0.1:'+str(port);self.instance=uuid.uuid4().hex
        self.commands=service or CommandService(self.root);self.supervisor=Supervisor(self.commands);self.sessions=Sessions();self.tasks={};self.task_errors={};self.quit=asyncio.Event();self.loop=None;self.pipe=None;self.schedule_dispatch_paused=False;self.recovery=None
        from .scheduler import Scheduler
        self.scheduler=Scheduler(self.commands);self.commands.scheduler=self.scheduler
        self.backup_task=None;self.editor_processes=[];self.queued_commands=[];self.registration_lock=asyncio.Lock()
    def readiness(self):
        return self.commands.persistence_healthy and self.supervisor.healthy and not self.task_errors.get('supervisor') and not (self.pipe and self.pipe.error) and not self.commands.launch_barrier
    def task_done(self,name,task):
        if task.cancelled():return
        error=task.exception()
        if error or not self.quit.is_set():
            self.task_errors[name]=name+' failed: '+(type(error).__name__ if error else 'unexpected completion')
            if name=='scheduler':self.schedule_dispatch_paused=True
            else:
                self.commands.launch_barrier=True;self.supervisor.healthy=False
                self.loop.create_task(self.supervisor.shutdown(interrupted=True))
    async def start(self):
        self.loop=asyncio.get_running_loop()
        self.pipe=PipeServer(self.root,self.instance,self.ipc)
        for name,coro in [('supervisor',self.supervisor.run()),('scheduler',self.scheduler.run()),('operations',self.operations_loop())]:
            task=asyncio.create_task(coro,name=name);self.tasks[name]=task;task.add_done_callback(lambda t,n=name:self.task_done(n,t))
        discovery={'instance':self.instance,'pid':os.getpid(),'creation':process_creation(os.getpid()),'origin':self.origin}
        directory=self.root/'data/core';directory.mkdir(parents=True,exist_ok=True)
        (directory/'discovery.json').write_text(json.dumps(discovery),encoding='utf-8')
    def ipc(self,request):
        if request.get('action')=='identify':return {'ready':self.readiness(),'origin':self.origin}
        if request.get('action')=='dashboard':
            if not self.readiness():return {'error':'Core is not ready. Inspect local status and recovery instructions.'}
            return {'origin':self.origin,'ticket':self.sessions.ticket(),'ready':True}
        future=asyncio.run_coroutine_threadsafe(self.ipc_command(request),self.loop)
        return future.result(timeout=180)
    async def ipc_command(self,request):
        try:
            if request.get('action')=='status':return self.status()
            return await self.submit(request.get('target','*'),request['action'],request.get('payload'),request.get('operation_id'))
        except CommandError as error:return {'error':str(error)}
    async def submit(self,target,action,payload=None,operation_id=None):
        if self.commands.capture_barrier:
            future=asyncio.get_running_loop().create_future()
            self.queued_commands.append((action,target,payload,operation_id,future))
            # A queued request has no durable acceptance yet. Its caller awaits commit.
            return await future
        try:return self.commands.submit(target,action,payload,operation_id)
        except PersistenceError:
            if action in {'stop','disable','stop_all','quit'}:
                for run in [*self.supervisor.runs.values(),*self.supervisor.jobs.values()]:
                    if target=='*' or run.bot_id==target:self.supervisor.begin_stop(run)
                if action=='quit':
                    await self.supervisor.shutdown();self.quit.set()
                raise PersistenceError('Immediate shutdown was requested, but persistent Stop/Disable/Quit could not be saved. New launches are suspended.') from None
            raise
    def release_capture(self):
        self.commands.capture_barrier=False
        queued=self.queued_commands;self.queued_commands=[]
        priority={'stop','disable','stop_all','quit'}
        indexed=list(enumerate(queued))
        for index,(action,target,payload,identity,future) in sorted(indexed,key=lambda entry:entry[1][0] not in priority):
            if future.cancelled():continue
            newer_stop=any(later>index and request[0] in priority and (request[1] in {target,'*'} or target=='*') for later,request in indexed)
            already_recorded=identity and self.commands.db.execute('SELECT 1 FROM operations WHERE id=?',(identity,)).fetchone()
            if newer_stop and action not in priority and not already_recorded:
                future.set_exception(Busy('Submitted request was superseded by a newer Stop/Disable/Quit before durable acceptance.'));continue
            try:result=self.commands.submit(target,action,payload,identity)
            except Exception as error:future.set_exception(error)
            else:future.set_result(result)
        self.schedule_dispatch_paused=False
    def status(self):
        declarations={b['id']:b for b in self.commands.installation.manifests()}
        scheduled={}
        for schedule in self.scheduler.list():
            if schedule['enabled'] and schedule['action']=='start' and schedule.get('next_due_utc'):
                scheduled.setdefault(schedule['target'],[]).append(schedule['next_due_utc'])
        bots=[]
        for bot in self.commands.bots():
            declaration=declarations.get(bot['id'],{})
            bots.append({**bot,**self.supervisor.status(bot['id']),'display_name':declaration.get('display_name',bot['id']),'next_scheduled_start':min(scheduled[bot['id']]) if scheduled.get(bot['id']) else None,'jobs':[{'id':j['id'],'name':j.get('name',j['id'])} for j in declaration.get('jobs',[])]})
        return {'instance':self.instance,'ready':self.readiness(),'bots':bots,'operations':self.commands.operations(),'scheduling':{'healthy':not self.task_errors.get('scheduler'),'error':self.task_errors.get('scheduler'),'paused':self.schedule_dispatch_paused},'errors':self.task_errors,'capture_in_progress':self.commands.capture_barrier}
    async def open_editor(self,no_browser=False):
        installation=Installation(self.root);bot=installation.require_state('french')
        executable=Path(bot.get('editor_python',self.root/'.venvs/french-editor/Scripts/pythonw.exe'))
        if not executable.is_file():raise CommandError('French editor environment is missing.')
        # Independent child: never assign to any managed Job Object.
        env=dict(os.environ);env['BOT_CENTER_ROOT']=str(self.root);env.pop('PYTHONPATH',None)
        arguments=[str(executable),'-m','french_notes.app','--root',str(self.root)]
        if no_browser:arguments+=['--no-browser','--idle-seconds','0']
        process=subprocess.Popen(arguments,cwd=self.root,env=env,close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
        self.editor_processes.append(process)
        # Confirm actual editor health/discovery instead of equating spawn with success.
        server=Path(bot['editor_state_dir'])/'server.json'
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if server.exists():
                try:
                    import urllib.request
                    local_http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    address=json.loads(server.read_text('utf-8'))['url']
                    if not address.startswith('http://127.0.0.1:'):raise ValueError()
                    with await asyncio.to_thread(local_http.open,address+'/health',timeout=2) as response:health=json.load(response)
                    if health.get('app')=='French Notes' and Path(health.get('folder','')).resolve()==Path(bot['notes_dir']).resolve():return {'opened':True}
                except (OSError,ValueError,KeyError):pass
            if process.poll() not in (None,0):raise CommandError('French editor failed to open. Check its application log.')
            await asyncio.sleep(.2)
        raise CommandError('Editor launch has not reached readiness; inspect its application log.')
    async def backup(self,operation):
        from .recovery import RecoveryService
        try:
            async with self.registration_lock:
                if self.commands.launch_barrier:
                    self.commands.update_operation(operation['id'],'Superseded',{'message':'Quit superseded this pending capture.'});return
                self.commands.capture_barrier=True;self.schedule_dispatch_paused=True
                self.commands.update_operation(operation['id'],'Running',{'phase':'Quiescing writers'})
                await self.supervisor.quiesce()
                # The editor holds a shared lease for its independent lifetime; never close it.
                with FileLease(self.root/'data/maintenance.lock',exclusive=True):
                    recovery=RecoveryService(self.root)
                    captured=await asyncio.to_thread(recovery.stage_capture,quiesced=True,destination=operation['payload'].get('destination'))
                # Capture and verification are separate: latest intent governs resumption.
                self.release_capture()
            verified=await asyncio.to_thread(recovery.verify,captured['path'])
            self.commands.update_operation(operation['id'],'Completed',verified)
        except LockBusy:
            self.commands.update_operation(operation['id'],'Blocked',{'message':'Close French editor after saving drafts, then request a new backup.'})
        except Exception as error:self.commands.update_operation(operation['id'],'Failed',{'message':redact(str(error))})
        finally:self.release_capture()
    async def operations_loop(self):
        while not self.quit.is_set():
            if self.pipe and self.pipe.error:raise RuntimeError('Essential management channel failed.')
            for operation in self.commands.pending_operations():
                if operation['status']!='Accepted':continue
                if operation['action']=='open_editor':
                    self.commands.update_operation(operation['id'],'Running')
                    try:self.commands.update_operation(operation['id'],'Completed',await self.open_editor())
                    except Exception as error:self.commands.update_operation(operation['id'],'Failed',{'message':redact(error)})
                elif operation['action']=='backup':
                    if self.backup_task and not self.backup_task.done():self.commands.update_operation(operation['id'],'Failed',{'message':'Busy: a backup is already active.'})
                    else:self.backup_task=asyncio.create_task(self.backup(operation))
            if self.commands.quit_requested and not self.supervisor.runs and not self.supervisor.jobs:
                self.quit.set()
            await asyncio.sleep(.2)
    async def close(self):
        if self.backup_task and not self.backup_task.done():await self.backup_task
        await self.supervisor.shutdown()
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        if self.pipe:self.pipe.close()
        self.commands.close()

def create_app(core,start_tasks=True):
    @asynccontextmanager
    async def lifespan(app):
        if start_tasks:await core.start()
        try:yield
        finally:
            if start_tasks:await core.close()
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    ui=Path(__file__).parent/'ui'
    @app.middleware('http')
    async def security(request,call_next):
        if int(request.headers.get('content-length','0') or '0')>65536:return JSONResponse({'error':'Request is too large.'},413)
        if request.headers.get('host')!=core.origin.removeprefix('http://'):return JSONResponse({'error':'Invalid local address.'},403)
        origin=request.headers.get('origin')
        if origin and origin!=core.origin:return JSONResponse({'error':'Invalid request origin.'},403)
        if request.url.path.startswith('/api/') and request.url.path!='/api/session':
            session=core.sessions.session(request.cookies.get('bot_center_session',''))
            if not session:return JSONResponse({'error':'Open the dashboard through START.cmd to authenticate.'},401)
            request.state.session=session
            if request.method not in {'GET','HEAD'}:
                if origin!=core.origin or not secrets.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):return JSONResponse({'error':'Request verification failed.'},403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        return response
    @app.exception_handler(CommandError)
    async def command_error(request,error):return JSONResponse({'error':redact(error)},409 if isinstance(error,(Busy,OperationConflict)) else 503 if isinstance(error,PersistenceError) else 400)
    @app.exception_handler(ValueError)
    async def value_error(request,error):return JSONResponse({'error':redact(error)},400)
    @app.get('/')
    async def index():return FileResponse(ui/'index.html')
    @app.get('/assets/{name}')
    async def asset(name:str):
        if name not in {'app.js','style.css'}:return Response(status_code=404)
        return FileResponse(ui/name)
    @app.post('/api/session')
    async def session(request:Request):
        if request.headers.get('origin')!=core.origin:return JSONResponse({'error':'Invalid origin.'},403)
        try:body=await request.json();result=core.sessions.exchange(body.get('ticket',''))
        except (ValueError,AttributeError,TypeError):result=None
        if not result:return JSONResponse({'error':'Launcher link expired or was already used. Reopen START.cmd.'},401)
        response=JSONResponse({'csrf':result[1]});response.set_cookie('bot_center_session',result[0],httponly=True,samesite='strict',path='/',max_age=12*3600);return response
    @app.get('/api/status')
    async def status(request:Request):return {**core.status(),'csrf':request.state.session['csrf']}
    @app.post('/api/commands')
    async def command(request:Request):
        body=await request.json()
        return await core.submit(body.get('target','*'),body.get('action',''),body.get('payload'),body.get('operation_id'))
    @app.get('/api/logs/{bot_id}')
    async def logs(bot_id:str):
        core.commands.bot(bot_id);path=core.root/'logs'/bot_id/'runtime.log'
        if not path.exists():return {'text':'No diagnostics recorded.'}
        with path.open('rb') as stream:stream.seek(max(0,path.stat().st_size-65536));text=stream.read().decode('utf-8','replace')
        return {'text':redact(text)}
    @app.get('/api/schedules')
    async def schedules():return {'schedules':core.scheduler.list(),'occurrences':core.scheduler.occurrences()}
    @app.post('/api/schedules')
    async def create_schedule(request:Request):
        body=await request.json()
        if core.commands.capture_barrier or core.commands.launch_barrier:raise Busy('Scheduling changes are blocked by capture or shutdown; this request is not accepted.')
        return core.scheduler.create(body['target'],body['action'],body['spec'],body.get('payload'))
    @app.post('/api/schedules/{identity}')
    async def edit_schedule(identity:str,request:Request):
        body=await request.json()
        if core.commands.capture_barrier or core.commands.launch_barrier:raise Busy('Scheduling changes are blocked by capture or shutdown; this request is not accepted.')
        return core.scheduler.edit(identity,**body)
    @app.post('/api/bots')
    async def add_bot(request:Request):
        from .registration import RegistrationService
        body=await request.json();registration=RegistrationService(core.root,core.commands)
        try:
            async with core.registration_lock:
                if core.commands.capture_barrier or core.commands.launch_barrier:raise Busy('Registration is blocked by capture or shutdown; this request is not accepted.')
                core.commands.installation.assert_active()
                if body.get('mode')=='existing':return await asyncio.to_thread(registration.register,body['manifest_path'])
                return await asyncio.to_thread(registration.create,body['id'],body['display_name'],telegram_id=body.get('telegram_id'),capabilities=body.get('capabilities',['polling']),install=True)
        except CommandError:raise
        except (ValueError,RuntimeError) as error:return JSONResponse({'error':redact(error)},400)
    @app.get('/api/backups')
    async def backups():
        from .recovery import RecoveryService
        return {'sets':RecoveryService(core.root).list_sets()}
    return app
