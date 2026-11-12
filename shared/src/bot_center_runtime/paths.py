"""One canonical installation resolver used by every production entry point."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

class ConfigurationError(RuntimeError): pass

def installation_root(value=None):
    if value:return Path(value).resolve(strict=True)
    if os.environ.get('BOT_CENTER_ROOT'):return Path(os.environ['BOT_CENTER_ROOT']).resolve(strict=True)
    for p in Path(sys.executable).resolve().parents:
        if (p/'config/installation.json').is_file():return p
    raise ConfigurationError('Installation root is required; use --root or a final-path installation environment.')

class Installation:
    def __init__(self, root=None):self.root=installation_root(root)
    def assert_active(self):
        journal=self.root/'config/activation-journal.json'
        if journal.exists():
            try:data=json.loads(journal.read_text('utf-8'))
            except (ValueError,OSError):raise ConfigurationError('Activation journal unreadable; use read-only recovery inspection.') from None
            if data.get('phase') not in {'complete','rolled_back'}:
                raise ConfigurationError('Installation activation/recovery is incomplete; polling and writable startup are blocked. Run recovery inspect.')
        settings=self.root/'config/installation.json'
        if not settings.is_file():raise ConfigurationError('Installation is not activated. Run migration verification first.')
        return json.loads(settings.read_text('utf-8'))
    def resolve(self,value):
        path=Path(value)
        return (self.root/path).resolve() if not path.is_absolute() else path.resolve()
    def manifests(self):
        raw=json.loads((self.root/'config/registry.json').read_text('utf-8'))
        output=[]
        for entry in raw['bots']:
            if isinstance(entry,str):entry=json.loads(self.resolve(entry).read_text('utf-8'))
            elif 'manifest' in entry:entry=json.loads(self.resolve(entry['manifest']).read_text('utf-8'))
            output.append(entry)
        return output
    def bot(self,bot_id):
        matches=[b for b in self.manifests() if b['id']==bot_id]
        if len(matches)!=1:raise ConfigurationError('Unknown or duplicated bot identity.')
        data=dict(matches[0])
        if data.get('contract_version')!=1 or data.get('schema_version')!=1:raise ConfigurationError('Unsupported bot contract/schema.')
        data['telegram_id']=data.get('telegram_id',data.get('expected_bot_id'))
        data['python']=data.get('python',data.get('executable'))
        for key in ('python','editor_python','cwd','data_dir','log_dir','notes_dir','editor_state_dir','history_path'):
            if key in data:data[key]=str(self.resolve(data[key]))
        return data
    def require_state(self,bot_id):
        self.assert_active();bot=self.bot(bot_id)
        for journal in (self.root/'data/core/registrations').glob('*.json'):
            try:publication=json.loads(journal.read_text('utf-8'))
            except (OSError,ValueError):raise ConfigurationError('A registration journal is unreadable. Inspect recovery before writable startup.') from None
            if publication.get('bot_id')==bot_id and publication.get('files') and publication.get('phase') not in {'complete','rolled_back'}:
                raise ConfigurationError('This bot has an incomplete registration publication. Complete or roll it back before writable startup.')
        base=Path(bot['data_dir'])
        if not base.is_dir():raise ConfigurationError('Expected application state is missing; restore it rather than initializing a new namespace.')
        for relative in bot.get('required_state',[]):
            if not (base/relative).is_file():raise ConfigurationError('Required application state is missing; restore a verified recovery set.')
        return bot
