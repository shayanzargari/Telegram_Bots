import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from french_notes.store import NotesError, NotesStore, atomic_json, sha


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root / 'notes'
        self.notes.mkdir()
        self.path = self.notes / 'Overall Examples.txt'
        self.original = b'\xef\xbb\xbf' + 'Bonjour.\r\n\r\nJe suis là. فارسی\r\n'.encode('utf-8')
        self.path.write_bytes(self.original)
        self.store = NotesStore(self.notes, self.root / 'state')

    def add(self, text='Un café, s’il vous plaît.\nMerci ! فارسی', **options):
        return self.store.append(self.path.name, text, self.store.read(self.path.name)['revision'], str(uuid.uuid4()), **options)

    def test_open_read_search_do_not_modify_notes(self):
        self.store.categories()
        self.store.read(self.path.name)
        self.assertEqual(self.store.search('فارسی')['total'], 1)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.notes.iterdir()), [self.path])

    @unittest.skipUnless(os.name == 'nt', 'Windows file locks')
    def test_history_write_recovers_from_a_brief_windows_file_lock(self):
        target = self.root / 'locked-history.json'
        target.write_text('{"old": true}', encoding='utf-8')
        original_replace = os.replace
        calls = []

        def briefly_locked(source, destination):
            calls.append(destination)
            if len(calls) <= 2:
                error = PermissionError('File is temporarily busy')
                error.winerror = 32
                raise error
            return original_replace(source, destination)

        with patch('french_notes.store.os.replace', side_effect=briefly_locked), patch('french_notes.store.time.sleep'):
            atomic_json(target, {'new': True})
        self.assertEqual(json.loads(target.read_text('utf-8')), {'new': True})
        self.assertEqual(len(calls), 3)

    @unittest.skipUnless(os.name == 'nt', 'Windows file locks')
    def test_persistent_windows_file_lock_keeps_existing_history(self):
        target = self.root / 'locked-history.json'
        original = b'{"old": true}'
        target.write_bytes(original)
        error = PermissionError('File remains busy')
        error.winerror = 5
        with patch('french_notes.store.os.replace', side_effect=error) as replace, patch('french_notes.store.time.sleep'):
            with self.assertRaises(PermissionError):
                atomic_json(target, {'new': True})
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_append_preserves_original_bytes_and_accents(self):
        result = self.add()
        expected = self.original + 'Un café, s’il vous plaît.\r\nMerci ! فارسی\r\n'.encode('utf-8')
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(result['added_lines'], 2)
        self.assertEqual(next(self.store.backups.glob('*.bin')).read_bytes(), self.original)

    def test_undo_is_byte_exact_and_survives_restart(self):
        result = self.add()
        reloaded = NotesStore(self.notes, self.root / 'state')
        reloaded.undo(self.path.name, result['file']['revision'], result['file']['undo_id'])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_each_file_has_independent_undo(self):
        other = self.notes / 'Questions.txt'
        other.write_text('Pourquoi ?\n', encoding='utf-8')
        first = self.add()
        self.store.append(other.name, 'Quand ?', sha(other.read_bytes()), str(uuid.uuid4()))
        self.store.undo(self.path.name, first['file']['revision'], first['file']['undo_id'])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn('Quand ?', other.read_text('utf-8'))

    def test_external_edit_blocks_append_and_undo(self):
        revision = self.store.read(self.path.name)['revision']
        self.path.write_bytes(self.original + b'External edit\r\n')
        changed = self.path.read_bytes()
        with self.assertRaises(NotesError):
            self.store.append(self.path.name, 'Nouvelle note.', revision, str(uuid.uuid4()))
        self.assertEqual(self.path.read_bytes(), changed)
        result = self.add()
        self.path.write_bytes(self.path.read_bytes() + b'Another edit\r\n')
        changed = self.path.read_bytes()
        with self.assertRaises(NotesError):
            self.store.undo(self.path.name, result['file']['revision'], result['file']['undo_id'])
        self.assertEqual(self.path.read_bytes(), changed)
        self.assertIsNone(self.store.read(self.path.name)['undo_id'])

    def test_duplicate_warning_requires_choice(self):
        revision = sha(self.original)
        preview = self.store.preview(self.path.name, 'BONJOUR!\nNouveau.\nNouveau !', revision)
        self.assertEqual([d['line'] for d in preview['duplicates']], [1, 3])
        with self.assertRaises(NotesError):
            self.store.append(self.path.name, 'BONJOUR!', revision, str(uuid.uuid4()))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.add('BONJOUR!', allow_duplicates=True)

    def test_retried_request_does_not_add_twice(self):
        request_id = str(uuid.uuid4())
        revision = sha(self.original)
        self.store.append(self.path.name, 'Une seule fois.', revision, request_id)
        after = self.path.read_bytes()
        response = self.store.append(self.path.name, 'Une seule fois.', revision, request_id)
        self.assertTrue(response['already_added'])
        self.assertEqual(self.path.read_bytes(), after)

    def test_request_id_cannot_be_reused_for_different_text(self):
        request_id = str(uuid.uuid4())
        self.store.append(self.path.name, 'Une seule fois.', sha(self.original), request_id)
        with self.assertRaises(NotesError):
            self.store.append(self.path.name, 'Autre texte.', sha(self.original), request_id)

    def test_missing_final_newline_and_empty_file(self):
        for original in [b'Bonjour', b'']:
            with self.subTest(original=original):
                self.path.write_bytes(original)
                self.add('  Espaces conservés.  \n\nDeuxième ligne.')
                separator = b'\n' if original else b''
                self.assertEqual(self.path.read_bytes(), original + separator + '  Espaces conservés.  \n\nDeuxième ligne.\n'.encode())

    def test_utf16_and_bom_are_preserved(self):
        self.path.write_bytes(b'\xff\xfe' + 'Bonjour.\r\n'.encode('utf-16-le'))
        original = self.path.read_bytes()
        result = self.add('Ça va ? فارسی')
        self.assertEqual(self.path.read_bytes(), original + 'Ça va ? فارسی\r\n'.encode('utf-16-le'))
        self.store.undo(self.path.name, result['file']['revision'], result['file']['undo_id'])
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_paths_and_empty_text_are_refused(self):
        for name in ['../outside.txt', 'C:\\outside.txt', 'sub/file.txt', 'Overall Examples.txt:stream', 'app.py', None]:
            with self.subTest(name=name), self.assertRaises(NotesError):
                self.store.read(name)
        for text in ['', ' \n\t', 'bad\x00text']:
            with self.subTest(text=text), self.assertRaises(NotesError):
                self.add(text)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_failed_backup_journal_does_not_modify_notes(self):
        with patch.object(self.store, 'save_journal', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.add()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_pending_commit_recovers_after_restart(self):
        result = self.add()
        self.store.journal[-1]['status'] = 'pending'
        self.store.save_journal()
        restored = NotesStore(self.notes, self.root / 'state')
        self.assertEqual(restored.read(self.path.name)['undo_id'], result['file']['undo_id'])

    def test_multiple_undo_returns_to_original(self):
        self.add('Première addition.')
        self.add('Deuxième addition.')
        for _ in range(2):
            current = self.store.read(self.path.name)
            self.store.undo(self.path.name, current['revision'], current['undo_id'])
        self.assertEqual(self.path.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
