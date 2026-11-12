import concurrent.futures
import html
import json
from pathlib import Path
import tempfile
import unittest

from french_notes.telegram_bot.library import Source, build
from french_notes.telegram_bot.reader import Reader, chunks, entry_text
from french_notes.telegram_bot.refresh import LibraryManager
from french_notes.telegram_bot.state import State


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.notes=self.root/'notes';self.notes.mkdir()
        self.file=self.notes/'Practice.txt';self.file.write_text('Bonjour    |    سلام    |    Hello\n','utf-8')
        self.state=State(self.root/'state',self.notes);self.addCleanup(self.state.close)
        self.state.redeem(self.state.invitation('owner'),1,'Owner')
        self.user=self.state.user(1)

    def test_atomic_invitation_across_independent_connections(self):
        token=self.state.invitation()
        def redeem(i):
            state=State(self.root/'state',self.notes)
            try:return state.redeem(token,i,'Reader')
            finally:state.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(redeem,range(2,26)))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(self.state.readers()),2)

    def test_expired_cancelled_and_owner_links(self):
        token=self.state.invitation()
        with self.state.transaction() as db:db.execute("UPDATE invitations SET expires=0 WHERE kind='reader'")
        self.assertFalse(self.state.redeem(token,2,'Other'))
        token=self.state.invitation();self.state.cancel_invitation(self.state.invitations()[0]['hash'])
        self.assertFalse(self.state.redeem(token,2,'Other'))
        with self.assertRaises(ValueError):self.state.invitation('owner')
        self.assertFalse(self.state.redeem('guess',2,'Other'))

    def test_revoke_invalidates_epoch_callbacks_and_reinvite(self):
        self.state.redeem(self.state.invitation(),2,'Reader')
        self.state.save_session(2,generation=1)
        handle=self.state.buttons(2,2,1,'v',[{'screen':'menu'}])[0]
        epoch=self.state.user(2)['epoch']
        self.assertIsNotNone(self.state.resolve(handle,2,2))
        self.assertIsNone(self.state.resolve(handle,1,2))
        self.state.revoke(1,2)
        self.assertFalse(self.state.allowed(2))
        self.assertIsNone(self.state.resolve(handle,2,2))
        self.state.redeem(self.state.invitation(),2,'Reader')
        self.assertFalse(self.state.allowed(2,epoch))
        with self.assertRaises(PermissionError):self.state.revoke(2,1)

    def test_no_silent_identity_reset_and_persisted_settings(self):
        self.state.bind_bot(100)
        with self.assertRaises(ValueError):self.state.bind_bot(101)
        self.state.setting(1,voice='henri',speed='0.8',position={'entry':'abc'})
        other=State(self.root/'state',self.notes)
        try:
            self.assertTrue(other.has_owner());self.assertEqual(other.user(1)['voice'],'henri')
        finally:other.close()
        wrong=self.root/'lost';wrong.mkdir();(wrong/'identity.json').write_text('{}')
        with self.assertRaises(ValueError):State(wrong,self.notes)
        with self.assertRaises(ValueError):State(self.notes/'state',self.notes)

    def test_complete_long_unicode_pages_and_html(self):
        text=('« Élève » فارسی 😀 <tag> & "quote"\n'*700)
        parts=chunks(text)
        self.assertEqual(''.join(parts),text)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2<=3300 for p in parts))
        snap=build((Source('Long.txt',text.encode()),));reader=Reader(snap)
        entry=snap.entries[0]
        rendered=[]
        for i in range(len(chunks(entry_text(entry)))):
            screen=reader.render({'screen':'entry','entry':entry.id,'part':i},self.user)
            rendered.append(html.unescape(screen.continuation))
            self.assertIn('&lt;tag&gt;',screen.continuation)
        self.assertEqual(''.join(rendered),entry_text(entry))

    def test_results_back_and_complete_six_fields(self):
        rows='\n'.join(f'Expression {i}    |    معنی {i}    |    Meaning {i}    |    Exemple {i}    |    ترجمه {i}    |    Translation {i}' for i in range(20))
        snap=build((Source('Expressions.txt',rows.encode()),));reader=Reader(snap)
        origin={'screen':'results','query':'Meaning','page':1}
        listing=reader.render(origin,self.user)
        action=listing.rows[0][0][1]
        screen=reader.render(action,self.user)
        self.assertIn('Exemple 8',html.unescape(screen.text))
        self.assertEqual(screen.rows[-2][0][1],origin)
        self.assertEqual(screen.position['origin'],origin)

    def test_empty_sections_and_buttons_are_flat(self):
        snap=build((Source('Other.txt',b'## Empty\n## Filled\nHello    |    Farsi    |    English\n'),));reader=Reader(snap)
        for view in ({'screen':'sections','document':'Other.txt'},{'screen':'list','document':'Other.txt','section':'Empty'}):
            screen=reader.render(view,self.user)
            self.assertTrue(all(len(button)==2 and isinstance(button[0],str) for row in screen.rows for button in row))
        self.assertIn('No saved entries',screen.text)

    def test_heading_translations_are_readable_even_in_empty_sections(self):
        text='1. Cause    |    علت    |    Reason\nBonjour    |    سلام    |    Hello\n2. Addition    |    اضافه    |    Addition\n'
        snap=build((Source('Headings.txt',text.encode()),));reader=Reader(snap)
        section=reader.render({'screen':'list','document':'Headings.txt','section':'2. Addition'},self.user)
        self.assertIn('اضافه',html.unescape(section.text))
        entry=reader.render({'screen':'entry','entry':snap.entries[0].id},self.user)
        self.assertIn('علت',html.unescape(entry.text));self.assertIn('Reason',html.unescape(entry.text))

    def test_verified_refresh_and_external_change_hold(self):
        journal=self.root/'history.json';journal.write_text('[]')
        manager=LibraryManager(self.notes,self.state,journal=journal)
        manager.publish(manager.candidate());old=manager.current
        self.file.write_text('Changed    |    تغییر    |    New\n','utf-8')
        self.assertFalse(manager.scan());self.assertFalse(manager.scan())
        self.assertEqual(manager.current,old)
        self.assertEqual(manager.status,'Update awaiting local review')
        candidate=manager.candidate()
        journal.write_text(json.dumps([{'name':'Practice.txt','status':'pending','after':candidate.manifest()['Practice.txt']}]))
        self.assertFalse(manager.scan())
        journal.write_text(json.dumps([{'name':'Practice.txt','status':'committed','after':candidate.manifest()['Practice.txt']}]))
        self.assertTrue(manager.scan())
        self.assertNotEqual(manager.current.version,old.version)
        self.file.unlink()
        self.assertFalse(manager.scan());self.assertIn('verified copy',manager.status)

    def test_review_accepts_only_exact_candidate_and_survives_restart(self):
        manager=LibraryManager(self.notes,self.state);manager.publish(manager.candidate())
        self.file.write_text('Other\n')
        self.assertFalse(manager.review(lambda prompt:'NO'))
        candidate=manager.candidate()
        self.assertTrue(manager.review(lambda prompt:'ACCEPT '+candidate.version[:12]))
        restored=LibraryManager(self.notes,self.state)
        self.assertEqual(restored.current.version,candidate.version)
        self.assertEqual(restored.current.entries,manager.current.entries)

    def test_no_invitation_secret_in_persisted_screen(self):
        snap=build((Source('Example.txt',b'Hello\n'),));reader=Reader(snap)
        token=self.state.invitation()
        screen=reader.render({'screen':'invitation','link':'https://t.me/bot?start='+token,'hash':'hash'},self.user)
        self.assertIn(token,screen.text)
        self.assertNotIn(token,json.dumps(screen.view))
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM invitations WHERE hash=?',(token,)).fetchone()[0],0)


if __name__=='__main__':unittest.main()
