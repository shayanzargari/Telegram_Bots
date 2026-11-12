import json
from pathlib import Path
import tempfile
import unittest

from french_notes.telegram_bot.library import Source, SourceReader, Snapshot, build, normalize


def sources(text, name='Practice.txt'):
    return (Source(name, text.encode('utf-8')),)


class LibraryTests(unittest.TestCase):
    def test_unicode_fields_duplicates_and_order(self):
        text = 'Été    |    تابستان    |    Summer\nÉté    |    تابستان    |    Summer\n'
        s = build(sources(text))
        self.assertEqual(len(s.entries), 2)
        self.assertNotEqual(s.entries[0].id,s.entries[1].id)
        self.assertEqual(s.search('ete'), s.entries)
        self.assertEqual(s.search('تابستان'), s.entries)
        self.assertEqual(s.search('summer'), s.entries)
        self.assertEqual(len(s.search('nonexistent')),0)

    def test_stable_identity_never_uses_position(self):
        a = build(sources('Bonjour    |    سلام    |    Hello\n'))
        b = build(sources('Avant    |    پیش    |    Before\nBonjour    |    سلام    |    Hello\n'),a)
        self.assertEqual(a.entries[0].id,b.entries[1].id)
        c = build(sources('Autre    |    دیگر    |    Other\n'),b)
        self.assertIsNone(c.entry(a.entries[0].id))

    def test_ambiguous_duplicates_get_new_ids(self):
        a=build(sources('A    |    B    |    C\n'*2))
        b=build(sources('A    |    B    |    C\n'*3),a)
        self.assertFalse({e.id for e in a.entries}&{e.id for e in b.entries})

    def test_snapshot_roundtrip_and_corruption(self):
        a=build(sources('Bonjour    |    سلام    |    Hello\n'*2))
        b=Snapshot.loads(a.dumps())
        self.assertEqual(a,b)
        data=json.loads(a.dumps());data['version']='wrong'
        with self.assertRaises(ValueError): Snapshot.loads(json.dumps(data))

    def test_utf16_both_endians_bom_and_crlf(self):
        for encoding,bom in [('utf-16-le',b'\xff\xfe'),('utf-16-be',b'\xfe\xff'),('utf-8',b'\xef\xbb\xbf')]:
            with self.subTest(encoding=encoding):
                raw=bom+'Été    |    فارسی    |    Summer\r\n'.encode(encoding)
                s=build((Source('Example.txt',raw),))
                self.assertEqual(s.entries[0].records[0].start,len(bom))
                self.assertEqual(s.entries[0].records[0].end,len(raw))

    def test_expressions_legacy_and_secondary_tenses(self):
        s=build(sources('triste    |    غمگین    |    sad    |    Ne sois pas triste.    |    غمگین نباش    |    Do not be sad.\n','Expressions.txt'))
        self.assertEqual(s.entries[0].speech,'Ne sois pas triste.')
        self.assertEqual(len(s.entries[0].records[0].cells),6)
        s=build(sources('Exemple    |    فارسی    |    English\n','Expressions.txt'))
        self.assertEqual(s.entries[0].records[0].cells[:3],('','',''))
        s=build(sources('## Présent\nPhrase    |    فارسی    |    English    |    Imparfait\n## Passé\n','Indicatif.txt'))
        self.assertEqual(s.entries[0].records[0].secondary,'Imparfait')
        self.assertIn('Passé',s.documents[0].sections)

    def test_combined_views_and_safe_fallback(self):
        s=build(sources('----- Words -----\nMot    |    واژه    |    Word\n----- Verbs -----\nLire    |    خواندن    |    Read\n----------------\nDetails\n','wrods, verbs.txt'))
        self.assertEqual([d.id for d in s.documents],['@words','@verbs','@additional'])
        s=build(sources('Ambiguous boundaries stay readable\n','wrods, verbs.txt'))
        self.assertEqual(s.documents[0].title,'Combined Notes')

    def test_unfamiliar_material_and_table_preserved(self):
        s=build(sources('Explanation\nA\tB\tC\nX\tY\tZ\n\nUnknown | spacing\n'))
        self.assertEqual(s.entries[0].text,'Explanation\nA\tB\tC\nX\tY\tZ')
        self.assertEqual(s.entries[1].text,'Unknown | spacing')

    def test_chapter_speech_uses_only_known_french_fields(self):
        text='----- Words -----\nMot    |    واژه    |    Word\n----- Verbs -----\nLire    |    خواندن    |    Read\n----------------\nExplanation\nBonjour    |    سلام    |    Hello\nMerci    |    ممنون    |    Thanks\n'
        snap=build(sources(text,'wrods, verbs.txt'))
        chapter=snap.document('@additional').entries[0]
        self.assertEqual(chapter.speech,'Bonjour\nMerci')

    def test_arabic_farsi_normalization_is_search_only(self):
        self.assertEqual(normalize('كي'),normalize('کی'))
        self.assertEqual(normalize('می\u200cرود'),normalize('می رود'))

    def test_reader_does_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'Test.txt';raw='Bonjour\n'.encode();p.write_bytes(raw)
            (root/'backup').mkdir();(root/'backup/secret.txt').write_text('Hidden')
            s=build(SourceReader(root).capture())
            self.assertEqual(len(s.sources),1)
            self.assertEqual(p.read_bytes(),raw)


if __name__=='__main__': unittest.main()
