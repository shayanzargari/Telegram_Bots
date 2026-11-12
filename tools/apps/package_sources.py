"""Repeatable initial extraction from protected active sources; never executes apps."""
from pathlib import Path
import json, re, shutil, hashlib

ROOT=Path(__file__).resolve().parents[2]
def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

def extract():
    home=ROOT/'bots/home/src/home_bot';french=ROOT/'bots/french/src/french_notes'
    if (home/'__init__.py').exists() or (french/'__init__.py').exists():
        raise RuntimeError('Initial extraction already completed. Maintained package source must not be overwritten by historical originals.')
    home.mkdir(parents=True,exist_ok=True);french.mkdir(parents=True,exist_ok=True)
    for name in ('bot.py','storage.py','seed_tasks.json','simulator.html'):
        shutil.copy2(ROOT/'Our_Home_Telegram_Bot'/name,home/name)
    shutil.copy2(ROOT/'Our_Home_Telegram_Bot/local_telegram.py',home/'simulator.py')
    write(home/'__init__.py','"""Our Home application package."""\n__version__="1.0.0"\n')
    for p in home.glob('*.py'):
        s=p.read_text('utf-8');s=re.sub(r'^from (bot|storage) import ',r'from .\1 import ',s,flags=re.M)
        p.write_text(s,encoding='utf-8')
    for name in ('app.py','store.py','editor_store.py','tense_notes.py','speech.py','speech_process.py','speech_worker.py','speak_french.ps1'):
        shutil.copy2(ROOT/'French Notes'/name,french/name)
    shutil.copytree(ROOT/'French Notes/ui',french/'ui',dirs_exist_ok=True)
    shutil.copytree(ROOT/'French Notes/telegram_bot',french/'telegram_bot',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
    write(french/'__init__.py','"""French Notes editor, document library, audio and Telegram reader."""\n__version__="1.0.0"\n')
    local={'app','store','editor_store','tense_notes','speech','speech_process','speech_worker'}
    for p in french.rglob('*.py'):
        s=p.read_text('utf-8');prefix='..' if p.parent.name=='telegram_bot' else '.'
        for name in local:s=re.sub(r'^([ \t]*)from '+name+r' import ',r'\1from '+prefix+name+' import ',s,flags=re.M)
        p.write_text(s,encoding='utf-8')
    for component,legacy in [('home','Our_Home_Telegram_Bot'),('french','French Notes')]:
        target=ROOT/'tests'/component
        shutil.copytree(ROOT/legacy/'tests',target,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
        for p in target.glob('*.py'):
            s=p.read_text('utf-8')
            s=re.sub(r'^sys\.path\.insert\(0, str\(Path\(__file__\)\.resolve\(\)\.parents\[1\]\)\)\r?\n','',s,flags=re.M)
            if component=='home':
                s=re.sub(r'\bfrom (bot|storage|local_telegram) import ',lambda m:'from home_bot.'+('simulator' if m[1]=='local_telegram' else m[1])+' import ',s)
                s=re.sub(r'(?m)^import bot$', 'from home_bot import bot',s)
                s=s.replace("patch('bot.","patch('home_bot.bot.").replace('patch("bot.','patch("home_bot.bot.')
                s=s.replace("'from bot import process_lock", "'from home_bot.bot import process_lock")
            else:
                for name in local|{'telegram_bot'}:
                    s=re.sub(r'\bfrom '+name+r'(\.[\w.]+)? import ',lambda m:'from french_notes.'+name+(m[1] or '')+' import ',s)
                    s=s.replace("patch('"+name+'.',"patch('french_notes."+name+'.').replace('patch("'+name+'.','patch("french_notes.'+name+'.')
                s=s.replace('from french_notes.__main__','from french_notes.__main__')
            p.write_text(s,encoding='utf-8')
    # Preserve original content instructions byte-for-byte as a package ownership reference.
    shutil.copy2(ROOT/'French Notes/AGENTS.md',ROOT/'bots/french/ORIGINAL_CONTENT_RULES.md')
    for component,package in [('home','home_bot'),('french','french_notes')]:
        dependencies='["bot-center-runtime==1.0.0"]' if component=='home' else '["bot-center-runtime==1.0.0", "python-telegram-bot==22.8", "edge-tts==7.3.1"]'
        entry='home-bot = "home_bot.__main__:main"' if component=='home' else 'french-reader = "french_notes.__main__:main"\nfrench-editor = "french_notes.app:main"'
        write(ROOT/'bots'/component/'pyproject.toml',f'''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{package.replace('_','-')}"
version = "1.0.0"
requires-python = ">=3.11,<3.12"
dependencies = {dependencies}

[project.scripts]
{entry}

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
{package} = ["*.json", "*.html", "*.ps1", "ui/*", "telegram_bot/*.json"]
''')
    print('Extracted active sources and original regression scenarios into final package/test trees.')

if __name__=='__main__':extract()
