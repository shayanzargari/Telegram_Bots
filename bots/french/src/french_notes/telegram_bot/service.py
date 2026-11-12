"""Telegram adapter. Every protected operation crosses the current access gate."""
from __future__ import annotations

import asyncio
from collections import defaultdict
import html
import io
import json
import logging
from pathlib import Path
import os
import re
import sqlite3
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaAudio, Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, TypeHandler

from . import BOT_USERNAME
from .library import digest
from .reader import Reader, chunks, short
from .audio import AudioPreparation,audio_key

LOG=logging.getLogger(__name__)


class Service:
    def __init__(self,state,library,speech=None,automatic_audio=True,preload=True):
        self.state=state;self.library=library;self.speech=speech
        self.gates=defaultdict(asyncio.Lock)
        self.update_gate=asyncio.Lock()
        self._update_task=None;self._update_id=None;self._text_sequence=0
        self.jobs={};self.speech_slots=asyncio.Semaphore(2)
        self.denials={};self.application=None;self.watcher=None
        self.health={'polling':'starting','speech':'ready' if speech else 'unavailable','last_update':None}
        self.errors=[]
        self.cache_limit=256*1024*1024
        self.closing=False
        self.scan_job=None
        self.automatic_audio=automatic_audio;self.preload=preload
        self.preparation=AudioPreparation(speech,self.preparation_authorized) if speech else None

    def preparation_authorized(self,tag):
        user,epoch,version,voice,speed=tag
        account=self.state.user(user)
        return bool(not self.closing and account and account['active'] and account['epoch']==epoch
                    and account['voice']==voice and account['speed']==speed and self.library.current.version==version)

    def prefetch(self,user,account,snapshot,view):
        if not self.preload or not self.preparation or self.closing:return
        engine=Reader(snapshot)
        if view.get('screen') in ('list','results'):
            page=view.get('page',0);entries=engine.sequence(view)[page*8:(page+1)*8]
        elif view.get('screen')=='entry':
            current=snapshot.entry(view.get('entry'))
            if not current:return
            sequence=engine.sequence(view.get('origin',{'document':current.document}))
            index=next((i for i,e in enumerate(sequence) if e.id==current.id),0)
            entries=sequence[index:index+3]
        else:return
        tag=(user,account['epoch'],snapshot.version,account['voice'],account['speed'])
        for entry in entries:
            if entry.speech:
                phrase=chunks(entry.speech,3500)[0]
                if not self.state.meta(audio_key(phrase,account['voice'],account['speed'])):
                    self.preparation.enqueue(phrase,account['voice'],account['speed'],tag)

    async def protected(self,bot,user,epoch,method,**kwargs):
        # Callers hold this recipient's gate through request completion. Revocation
        # takes the same gate, then commits the new epoch. No queued task bypasses it.
        for attempt in range(2):
            if not self.state.allowed(user,epoch):
                raise PermissionError('Access revoked.')
            try:
                return await getattr(bot,method)(**kwargs)
            except RetryAfter as error:
                delay=error.retry_after.total_seconds() if hasattr(error.retry_after,'total_seconds') else float(error.retry_after)
                if attempt or delay>5:
                    raise
                await asyncio.sleep(delay)

    async def discard(self,bot,chat,message):
        if message:
            try:
                await bot.delete_message(chat_id=chat,message_id=message)
            except TelegramError:
                try:
                    await bot.edit_message_text(chat_id=chat,message_id=message,text='Closed. Use /menu.',reply_markup=None)
                except TelegramError:
                    pass

    async def clean(self,bot,user,chat,field):
        message=self.state.session(chat)[field]
        if message:
            await self.discard(bot,chat,message)
            self.state.save_session(chat,**{field:None})

    async def _text(self,bot,user,epoch,chat,message,text,markup):
        if message:
            try:
                await self.protected(bot,user,epoch,'edit_message_text',chat_id=chat,message_id=message,
                                     text=text,parse_mode='HTML',reply_markup=markup,link_preview_options={'is_disabled':True})
                return message
            except BadRequest as error:
                if 'message is not modified' in str(error).lower():return message
                if not any(term in str(error).lower() for term in ('message to edit not found',"message can't be edited",'message_id_invalid')):
                    raise
        self.reserve_text_delivery(chat)
        result=await self.protected(bot,user,epoch,'send_message',chat_id=chat,text=text,parse_mode='HTML',
                                    reply_markup=markup,link_preview_options={'is_disabled':True})
        return result.message_id

    def reserve_text_delivery(self,chat):
        if self._update_task is not asyncio.current_task():return
        self._text_sequence+=1
        if not self.state.reserve_text_delivery(self._update_id,self._text_sequence,chat):
            LOG.warning('An earlier French message delivery is uncertain; automatic repeat suppressed. Use /menu for a fresh reply.')
            raise NetworkError('Previous message delivery is uncertain; request /menu to recover.')

    async def render(self,bot,user,chat,view,notice='',automatic=True,fresh=False):
        account=self.state.user(user)
        if not account or not account['active']:raise PermissionError('Access revoked.')
        if not self.automatic_audio:account['auto_audio']=False
        snapshot=self.library.current
        if snapshot is None:raise ValueError('No accepted library is available.')
        engine=Reader(snapshot,self.library.status)
        screen=engine.render(view,account,self.state.readers() if account['owner'] else (),
                             self.state.invitations() if account['owner'] else (),notice)
        entry=snapshot.entry(screen.view.get('entry')) if screen.view.get('screen')=='entry' else None
        if not automatic and entry and entry.speech:
            screen.rows.insert(0,[('Retry audio',{'kind':'listen','entry':entry.id,'audio_part':0})])
        session=self.state.session(chat);generation=session['generation']+1
        self.state.save_session(chat,generation=generation,view=screen.view)
        actions=[action for row in screen.rows for _,action in row]
        handles=iter(self.state.buttons(user,chat,generation,snapshot.version,actions))
        rows=[[InlineKeyboardButton(label,callback_data=next(handles)) for label,_ in row] for row in screen.rows]
        markup=InlineKeyboardMarkup(rows)
        await self.clean(bot,user,chat,'audio')
        anchor_markup=InlineKeyboardMarkup(rows[-1:]) if screen.continuation is not None else markup
        # Start/Menu must produce a visible new reply. Editing an old anchor and
        # deleting /start can leave the client at its Start screen or old scroll.
        anchor=await self._text(bot,user,account['epoch'],chat,None if fresh else session['anchor'],screen.text,anchor_markup)
        self.state.save_session(chat,anchor=anchor)
        if fresh and session['anchor']!=anchor:
            await self.discard(bot,chat,session['anchor'])
        if screen.continuation is not None:
            continuation=await self._text(bot,user,account['epoch'],chat,session['continuation'],screen.continuation,markup)
            self.state.save_session(chat,continuation=continuation)
        else:
            await self.clean(bot,user,chat,'continuation')
        if screen.position:self.state.setting(user,position=screen.position)
        if automatic:
            if entry and entry.speech and account.get('auto_audio'):
                await self.start_audio(bot,user,chat,{'entry':entry.id,'audio_part':0},automatic=True)
            self.prefetch(user,account,snapshot,screen.view)
        return screen

    async def deny(self,bot,user,chat):
        now=time.monotonic()
        if now-self.denials.get(user,-60)<15:return
        if len(self.denials)>1000:self.denials.clear()
        self.denials[user]=now
        text=('Owner setup is not finished yet. On the Windows PC, open the OWNER PAIRING link from the bot terminal and tap Start. '
              'If it expired, run PAIR_OWNER.bat on that PC to get a fresh link. The bot can stay running.') if not self.state.has_owner() else 'Invitation required. Ask the owner for an invitation link.'
        try:
            self.reserve_text_delivery(chat)
            await bot.send_message(chat_id=chat,text=text)
        except TelegramError:pass

    async def handle(self,update:Update,context):
        # One SQLite connection has one transaction owner even when a test or
        # framework submits handlers concurrently for different recipients.
        async with self.update_gate:
            self._update_task=asyncio.current_task();self._update_id=update.update_id;self._text_sequence=0
            try:
                with self.state.durable_update(update.update_id) as fresh:
                    if fresh:await self._handle(update,context)
            finally:self._update_task=None;self._update_id=None

    async def _handle(self,update:Update,context):
        bot=context.bot;user=update.effective_user;chat=update.effective_chat;callback=update.callback_query
        if callback:
            try:await bot.answer_callback_query(callback_query_id=callback.id)
            except TelegramError:pass
        if self.closing:return
        if not user or not chat or chat.type!='private' or chat.id!=user.id:return
        if self.state.seen(update.update_id):return
        self.health['last_update']=time.time();self.health['polling']='connected'
        async with self.gates[user.id]:
            try:
                message=update.message
                text=(message.text or '').strip() if message else ''
                if text.startswith('/start '):
                    token=text.split(maxsplit=1)[1]
                    self.state.redeem(token,user.id,user.full_name)
                if not self.state.allowed(user.id):
                    await self.deny(bot,user.id,chat.id)
                    return
                session=self.state.session(chat.id);notice='';action=None;fresh=False
                if callback:
                    origin=getattr(callback.message,'message_id',None)
                    if origin not in (session['anchor'],session['continuation'],session['audio']):
                        return
                    resolved=self.state.resolve(callback.data,user.id,chat.id)
                    if not resolved:
                        action={'screen':'menu'};notice='This screen expired. Choose an action below.'
                    elif resolved['version']!=self.library.current.version:
                        previous=session['view']
                        action=previous.get('origin',{'screen':'browse'}) if previous.get('screen')=='entry' else previous
                        notice='Notes updated. Choose from the refreshed list.'
                    else:action=resolved['action']
                elif text.startswith('/'):
                    command=text.split()[0].split('@')[0].lower()
                    fresh=command in ('/start','/menu')
                    action={'screen':{'/start':'menu','/menu':'menu','/search':'search','/help':'help','/settings':'settings'}.get(command,'help')}
                elif text:
                    action={'screen':'results','query':text[:200],'page':0}
                else:
                    action={'screen':'menu'}
                kind=action.get('kind')
                if kind=='listen':
                    await self.start_audio(bot,user.id,chat.id,action)
                elif kind=='remove_audio':
                    task=self.jobs.pop(user.id,None)
                    if task:task.cancel()
                    await self.clean(bot,user.id,chat.id,'audio')
                else:
                    if kind in ('invite','revoke','cancel_invite') and not self.state.allowed(user.id,owner=True):
                        raise PermissionError('Owner access required.')
                    if kind=='invite':
                        token=self.state.invitation()
                        action={'screen':'invitation','link':f'https://t.me/{BOT_USERNAME}?start={token}','hash':digest(token.encode())}
                    elif kind=='cancel_invite':
                        self.state.cancel_invitation(action['hash']);action={'screen':'invitations'}
                    elif kind=='revoke':
                        target=int(action['target'])
                        if target==user.id:raise ValueError('Owner cannot revoke their own access.')
                        async with self.gates[target]:
                            self.state.revoke(user.id,target)
                            task=self.jobs.pop(target,None)
                            if task:task.cancel()
                        action={'screen':'readers'};notice='Access revoked. Previously received material may remain.'
                    elif kind=='setting':
                        self.state.setting(user.id,voice=action.get('voice'),speed=action.get('speed'),auto_audio=action.get('auto_audio'))
                        action={'screen':'settings'}
                    elif kind=='continue':
                        account=self.state.user(user.id);position=json.loads(account['position']) if account.get('position') else {}
                        action=dict(position,screen='entry') if self.library.current.entry(position.get('entry')) else {'screen':'list','document':position.get('document'),'page':0}
                        if action['screen']!='entry':notice='Your previous entry changed. Choose from this list.'
                    await self.render(bot,user.id,chat.id,action,notice,fresh=fresh)
                # Only remove the user's processed input after a successful render.
                if message:
                    try:await bot.delete_message(chat_id=chat.id,message_id=message.message_id)
                    except TelegramError:pass
            except PermissionError:
                pass
            except Forbidden:
                self.health['polling']='reader chat unavailable'
            except (NetworkError,RetryAfter):
                self.health['polling']='network interrupted; retry with /menu'
                LOG.warning('Telegram operation deferred: network or rate limit')
            except sqlite3.Error:
                # Do not acknowledge an update whose required local effects
                # failed. The outer update transaction rolls back its receipt.
                raise
            except Exception as error:
                self.errors.append(type(error).__name__)
                LOG.error('Reader request failed: %s',type(error).__name__)
                # Keep existing material and state; a fresh menu is always recoverable.
                try:
                    if self.state.allowed(user.id):
                        await self.render(bot,user.id,chat.id,{'screen':'menu'},'Could not finish that action. Please try again.')
                except Exception:pass

    async def start_audio(self,bot,user,chat,action,automatic=False):
        entry=self.library.current.entry(action.get('entry'))
        if not entry or not entry.speech:return
        parts=chunks(entry.speech,3500);part=int(action.get('audio_part',0))
        if not 0<=part<len(parts):return
        account=self.state.user(user);session=self.state.session(chat)
        key=audio_key(parts[part],account['voice'],account['speed'])
        if session['audio'] and self.state.meta('displayed_audio:'+str(chat))==key:return
        old=self.jobs.pop(user,None)
        if old:old.cancel()
        view=self.state.session(chat)['view']
        if not self.speech:
            if not automatic:await self.render(bot,user,chat,view,'Pronunciation unavailable. See the setup instructions.',automatic=False)
            return
        if len(self.jobs)>=20:
            if not automatic:await self.render(bot,user,chat,view,'Pronunciation is busy. Try again shortly.',automatic=False)
            return
        # No preparatory edit or deletion on Listen. A cached clip now needs only
        # one media request, and an already visible clip needs no request at all.
        task=asyncio.create_task(self.audio_job(bot,user,chat,account['epoch'],session['generation'],
                                               self.library.current.version,parts[part],entry.title,account['voice'],account['speed']))
        self.jobs[user]=task
        task.add_done_callback(lambda completed:self.job_done(user,completed))

    def job_done(self,user,task):
        if self.jobs.get(user) is task:self.jobs.pop(user,None)
        if not task.cancelled():
            try:task.exception()
            except Exception:pass

    async def audio_job(self,bot,user,chat,epoch,generation,version,text,title,voice,speed):
        key=audio_key(text,voice,speed)
        try:
            cached=self.state.meta(key)
            data=None
            for attempt in range(2):
                if not cached:
                    if not self.state.allowed(user,epoch):return
                    data=await self.preparation.get(text,voice,speed)
                retry=False
                async with self.gates[user]:
                    if not self.state.allowed(user,epoch) or self.state.session(chat)['generation']!=generation or self.library.current.version!=version:return
                    handle=self.state.buttons(user,chat,generation,version,[{'kind':'remove_audio'}],replace=False)[0]
                    markup=InlineKeyboardMarkup([[InlineKeyboardButton('Remove audio',callback_data=handle)]])
                    caption=short(title,150)+' · '+voice.title()+' · '+('Slow' if speed=='0.8' else 'Normal')
                    # An InputFile inside InputMediaAudio must have an attachment
                    # reference; otherwise edits serialize a null media field.
                    media=cached or InputFile(io.BytesIO(data),filename='french.mp3',attach=True)
                    message_id=self.state.session(chat)['audio']
                    try:
                        if message_id:
                            try:
                                sent=await self.protected(bot,user,epoch,'edit_message_media',chat_id=chat,message_id=message_id,
                                                          media=InputMediaAudio(media,caption=caption,title=short(title,80),performer='French_Learning'),reply_markup=markup)
                            except BadRequest as error:
                                if not any(term in str(error).lower() for term in ('message to edit not found',"message can't be edited",'message_id_invalid')):raise
                                self.state.save_session(chat,audio=None);message_id=None
                        if not message_id:
                            sent=await self.protected(bot,user,epoch,'send_audio',chat_id=chat,audio=media,
                                                      caption=caption,title=short(title,80),performer='French_Learning',reply_markup=markup)
                    except BadRequest:
                        if not cached or attempt:raise
                        self.state.set_meta(key,'');cached=None;retry=True
                    if not retry:
                        self.state.save_session(chat,audio=sent.message_id)
                        self.state.set_meta('displayed_audio:'+str(chat),key)
                        if sent.audio:self.state.set_meta(key,sent.audio.file_id)
                        self.health['speech']='ready'
                if not retry:break
            self.touch_cache(text,voice,speed)
            await asyncio.to_thread(self.prune_cache)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.health['speech']='temporarily unavailable'
            async with self.gates[user]:
                if self.state.allowed(user,epoch) and self.state.session(chat)['generation']==generation:
                    try:await self.render(bot,user,chat,self.state.session(chat)['view'],'Pronunciation unavailable. Use Retry audio.',automatic=False)
                    except Exception:pass

    def touch_cache(self,text,voice,speed):
        if not self.speech or not hasattr(self.speech,'cache'):return
        from ..speech import CACHE_VERSION,VOICES,RATES
        key=digest((CACHE_VERSION+'\0'+VOICES[voice]+'\0'+RATES[speed]+'\0'+text.strip()).encode('utf-8'))
        try:os.utime(Path(self.speech.cache)/(key+'.mp3'),None)
        except OSError:pass

    def prune_cache(self):
        if not self.speech or not hasattr(self.speech,'cache'):return
        files=[]
        for p in Path(self.speech.cache).glob('*.mp3'):
            # Worker temporaries have 32-character UUID names. Never evict them.
            if not re.fullmatch(r'[0-9a-f]{64}\.mp3',p.name):continue
            try:
                stat=p.stat();files.append((stat.st_mtime,stat.st_size,p))
            except OSError:pass
        size=sum(item[1] for item in files)
        for _,length,p in sorted(files):
            if size<=self.cache_limit:break
            try:p.unlink()
            except OSError:pass
            else:size-=length

    async def watch(self):
        while True:
            await asyncio.sleep(2)
            previous=self.library.status
            self.scan_job=asyncio.create_task(asyncio.to_thread(self.library.scan))
            await asyncio.shield(self.scan_job)
            self.scan_job=None
            if self.library.status!=previous:print('Library:',self.library.status,flush=True)

    async def close(self):
        self.closing=True
        if self.watcher:self.watcher.cancel()
        tasks=list(self.jobs.values())
        for task in tasks:task.cancel()
        if self.preparation:await self.preparation.close()
        if self.speech:self.speech.close()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.watcher:await asyncio.gather(self.watcher,return_exceptions=True)
        if self.scan_job:await asyncio.gather(self.scan_job,return_exceptions=True)


def attach(application:Application,service:Service):
    service.application=application
    application.add_handler(TypeHandler(Update,service.handle))
    return application
