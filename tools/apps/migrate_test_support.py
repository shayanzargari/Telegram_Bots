"""Initial, explicit legacy fixture extraction and test-path relocation.

Only isolated test inputs are copied. No production source/notes are modified.
"""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[2]

def main():
    if (ROOT/'tests/french/support/fixture-manifest.json').exists():
        raise RuntimeError('Initial fixture extraction already completed. Maintained regressions must not be rewritten from historical originals.')
    support=ROOT/'tests/french/support';private=support/'private';private.mkdir(parents=True,exist_ok=True)
    (private/'.gitignore').write_text('*\n!.gitignore\n',encoding='utf-8')
    sources={
      'editor-notes':ROOT/'French Notes/checks/editor-update/notes',
      'append-notes':ROOT/'French Notes/checks/notes',
      'expression-notes':ROOT/'French Notes/checks/notes-expressions-20261106T201106Z/before/notes',
      'expression-plan.json':ROOT/'French Notes/checks/notes-expressions-20261106T201106Z/plan.json',
      'indicatif-notes':ROOT/'French Notes/checks/indicatif-20261110T152321Z/staged',
      'indicatif-ledger.json':ROOT/'French Notes/checks/indicatif-20261110T152321Z/migration-ledger.json',
      'current-notes':ROOT/'French Notes/french',
    }
    manifest=[]
    for key,source in sources.items():
        target=private/key
        if not source.exists():raise FileNotFoundError('Required historical fixture is missing: '+str(source))
        if source.is_dir():shutil.copytree(source,target,dirs_exist_ok=True)
        else:shutil.copy2(source,target)
        for p in target.rglob('*') if target.is_dir() else [target]:
            if p.is_file():manifest.append({'source':str(source),'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
    (support/'fixture-manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    for p in (ROOT/'tests/home').glob('*.py'):
        s=p.read_text('utf-8')
        for old,new in [('bot.','home_bot.bot.'),('storage.','home_bot.storage.'),('local_telegram.','home_bot.simulator.')]:
            s=re.sub(r'([\'\"])'+re.escape(old),lambda m:m[1]+new,s)
        s=s.replace('Path(__file__).resolve().parents[1] / "seed_tasks.json"','Path(__import__("home_bot").__file__).parent / "seed_tasks.json"')
        s=s.replace("Path(__file__).resolve().parents[1] / '.local-telegram/comprehensive-audit/api-fault-matrix.json'", "Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',__import__('tempfile').gettempdir())) / 'home-api-fault-matrix.json'")
        s=s.replace("Path(__file__).resolve().parents[1] / '.local-telegram/batch-remove/extended-model.json'", "Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',__import__('tempfile').gettempdir())) / 'home-extended-model.json'")
        p.write_text(s,encoding='utf-8')
    fixture=ROOT/'tests/home/fixtures';fixture.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'Our_Home_Telegram_Bot/RUN_BACKGROUND.vbs',fixture/'RUN_BACKGROUND.vbs')
    p=ROOT/'tests/home/test_launcher_audit.py';s=p.read_text('utf-8').replace("(ROOT / 'RUN_BACKGROUND.vbs')","(Path(__file__).parent / 'fixtures/RUN_BACKGROUND.vbs')").replace("ROOT / 'RUN_BACKGROUND.vbs', path", "Path(__file__).parent / 'fixtures/RUN_BACKGROUND.vbs', path");p.write_text(s,encoding='utf-8')
    for p in (ROOT/'tests/french').glob('*.py'):
        s=p.read_text('utf-8')
        s=s.replace("ROOT/'tests/fixtures/denise.mp3'","Path(__file__).parent/'fixtures/denise.mp3'")
        p.write_text(s,encoding='utf-8')
    for p in (ROOT/'tests/french').glob('*.cjs'):
        s=p.read_text('utf-8')
        if p.name=='make_icon.cjs':continue
        s=s.replace("path.resolve(__dirname,'..')","path.resolve(__dirname,'..','..')")
        s=s.replace("path.join(root,'checks','editor-update','notes')","path.join(__dirname,'support/private/editor-notes')")
        for marker in ('current-audit-work.txt','current-alignment-work.txt','current-rows-audio-work.txt','current-notes-expressions-work.txt','current-indicatif-work.txt'):
            s=s.replace("fs.readFileSync(path.join(root,'checks/"+marker+"'),'utf8').trim()", "process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french')")
            s=s.replace("fs.readFileSync(path.join(root,'checks','"+marker+"'),'utf8').trim()", "process.env.BOT_CENTER_TEST_OUTPUT||path.join(root,'work/tests/manual/french')")
        s=s.replace("'D:/french'","path.join(__dirname,'support/private/current-notes')")
        s=s.replace("path.join(work,'before/notes')","path.join(__dirname,'support/private/expression-notes')").replace("path.join(work,'before/notes',name)","path.join(__dirname,'support/private/expression-notes',name)")
        s=s.replace("path.join(work,'plan.json')","path.join(__dirname,'support/private/expression-plan.json')")
        s=s.replace("path.join(work,'migration-ledger.json')","path.join(__dirname,'support/private/indicatif-ledger.json')")
        s=s.replace("path.join(work,'staged')","path.join(__dirname,'support/private/indicatif-notes')")
        s=s.replace("path.join(root,'tests/fixtures/denise.mp3')","path.join(__dirname,'fixtures/denise.mp3')")
        s=s.replace("spawn('py',['-3.11',path.join(root,'app.py')", "spawn(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-m','french_notes.app'")
        s=s.replace("spawnSync('py',['-3.11','-c'", "spawnSync(process.env.BOT_CENTER_FRENCH_PYTHON||path.join(root,'.venvs/french-editor/Scripts/python.exe'),['-c'")
        s=s.replace('from editor_store import EditorStore','from french_notes.editor_store import EditorStore')
        s=s.replace("path.join(root,'ui/columns.js')","path.join(__dirname,'../../.venvs/french-editor/Lib/site-packages/french_notes/ui/columns.js')")
        s=s.replace("path.join(root,'checks','notes')","path.join(__dirname,'support/private/append-notes')").replace("path.join(root,'checks','notes',file)","path.join(__dirname,'support/private/append-notes',file)")
        s=s.replace("path.join(root,'checks','ui-run-')","path.join(process.env.BOT_CENTER_TEST_OUTPUT,'ui-run-')")
        s=s.replace("path.join(root,'checks',", "path.join(process.env.BOT_CENTER_TEST_OUTPUT,")
        s=s.replace("path.join(root,'French Notes.png')","path.join(process.env.BOT_CENTER_TEST_OUTPUT,'French Notes.png')")
        # Routine UI checks need an existing isolated output root, never markers.
        s="const outputRoot=process.env.BOT_CENTER_TEST_OUTPUT;if(outputRoot)require('node:fs').mkdirSync(outputRoot,{recursive:true});\n"+s
        p.write_text(s,encoding='utf-8')
    print(json.dumps({'extracted_fixtures':len(manifest),'private_root':str(private),'historical_inputs_preserved':True}))

if __name__=='__main__':main()
