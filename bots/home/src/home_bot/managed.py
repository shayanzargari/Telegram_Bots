"""Contract v1 adapter around the unchanged Home business application."""
from pathlib import Path
import contextlib, threading,queue,time
from .bot import Bot, Telegram, TelegramError, NetworkError, bootstrap, configure_logging, process_lock
from .storage import Store

class PollDeadlineExceeded(Exception):pass

def single_poll(call,stop,deadline_seconds=30):
    """Bound the complete urllib poll, including a trickling response body.

    A timed-out thread is never reused or followed by another poll. The caller
    exits the worker, whose lifetime owner contains all of its resources.
    """
    result=queue.Queue(maxsize=1)
    def perform():
        try:result.put((True,call()))
        except BaseException as error:result.put((False,error))
    threading.Thread(target=perform,daemon=True,name='home-telegram-poll').start()
    deadline=time.monotonic()+deadline_seconds
    while True:
        if stop.is_set():raise InterruptedError('Stop requested')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise PollDeadlineExceeded('Home polling exceeded its overall deadline; the worker must exit before another poll.')
        try:ok,value=result.get(timeout=min(.2,remaining));break
        except queue.Empty:continue
    if not ok:raise value
    return value

def run(client,manifest):
    if client.bootstrap.get('job'):
        return run_job(client,manifest,client.bootstrap['job'],client.bootstrap.get('payload',{}))
    directory=Path(manifest['data_dir'])
    if not (directory/'tasks.sqlite3').is_file():
        client.emit('failure',category='configuration',message='Home database is missing; restore it before starting.')
        return 2
    token=client.bootstrap['token']
    class ObservedTelegram(Telegram):
        def call(self,method,*args,**kwargs):
            if client.stop.is_set():raise InterruptedError('Stop requested')
            if method=='getUpdates':kwargs['timeout']=min(kwargs.get('timeout',30),30)
            try:
                transport=super().call
                result=single_poll(lambda:transport(method,*args,**kwargs),client.stop) if method=='getUpdates' else transport(method,*args,**kwargs)
            except NetworkError:
                client.progress(connection='Disconnected');client.emit('connection',state='Disconnected');raise
            else:
                client.progress(poll_ok=method=='getUpdates')
                return result
    configure_logging(directory,token,True,log_dir=manifest['log_dir'])
    api=ObservedTelegram(token)
    store=None;legacy_lock=None
    try:
        # The shared runner verified identity and owns the numeric polling lock
        # before this module was called or writable state was opened.
        legacy_lock=process_lock(directory/'bot.lock');legacy_lock.__enter__()
        store=Store(directory)
        me=api.call('getMe')
        if int(me['id'])!=int(manifest['telegram_id']):
            client.emit('failure',category='identity',message='Home credential identity changed; saved state was left unchanged.')
            return 2
        bootstrap(store,api,me['username'],False)
        bot=Bot(store,api,me['username'],directory)
        def stop():
            client.stop.wait();bot.running=False
        threading.Thread(target=stop,daemon=True).start()
        client.ready()
        bot.run()
        return 0
    except InterruptedError:return 0
    except PollDeadlineExceeded:
        client.emit('failure',category='unexpected',message='Home polling exceeded the 30-second overall deadline. Worker stopped without a concurrent replacement poll.')
        return 2
    except TelegramError as error:
        category='credentials' if error.code==401 else 'conflict' if error.code==409 else 'unexpected'
        client.emit('failure',category=category,message='Home Telegram request failed; inspect the rotating log.')
        return 2
    except (ValueError,RuntimeError) as error:
        client.emit('failure',category='configuration',message=str(error))
        return 2
    finally:
        if store:store.close()
        if legacy_lock:legacy_lock.__exit__(None,None,None)

def run_job(client,manifest,job,payload):
    from bot_center_runtime.locks import ResourceLock
    import sqlite3,json
    if job!='integrity':
        client.emit('failure',category='configuration',message='Unsupported Home maintenance job.')
        return 2
    with ResourceLock('job:'+manifest['id']+':'+job):
        path=Path(manifest['data_dir'])/'tasks.sqlite3'
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Home database integrity check failed.')
            for name in ('tasks','members','meta'):db.execute('SELECT 1 FROM '+name+' LIMIT 1').fetchall()
            if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Home foreign key validation failed.')
            invalid=db.execute('SELECT COUNT(*) FROM tasks WHERE id<=0 OR revision<=0 OR done NOT IN (0,1) OR deleted NOT IN (0,1) OR length(title)=0').fetchone()[0]
            if invalid:raise ValueError('Home task records violate the saved-state contract.')
            members=db.execute('SELECT user_id,role FROM members').fetchall()
            if len(members)>2 or (members and sum(role=='owner' for _,role in members)!=1):raise ValueError('Home ownership/member contract is invalid.')
            for key,value in db.execute('SELECT key,value FROM meta'):
                parsed=json.loads(value)
                if key=='_data_revision' and (not isinstance(parsed,int) or parsed<0):raise ValueError('Home data revision is invalid.')
        finally:db.close()
        client.emit('progress',message='Home state integrity verified.');client.progress()
        return 0
