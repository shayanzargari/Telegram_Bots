import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from french_notes.editor_store import COMBINED, EditorStore, normalized, section_spans
from french_notes.store import NotesError, sha


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root / 'notes'
        self.notes.mkdir()
        self.plain = self.notes / 'Practice.txt'
        self.original = b'\xef\xbb\xbf' + 'Bonjour.\r\nJe suis là. فارسی\r\n'.encode('utf-8')
        self.plain.write_bytes(self.original)
        self.combined = self.notes / COMBINED
        self.words = ('---------------- Words ----------------\r\n\r\nLa fourmi.\r\n\r\n'
                      '---------------- Verbs ----------------\r\n\r\nCultiver.\r\n### Verbs with À\r\nParler à\r\n\r\n'
                      '------------------------------------------------------------------------\r\n'
                      'Haut = بالا\r\nBas = پایین\r\n\r\n'
                      '------------------------------------------------------------------------\r\n'
                      'Savoir / Connaître\r\n').encode('utf-8')
        self.combined.write_bytes(self.words)
        self.store = EditorStore(self.notes, self.root / 'state')

    def save(self, name, text, doc=None, request_id=None):
        doc = doc or self.store.read_document(name)
        return self.store.save_document(name, text, doc['revision'], doc['section_revision'], request_id or str(uuid.uuid4()))['file']

    def undo(self, doc):
        return self.store.undo_document(doc['name'], doc['revision'], doc['section_revision'], doc['undo_id'])['file']

    def test_views_are_distinct_and_do_not_modify_original(self):
        self.assertEqual([x['title'] for x in self.store.categories()['categories']], ['Words', 'Verbs', 'Additional Notes', 'Practice'])
        words = self.store.read_document('@words')['text']
        verbs = self.store.read_document('@verbs')['text']
        details = self.store.read_document('@additional')['text']
        self.assertIn('La fourmi.', words)
        self.assertNotIn('Cultiver.', words)
        self.assertIn('Cultiver.', verbs)
        self.assertIn('Verbs with À', verbs)
        self.assertNotIn('Haut', verbs)
        self.assertIn('Haut = بالا', details)
        self.assertIn('Savoir / Connaître', details)
        self.assertEqual(self.combined.read_bytes(), self.words)

    def test_direct_edit_replaces_text_and_undo_restores_bytes(self):
        doc = self.save(self.plain.name, 'Texte remplacé. فارسی\nDeuxième ligne.')
        self.assertEqual(self.plain.read_bytes(), b'\xef\xbb\xbf' + 'Texte remplacé. فارسی\r\nDeuxième ligne.'.encode())
        self.undo(doc)
        self.assertEqual(self.plain.read_bytes(), self.original)

    def test_noop_does_not_rewrite_mixed_newlines(self):
        original = b'\xef\xbb\xbfA\r\nB\nC\r\n'
        self.plain.write_bytes(original)
        doc = self.store.read_document(self.plain.name)
        self.save(self.plain.name, doc['text'])
        self.assertEqual(self.plain.read_bytes(), original)
        self.assertEqual(self.store.journal, [])

    def test_words_save_preserves_all_other_bytes(self):
        old_text, start, end, encoding = self.store.unpack('@words', self.words)
        self.save('@words', 'Un mot. فارسی')
        after = self.combined.read_bytes()
        self.assertEqual(after, self.words[:start] + 'Un mot. فارسی\r\n'.encode(encoding) + self.words[end:])
        self.assertIn('Cultiver.', self.store.read_document('@verbs')['text'])

    def test_verbs_save_keeps_detail_boundary_even_without_last_newline(self):
        self.save('@verbs', 'Marcher.')
        self.assertEqual(self.store.read_document('@verbs')['text'], 'Marcher.\n')
        self.assertIn('Haut = بالا', self.store.read_document('@additional')['text'])

    def test_additional_notes_can_be_fully_replaced(self):
        self.save('@additional', 'Détail remplacé. جزئیات')
        self.assertEqual(self.store.read_document('@additional')['text'], 'Détail remplacé. جزئیات')
        self.assertIn('La fourmi.', self.store.read_document('@words')['text'])

    def test_independent_section_saves_merge_without_losing_other_edits(self):
        words = self.store.read_document('@words')
        verbs = self.store.read_document('@verbs')
        self.save('@words', 'Un chat.', words)
        self.save('@verbs', 'Marcher.', verbs)
        self.assertEqual(self.store.read_document('@words')['text'], 'Un chat.\n')
        self.assertEqual(self.store.read_document('@verbs')['text'], 'Marcher.\n')

    def test_undo_words_after_editing_verbs_preserves_verbs(self):
        self.save('@words', 'Un chat.')
        self.save('@verbs', 'Marcher.')
        self.undo(self.store.read_document('@words'))
        self.assertEqual(self.store.read_document('@words')['text'], normalized(self.store.unpack('@words', self.words)[0]))
        self.assertEqual(self.store.read_document('@verbs')['text'], 'Marcher.\n')
        self.undo(self.store.read_document('@verbs'))
        self.assertEqual(self.combined.read_bytes(), self.words)

    def test_same_section_external_edit_blocks_save(self):
        doc = self.store.read_document('@words')
        self.combined.write_bytes(self.words.replace(b'La fourmi.', b'Un autre mot.'))
        external = self.combined.read_bytes()
        with self.assertRaises(NotesError):
            self.save('@words', 'Mon changement.', doc)
        self.assertEqual(self.combined.read_bytes(), external)

    def test_plain_file_external_edit_blocks_save(self):
        doc = self.store.read_document(self.plain.name)
        self.plain.write_bytes(self.original + b'External\r\n')
        with self.assertRaises(NotesError):
            self.save(self.plain.name, 'Mon changement.', doc)

    def test_draft_persists_across_server_restart_without_writing_notes(self):
        doc = self.store.read_document('@verbs')
        draft = {k: doc[k] for k in ('revision', 'section_revision')}
        draft['text'] = 'Un brouillon فارسی'
        self.store.write_draft('@verbs', draft)
        other = EditorStore(self.notes, self.root / 'state')
        self.assertEqual(other.read_drafts()['drafts']['@verbs'], draft)
        self.assertEqual(self.combined.read_bytes(), self.words)

    def test_empty_document_is_saved_and_undoable(self):
        doc = self.save(self.plain.name, '')
        self.assertEqual(self.plain.read_bytes(), b'\xef\xbb\xbf')
        self.undo(doc)
        self.assertEqual(self.plain.read_bytes(), self.original)

    def test_missing_boundaries_exposes_complete_combined_file(self):
        self.combined.write_text('Unstructured notes. فارسی', encoding='utf-8')
        entries = self.store.categories()['categories']
        self.assertIn(COMBINED, [e['name'] for e in entries])
        self.assertNotIn('@words', [e['name'] for e in entries])
        self.assertEqual(self.store.read_document(COMBINED)['text'], 'Unstructured notes. فارسی')

    def test_retried_save_is_idempotent(self):
        doc = self.store.read_document('@words')
        key = str(uuid.uuid4())
        self.save('@words', 'Un chat.', doc, key)
        after = self.combined.read_bytes()
        self.save('@words', 'Un chat.', doc, key)
        self.assertEqual(self.combined.read_bytes(), after)
        self.assertEqual(len(self.store.journal), 1)

    def test_failed_journal_does_not_write_document(self):
        with patch.object(self.store, 'save_journal', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                self.save('@words', 'Un chat.')
        self.assertEqual(self.combined.read_bytes(), self.words)

    def test_utf16_edit_and_undo_preserve_encoding(self):
        original = b'\xff\xfe' + self.words.decode('utf-8').encode('utf-16-le')
        self.combined.write_bytes(original)
        doc = self.save('@words', 'Un chat. فارسی')
        self.assertTrue(self.combined.read_bytes().startswith(b'\xff\xfe'))
        self.undo(doc)
        self.assertEqual(self.combined.read_bytes(), original)

    def test_undo_after_external_encoding_conversion_keeps_current_encoding(self):
        self.save('@words', 'Un chat.')
        changed = self.combined.read_bytes().decode('utf-8')
        self.combined.write_bytes(b'\xff\xfe' + changed.encode('utf-16-le'))
        self.undo(self.store.read_document('@words'))
        self.assertEqual(self.combined.read_bytes(), b'\xff\xfe' + self.words.decode().encode('utf-16-le'))

    def test_search_routes_detailed_examples_to_additional_notes(self):
        result = self.store.search('Haut')['results']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], '@additional')


if __name__ == '__main__':
    unittest.main(verbosity=2)
