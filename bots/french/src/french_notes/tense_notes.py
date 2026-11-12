"""Plain-text section contracts for the tense notebooks."""
from .store import NotesError

SEPARATOR = '    |    '
TENSE_SECTIONS = {
    'Indicatif.txt': ('Présent', 'Passé composé', 'Imparfait', 'Plus-que-parfait',
                     'Futur simple', 'Futur antérieur', 'Futur proche', 'Passé récent',
                     'Passé simple', 'Formules et fragments'),
    'Conditionnel.txt': ('Présent', 'Passé', 'Formules et fragments'),
}
LEGACY_NOTES = {'Overall Examples.txt': 'Indicatif.txt', 'passe compose.txt': 'Indicatif.txt'}


def validate_sections(name, text):
    if name not in TENSE_SECTIONS:
        return
    found = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith('## '):
            found.append(line[3:].strip())
        elif line.strip():
            if not found:
                raise NotesError('Put entries below a tense heading. Your draft is kept.')
            cells = line.split(SEPARATOR)
            if len(cells) not in (3, 4):
                raise NotesError('Each tense entry needs French, Farsi, and English cells, with an optional secondary-tense field. Your draft is kept.')
    if tuple(found) != TENSE_SECTIONS[name]:
        headings = ', '.join(TENSE_SECTIONS[name])
        raise NotesError('Keep the tense headings in this order: ' + headings + '. Your draft is kept.')
