"""Prepared recordings: persistent MP3s, bounded memory, deduplicated background work."""
import asyncio
from collections import OrderedDict

from .library import digest


def audio_key(text,voice,speed):
    return 'audio:'+digest((voice+'\0'+speed+'\0'+text).encode('utf-8'))


class AudioPreparation:
    def __init__(self,speech,authorized=lambda tag:True,memory_limit=16*1024*1024):
        self.speech=speech;self.authorized=authorized;self.memory_limit=memory_limit
        self.ready=OrderedDict();self.size=0;self.inflight={};self.queued=set()
        self.queue=asyncio.Queue(maxsize=16);self.worker=None
        self.slots=asyncio.Semaphore(2);self.foreground=0;self.closed=False
        self.waiters={};self.started=set()

    def remember(self,key,data):
        if key in self.ready:self.size-=len(self.ready.pop(key))
        if len(data)>self.memory_limit:return
        self.ready[key]=data;self.size+=len(data)
        while self.size>self.memory_limit:
            _,old=self.ready.popitem(last=False);self.size-=len(old)

    def local(self,text,voice,speed):
        key=audio_key(text,voice,speed)
        if key in self.ready:
            self.ready.move_to_end(key)
            return self.ready[key]
        cached=getattr(self.speech,'cached',None)
        data=cached(text,voice,speed) if cached else None
        if data is not None:self.remember(key,data)
        return data

    async def generate(self,key,text,voice,speed):
        async with self.slots:
            self.started.add(key)
            data=await asyncio.to_thread(self.speech.synthesize,text,voice,speed)
            self.remember(key,data)
            return data

    def done(self,key,task):
        if self.inflight.get(key) is task:
            self.inflight.pop(key,None);self.started.discard(key)
        if not task.cancelled():
            try:task.exception()
            except Exception:pass

    async def get(self,text,voice,speed,foreground=True):
        if self.closed:raise RuntimeError('Audio preparation stopped.')
        data=self.local(text,voice,speed)
        if data is not None:return data
        key=audio_key(text,voice,speed)
        if foreground:self.foreground+=1
        try:
            task=self.inflight.get(key)
            if task is None or task.cancelled():
                task=asyncio.create_task(self.generate(key,text,voice,speed))
                self.inflight[key]=task
                task.add_done_callback(lambda completed:self.done(key,completed))
            self.waiters[key]=self.waiters.get(key,0)+1
            # Leaving an entry cancels its delivery, while preparation may finish
            # once and serve future visits. It never sends a Telegram message.
            return await asyncio.shield(task)
        finally:
            if foreground:self.foreground-=1
            remaining=self.waiters.get(key,1)-1
            if remaining:self.waiters[key]=remaining
            else:
                self.waiters.pop(key,None)
                if key not in self.started and not task.done():task.cancel()

    def enqueue(self,text,voice,speed,tag):
        if self.closed or not text:return
        key=audio_key(text,voice,speed)
        if key in self.queued or key in self.inflight or self.local(text,voice,speed) is not None:return
        if self.queue.full():return
        self.queued.add(key);self.queue.put_nowait((key,text,voice,speed,tag))
        if self.worker is None or self.worker.done():self.worker=asyncio.create_task(self.run())

    async def run(self):
        while not self.closed:
            key,text,voice,speed,tag=await self.queue.get()
            try:
                # One background generator leaves the second speech worker free.
                while self.foreground and not self.closed:await asyncio.sleep(.02)
                if not self.closed and self.authorized(tag):
                    try:await self.get(text,voice,speed,foreground=False)
                    except Exception:pass  # Retry on demand; background errors stay quiet.
            finally:
                self.queued.discard(key);self.queue.task_done()

    async def close(self):
        self.closed=True
        tasks=list(self.inflight.values())
        if self.worker:tasks.append(self.worker)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
