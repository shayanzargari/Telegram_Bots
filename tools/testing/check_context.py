"""Check active instruction/context and maintained local documentation references."""
from pathlib import Path
import re
import json
import argparse

ROOT = Path(__file__).resolve().parents[2]


def check(root=ROOT):
    root = Path(root)
    required = ['AGENTS.md', 'core/AGENTS.md', 'shared/AGENTS.md', 'bots/home/AGENTS.md', 'bots/french/AGENTS.md',
                'tests/AGENTS.md', 'tools/AGENTS.md', 'docs/IMPLEMENTATION_PLAN.md', 'docs/PROJECT_STATUS.md',
                'docs/PROJECT_MAP.md', 'docs/DECISIONS.md', 'docs/ADDING_A_BOT.md', 'README.md', 'START.cmd', 'START.vbs',
                'tools/environments/prepare.py', 'tools/testing/run_tests.py', 'tools/bots/manage_bot.py']
    missing = [value for value in required if not (root / value).is_file()]
    skills = []
    for name in ('add-bot', 'verify-change', 'close-task'):
        file = root / '.agents/skills' / name / 'SKILL.md'
        if not file.exists(): missing.append(file.relative_to(root).as_posix()); continue
        text = file.read_text('utf-8')
        if not text.startswith('---\n') or not re.search(r'^name:\s*[\"\']?' + name + r'[\"\']?\s*$', text, re.M) or not re.search(r'^description:\s*\S', text, re.M):
            raise AssertionError('Invalid supported skill frontmatter: ' + name)
        skills.append(name)
    errors = []
    for path in [root / 'README.md', *(root / 'docs').glob('*.md')]:
        if not path.exists(): continue
        for label, link in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', path.read_text('utf-8')):
            link = link.strip('<>').split('#')[0]
            if not link or '://' in link or link.startswith(('mailto:', 'plugin:', 'app:')): continue
            resolved = (path.parent / link).resolve() if not Path(link).is_absolute() else Path(link)
            if not resolved.exists(): errors.append({'document': path.relative_to(root).as_posix(), 'target': link})
    if missing or errors: raise AssertionError(json.dumps({'missing': missing, 'broken_local_links': errors}))
    return {'status': 'Passed', 'required_paths': len(required), 'skills': skills, 'documentation_links': 'resolved'}


if __name__ == '__main__':
    print(json.dumps(check()))
