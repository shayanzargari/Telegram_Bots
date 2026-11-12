import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from french_notes.editor_store import EditorStore
from french_notes.store import NotesError
from french_notes.tense_notes import TENSE_SECTIONS, validate_sections


class TenseNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root/'notes'
        self.notes.mkdir()
        self.text = '\n\n'.join('## '+section for section in TENSE_SECTIONS['Indicatif.txt'])+'\n'
        (self.notes/'Indicatif.txt').write_bytes(b'\xef\xbb\xbf'+self.text.replace('\n','\r\n').encode())
        self.store = EditorStore(self.notes, self.root/'state')

    def test_empty_sections_are_valid_without_fabricated_entries(self):
        for name, sections in TENSE_SECTIONS.items():
            validate_sections(name, '\n'.join('## '+section for section in sections))

    def test_mixed_tense_field_round_trip_and_exact_undo(self):
        original = (self.notes/'Indicatif.txt').read_bytes()
        text = self.text.replace('## Passé composé', "## Passé composé\nTu as laissé les courses parce que c'était lourd.    |    خریدها را گذاشتی چون سنگین بود.    |    You left the shopping because it was heavy.    |    Imparfait")
        doc = self.store.read_document('Indicatif.txt')
        saved = self.store.save_document(doc['name'],text,doc['revision'],doc['section_revision'],str(uuid.uuid4()))['file']
        self.assertEqual(saved['text'], text)
        self.store.undo_document(saved['name'],saved['revision'],saved['section_revision'],saved['undo_id'])
        self.assertEqual((self.notes/'Indicatif.txt').read_bytes(),original)

    def test_invalid_structure_is_rejected_without_mutation(self):
        original = (self.notes/'Indicatif.txt').read_bytes()
        doc = self.store.read_document('Indicatif.txt')
        for text in ['Bonjour.\n'+self.text, self.text.replace('## Présent','## Unknown'), self.text.replace('## Présent','## Présent\nA single cell'), self.text.replace('## Présent','')]:
            with self.subTest(text=text), self.assertRaises(NotesError):
                self.store.save_document(doc['name'],text,doc['revision'],doc['section_revision'],str(uuid.uuid4()))
            self.assertEqual((self.notes/'Indicatif.txt').read_bytes(), original)

    def test_catalog_order_and_avoir_position(self):
        (self.notes/'Conditionnel.txt').write_text('\n'.join('## '+s for s in TENSE_SECTIONS['Conditionnel.txt']),encoding='utf-8')
        (self.notes/'Faire.txt').write_text('Faire confiance    |    اعتماد کردن    |    To trust',encoding='utf-8')
        (self.notes/'Avoir.txt').write_text('Avoir besoin de    |    نیاز داشتن    |    To need',encoding='utf-8')
        self.assertEqual([row['title'] for row in self.store.categories()['categories']], ['Indicatif','Conditionnel','Faire','Avoir'])

    def test_saved_draft_does_not_overwrite_classified_content(self):
        doc = self.store.read_document('Indicatif.txt')
        self.store.write_draft(doc['name'],{'text':'Old unsaved text','revision':doc['revision'],'section_revision':doc['section_revision']})
        reloaded = EditorStore(self.notes,self.root/'state')
        self.assertEqual(reloaded.read_document(doc['name'])['text'],self.text)
        self.assertEqual(reloaded.read_drafts()['drafts'][doc['name']]['text'],'Old unsaved text')


if __name__ == '__main__':
    unittest.main()
