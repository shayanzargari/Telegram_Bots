"""Pure navigation and readable text; deliberately independent of Telegram."""
from __future__ import annotations

from dataclasses import dataclass
import html
import json

PAGE_SIZE = 8


def short(text, limit=60):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit-1] + '…'


def chunks(text, limit=3300):
    """Telegram-safe UTF-16 budget; joining parts recovers the complete text."""
    parts = []
    while text:
        units, end = 0, 0
        for char in text:
            cost = 2 if ord(char)>0xffff else 1
            if units + cost > limit:
                break
            units += cost
            end += 1
        if end < len(text):
            boundary = text.rfind('\n',0,end)
            if boundary > end//2:
                end = boundary + 1
        parts.append(text[:end])
        text = text[end:]
    return tuple(parts) or ('',)


def heading_text(record):
    if record.cells:
        return '\n'.join(record.cells)
    return record.raw.lstrip('#').strip()


def entry_text(entry):
    output=[]
    if entry.heading:
        output.extend([heading_text(entry.heading),''])
    elif entry.section:
        output.extend([entry.section,''])
    table_headers=None
    for record in entry.records:
        if record.kind == 'row':
            labels = ('Expression','Farsi meaning','English meaning','French example','Farsi translation','English translation') if len(record.cells)==6 else ('French','فارسی','English')
            for label,value in zip(labels,record.cells):
                output.extend([label,value if value else '—',''])
            if record.secondary:
                output.extend(['Other tenses',record.secondary,''])
            table_headers=None
        elif record.kind == 'table':
            cells=record.cells
            if table_headers is None:
                table_headers=cells
                output.extend([' / '.join(c.strip() for c in cells),''])
            else:
                for index,cell in enumerate(cells):
                    label=table_headers[index].strip() if index<len(table_headers) else 'Column '+str(index+1)
                    output.extend([label,cell.strip(),''])
        else:
            output.append(record.raw)
            if record.kind not in ('blank',):
                table_headers=None
    return '\n'.join(output)


def nav(back=None):
    row=[]
    if back: row.append(('Back',back))
    row.extend([('Menu',{'screen':'menu'}),('Search',{'screen':'search'})])
    return row


@dataclass
class Screen:
    text: str
    rows: list
    view: dict
    continuation: str | None = None
    position: dict | None = None


class Reader:
    def __init__(self,snapshot,status='Saved material'):
        self.library=snapshot
        self.status=status

    def category(self,key):
        doc=self.library.document(key)
        return {'screen':'sections' if doc and doc.sections else 'list','document':key,'page':0}

    def sequence(self,view):
        if view.get('screen')=='results':
            return self.library.search(view.get('query',''))
        doc=self.library.document(view.get('document'))
        return tuple(e for e in doc.entries if view.get('section') is None or e.section==view['section']) if doc else ()

    def render(self,view,user,readers=(),invitations=(),notice=''):
        view=dict(view);screen=view.get('screen','menu');rows=[];body='';continuation=None;position=None
        back={'screen':'menu'}
        if screen=='menu':
            body='French_Learning\nRead only · '+self.status
            rows=[[('Browse material',{'screen':'browse'}),('Search',{'screen':'search'})]]
            if user.get('position'):
                rows.append([('Continue reading',{'kind':'continue'})])
            rows.append([('Settings',{'screen':'settings'})])
            if user.get('owner'): rows.append([('Access',{'screen':'access'})])
        elif screen=='browse':
            body='Browse material'
            buttons=[(d.title,self.category(d.id)) for d in self.library.documents]
            rows=[buttons[i:i+2] for i in range(0,len(buttons),2)]
            rows.append(nav())
        elif screen=='sections':
            doc=self.library.document(view.get('document'))
            if not doc: return self.render({'screen':'browse'},user,notice='Category changed. Choose a category.')
            body=doc.title+'\nChoose a section'
            rows=[[('All entries',{'screen':'list','document':doc.id,'page':0})]]
            rows.extend([(short(s),{'screen':'list','document':doc.id,'section':s,'page':0})] for s in doc.sections)
            rows.append(nav({'screen':'browse'}))
        elif screen in ('list','results'):
            sequence=self.sequence(view);total=len(sequence)
            page=max(0,min(int(view.get('page',0)),max(0,(total-1)//PAGE_SIZE)))
            view['page']=page
            doc=self.library.document(view.get('document'))
            title='Search: '+view.get('query','') if screen=='results' else doc.title if doc else 'Category unavailable'
            if screen=='list' and view.get('section'):
                heading=next((r for r in doc.records if r.kind=='heading' and r.section==view['section']),None) if doc else None
                title+='\n'+(heading_text(heading) if heading else view['section'])
            body=title+f'\n{total} '+('matches' if screen=='results' else 'entries')+f' · page {page+1}/{max(1,(total+7)//8)}'
            for e in sequence[page*8:(page+1)*8]:
                label=e.title
                if screen=='results': label=self.library.document(e.document).title+' · '+label
                rows.append([(short(label),{'screen':'entry','entry':e.id,'origin':view,'part':0})])
            if not total: body+='\n\nNo matches. Try another word.' if screen=='results' else '\n\nNo saved entries.'
            paging=[]
            if page: paging.append(('Previous page',dict(view,page=page-1)))
            if (page+1)*8<total: paging.append(('Next page',dict(view,page=page+1)))
            if paging: rows.append(paging)
            if screen=='results': rows.append([('New search',{'screen':'search'})])
            back={'screen':'search'} if screen=='results' else {'screen':'sections','document':doc.id} if doc and doc.sections else {'screen':'browse'}
            rows.append(nav(back))
        elif screen=='entry':
            entry=self.library.entry(view.get('entry'))
            origin=view.get('origin') or (self.category(entry.document) if entry else {'screen':'browse'})
            if not entry: return self.render(origin,user,notice='This entry changed. Choose it from the updated list.')
            doc=self.library.document(entry.document)
            if origin.get('screen')=='sections': origin={'screen':'list','document':doc.id,'page':0}
            sequence=self.sequence(origin)
            index=next((i for i,e in enumerate(sequence) if e.id==entry.id),None)
            if index is None:
                origin={'screen':'list','document':doc.id,'page':0};sequence=self.sequence(origin)
                index=next(i for i,e in enumerate(sequence) if e.id==entry.id)
            parts=chunks(entry_text(entry));part=max(0,min(int(view.get('part',0)),len(parts)-1));view['part']=part;view['origin']=origin
            body=f'{doc.title} · entry {index+1}/{len(sequence)}'
            if len(parts)>1:
                body+=f'\nLong note · part {part+1}/{len(parts)}'
                continuation=parts[part]
                paging=[]
                if part: paging.append(('Previous part',dict(view,part=part-1)))
                if part+1<len(parts): paging.append(('Next part',dict(view,part=part+1)))
                if paging: rows.append(paging)
            else:
                body+='\n\n'+parts[0]
            if entry.speech:
                speech_parts=chunks(entry.speech,3500)
                if not user.get('auto_audio',True) or len(speech_parts)>1:
                    buttons=[('Listen' if len(speech_parts)==1 else f'Listen {i+1}/{len(speech_parts)}',
                              {'kind':'listen','entry':entry.id,'audio_part':i}) for i in range(len(speech_parts))]
                    rows.extend(buttons[i:i+2] for i in range(0,len(buttons),2))
            paging=[]
            if index: paging.append(('Previous entry',{'screen':'entry','entry':sequence[index-1].id,'origin':origin,'part':0}))
            if index+1<len(sequence): paging.append(('Next entry',{'screen':'entry','entry':sequence[index+1].id,'origin':origin,'part':0}))
            if paging: rows.append(paging)
            # Back always restores the originating list page, not a recalculated position.
            rows.append([('Back to results' if origin.get('screen')=='results' else 'Back to list',origin)])
            rows.append(nav())
            position={'entry':entry.id,'document':doc.id,'origin':origin,'part':part}
        elif screen=='search':
            body='Search\nType a French, English, or Farsi word.'
            rows=[[('Cancel',{'screen':'menu'})]]
        elif screen=='settings':
            body='Pronunciation\nChoose a voice and speed.'
            rows=[[(name.title()+(' · selected' if user['voice']==name else ''),{'kind':'setting','voice':name})] for name in ('denise','henri','vivienne')]
            rows.append([(label+(' · selected' if user['speed']==speed else ''),{'kind':'setting','speed':speed}) for label,speed in [('Normal','1'),('Slow','0.8')]])
            rows.append([(label+(' · selected' if user.get('auto_audio',True)==value else ''),{'kind':'setting','auto_audio':value}) for label,value in [('Audio with entry',True),('Listen button only',False)]])
            rows.append(nav({'screen':'menu'}))
        elif screen in ('access','readers','reader','revoke','invitations','invitation'):
            if not user.get('owner'): return self.render({'screen':'menu'},user)
            if screen=='access':
                body='Access\nInvitations are single use and expire after 24 hours.'
                rows=[[('Create invitation',{'kind':'invite'})],[('Readers',{'screen':'readers','page':0})],[('Outstanding invitations',{'screen':'invitations'})],nav({'screen':'menu'})]
            elif screen=='readers':
                page=max(0,min(int(view.get('page',0)),max(0,(len(readers)-1)//8)));view['page']=page
                body=f'Readers · page {page+1}'
                rows=[[(short(r['name'])+(' · owner' if r['owner'] else ' · active' if r['active'] else ' · revoked'),{'screen':'reader','target':r['id']})] for r in readers[page*8:(page+1)*8]]
                paging=[]
                if page:paging.append(('Previous page',dict(view,page=page-1)))
                if (page+1)*8<len(readers):paging.append(('Next page',dict(view,page=page+1)))
                if paging: rows.append(paging)
                rows.append(nav({'screen':'access'}))
            elif screen in ('reader','revoke'):
                target=next((r for r in readers if r['id']==view.get('target')),None)
                if not target:return self.render({'screen':'readers'},user,readers,invitations)
                body=target['name']+'\n'+str(target['id'])+'\n'+('Owner' if target['owner'] else 'Active' if target['active'] else 'Revoked')
                if not target['owner'] and target['active']:
                    body+='\n\nRevoke this reader’s access?' if screen=='revoke' else ''
                    rows.append([('Confirm revoke' if screen=='revoke' else 'Revoke access',{'kind':'revoke','target':target['id']} if screen=='revoke' else dict(view,screen='revoke'))])
                rows.append(nav({'screen':'readers'}))
            elif screen=='invitations':
                body='Outstanding invitations\n'+(str(len(invitations))+' unused invitations.' if invitations else 'No outstanding invitations.')
                page=max(0,min(int(view.get('page',0)),max(0,(len(invitations)-1)//8)));view['page']=page
                rows=[[(f'Cancel invitation {page*8+i+1}',{'kind':'cancel_invite','hash':r['hash']})] for i,r in enumerate(invitations[page*8:(page+1)*8])]
                paging=[]
                if page:paging.append(('Previous page',dict(view,page=page-1)))
                if (page+1)*8<len(invitations):paging.append(('Next page',dict(view,page=page+1)))
                if paging:rows.append(paging)
                rows.append(nav({'screen':'access'}))
            else:
                body='Invitation created\nSingle use · expires in 24 hours\n\n'+view.pop('link','')
                rows=[[('Cancel this invitation',{'kind':'cancel_invite','hash':view.pop('hash','')})],nav({'screen':'access'})]
                view={'screen':'access'}  # Do not persist the raw invitation secret.
        elif screen=='help':
            body='Browse categories or type a word to search.\nBack restores your list. Menu returns home.\nUse Listen for French pronunciation.\n\nThis is read only. The Windows PC must stay running, awake, and online.'
            rows=[nav()]
        else:
            return self.render({'screen':'menu'},user)
        if notice:body=notice+'\n\n'+body
        return Screen(html.escape(body),rows,view,html.escape(continuation) if continuation is not None else None,position)
