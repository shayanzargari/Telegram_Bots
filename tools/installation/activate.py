"""Journaled initial installation activation; never starts a poller or editor."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,shutil,sqlite3,subprocess,time,uuid
from pathlib import Path
from bot_center_runtime.paths import Installation
from bot_center_runtime.locks import ResourceLock,FileLease
from bot_center_runtime.credentials import CredentialStore,verify_identity
from bot_center.commands import CommandService
from bot_center.recovery import Journal,maintenance_gate,atomic_json,sqlite_semantics,sha256,replace_with_retry

ROOT=Path(__file__).resolve().parents[2]
STAGE=ROOT/'work/migration/initial'

def validate_state():
    report=json.loads((STAGE/'staging-report.json').read_text('utf-8'))
    if not report.get('verified'):raise RuntimeError('Staging has not passed preservation comparison.')
    dbs={entry['target'] for entry in report['database_semantics']};checked=0
    for item in report['files']:
        target=STAGE/item['target']
        if not target.is_file() or sha256(target)!=item['sha256']:raise RuntimeError('Staged state changed after validation; preserve and reconcile it before activation: '+item['target'])
        source=Path(item.get('source',''))
        if item['target'] in dbs:
            if sqlite_semantics(source)!=sqlite_semantics(target):raise RuntimeError('A source database changed after staging. Capture and reconcile current state before activation.')
        elif not item.get('transform') and not item.get('derived') and source.is_file():
            if sha256(source)!=item['sha256']:raise RuntimeError('A source file changed after staging; no activation occurred.')
        elif item.get('transform') and source.name=='config.json':
            current=json.loads(source.read_text('utf-8'));current.pop('bot_token',None)
            if current!=json.loads(target.read_text('utf-8')):raise RuntimeError('Home configuration changed after staging; capture and reconcile it before activation.')
        elif not item.get('transform') and not item.get('derived'):
            raise RuntimeError('An expected original state file is missing; investigate before activation.')
        checked+=1
    actual={p.name:sha256(p) for p in (STAGE/'state/french/notes').glob('*.txt')}
    if actual!=report['note_hashes']:raise RuntimeError('Approved French note hashes differ from the protected stage.')
    outcome={'status':'Passed','checked_files':checked,'note_files':len(actual),'database_snapshots':len(dbs),'checked_at':time.time()}
    atomic_json(STAGE/'activation-freshness.json',outcome);return outcome

def job(owner,identity,name):
    catalog=json.loads((ROOT/'config/operations.json').read_text('utf-8'))['operations']
    declaration=next(item for item in catalog if item['id']==owner+'.'+identity and item['kind']=='production_job')
    return {'id':identity,'name':name,'timeout_seconds':declaration['timeout_seconds'],'safe_repeat':declaration['repetition_safety']=='safe','resources':declaration['resources'],'cancellation':declaration['cancellation']}

def prepare():
    freshness=validate_state()
    old=ROOT/'Our_Home_Telegram_Bot/data/config.json'
    original=json.loads(old.read_text('utf-8'));token=original.get('bot_token')
    if not token:
        token=CredentialStore(ROOT).load('home')
    expected=int(token.split(':',1)[0])
    identity=verify_identity(token,expected,original.get('bot_username'))
    CredentialStore(ROOT).store_protected('home',token)
    connection=sqlite3.connect((STAGE/'state/french/reader/reader.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:row=connection.execute("SELECT value FROM meta WHERE key='bot_id'").fetchone()
    finally:connection.close()
    if not row or not str(row[0]).isdigit():raise RuntimeError('French saved numeric identity is missing; preserve and investigate it before enrollment.')
    config=STAGE/'configuration';(config/'bots').mkdir(parents=True,exist_ok=True)
    home={'schema_version':1,'contract_version':1,'id':'home','display_name':'Home','package':'home_bot','distribution':'home-bot','project_dir':'bots/home','module':'home_bot.managed','python':'.venvs/home/Scripts/python.exe','cwd':'data/home','data_dir':'data/home','log_dir':'logs/home','credential_ref':'home','telegram_id':identity['id'],'expected_username':identity['username'],'capabilities':['polling','maintenance'],'required_state':['tasks.sqlite3'],'jobs':[job('home','integrity','Check state integrity')],'restore_validator':{'module':'home_bot.managed','job':'integrity'}}
    french={'schema_version':1,'contract_version':1,'id':'french','display_name':'French','package':'french_notes','distribution':'french-notes','project_dir':'bots/french','module':'french_notes.managed','python':'.venvs/french-reader/Scripts/python.exe','editor_python':'.venvs/french-editor/Scripts/pythonw.exe','cwd':'data/french/reader','data_dir':'data/french/reader','notes_dir':'data/french/notes','editor_state_dir':'data/french/editor','history_path':'data/french/editor/history.json','log_dir':'logs/french','credential_ref':'french','telegram_id':int(row[0]),'expected_username':'Easy_French_Learning_bot','capabilities':['polling','maintenance'],'required_state':['reader.sqlite3','identity.json'],'jobs':[job('french','integrity','Check source integrity'),job('french','prepare_audio','Prepare pronunciation audio'),job('french','validate_audio','Validate prepared audio')],'restore_validator':{'module':'french_notes.managed','job':'integrity'}}
    french['required_editor_state']=['history.json','editor-drafts.json','browser-state.json']
    for manifest in (home,french):atomic_json(config/'bots'/(manifest['id']+'.json'),manifest)
    atomic_json(config/'registry.json',{'schema':1,'bots':[{'manifest':'config/bots/home.json'},{'manifest':'config/bots/french.json'}]})
    atomic_json(config/'settings.json',{'schema':1,'port':8765,'timezone':'America/New_York','autostart':False})
    atomic_json(config/'installation.json',{'schema':1,'installation_id':uuid.uuid4().hex,'root':str(ROOT),'generation':'initial-1.0.0','build_manifest_sha256':sha256(ROOT/'work/build/build-manifest.json'),'created':time.time()})
    outcome={'status':'Prepared','freshness':freshness,'configuration':str(config),'home_identity_verified':True,'french_identity_from_preserved_state':True,'french_credential_available':(ROOT/'.secrets/french.dpapi').is_file(),'activated':False}
    atomic_json(STAGE/'prepared.json',outcome);return outcome

def verify_acceptance(path):
    evidence=json.loads(Path(path).read_text('utf-8'))
    if 'acceptance' not in evidence.get('profiles',[]) or '--fresh' not in evidence.get('command',[]):raise RuntimeError('Use a fresh full local acceptance run for activation.')
    spec=importlib.util.spec_from_file_location('maintained_test_coordinator',ROOT/'tools/testing/run_tests.py');coordinator=importlib.util.module_from_spec(spec);spec.loader.exec_module(coordinator)
    catalog=coordinator.load_catalog(ROOT);required={suite['id']:suite for suite in catalog if 'acceptance' in suite.get('profiles',[]) and suite.get('category')!='live'}
    results={item['suite']:item for item in evidence.get('results',[])}
    if not set(required)<=set(results):raise RuntimeError('Acceptance evidence omits required local suites.')
    environments={}
    for identity,suite in required.items():
        result=results[identity]
        if result['outcome'] not in {'Passed','Not applicable'}:raise RuntimeError('Required local acceptance has failed, blocked, skipped or reused results.')
        environment=suite['environment']
        if environment not in environments:environments[environment]=coordinator.environment_fingerprint(coordinator.resolve_environment(ROOT,environment))
        current=coordinator.input_fingerprint(ROOT,suite,environments[environment])
        if current['sha256']!=result['fingerprint']:raise RuntimeError('Acceptance evidence is stale for '+identity+'; rebuild and rerun affected final acceptance before activation.')
    return {'status':'Passed','suites':len(required),'summary':str(Path(path).resolve())}

def verify_writers():
    command="Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'python|msedge|chrome|wscript|cscript'} | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    data=subprocess.check_output(['powershell','-NoProfile','-Command',command],text=True,creationflags=0x08000000)
    processes=json.loads(data) if data.strip() else [];processes=[processes] if isinstance(processes,dict) else processes
    needles=[str(ROOT/'French Notes').casefold(),str(ROOT/'Our_Home_Telegram_Bot').casefold(),str(Path(os.environ['LOCALAPPDATA'])/'French Notes').casefold()]
    if any(p['ProcessId']!=os.getpid() and any(needle in (p.get('CommandLine') or '').casefold() for needle in needles) for p in processes):raise RuntimeError('An original app/editor/profile writer is active. Close it normally; no process was terminated.')

def verify_publications(publications):
    for item in publications:
        target=Path(item['target']);staged=Path(item['staged'])
        if 'before_sha256' not in item or 'staged_sha256' not in item:raise RuntimeError('Launcher publication lacks before/after fingerprints; refresh its maintained staging tool.')
        actual=sha256(target) if target.exists() else None
        if actual!=item['before_sha256'] or sha256(staged)!=item['staged_sha256']:raise RuntimeError('A launcher or shortcut changed after staging; preserve and reconcile before activation.')

def apply(checks):
    if (ROOT/'config/installation.json').exists():
        current=Installation(ROOT).assert_active()
        return {'status':'Already applied','generation':current['generation'],'intent':'preserved; no re-enrollment or state overwrite'}
    evidence=verify_acceptance(checks)
    if not (STAGE/'prepared.json').is_file():raise RuntimeError('Run --prepare first, without starting production processes.')
    freshness=validate_state();configuration=STAGE/'configuration'
    publications=json.loads((STAGE/'launcher-publications/publication-plan.json').read_text('utf-8'))
    paths=[ROOT/'config'/p.relative_to(configuration) for p in configuration.rglob('*') if p.is_file()]
    paths+=[Path(item['target']) for item in publications]
    old_home_config=ROOT/'Our_Home_Telegram_Bot/data/config.json';paths.append(old_home_config)
    (ROOT/'data').mkdir(exist_ok=True)
    with ResourceLock('core:'+str(ROOT).casefold()),FileLease(ROOT/'data/maintenance.lock',exclusive=True),maintenance_gate(ROOT):
        verify_writers();freshness=validate_state();verify_publications(publications)
        journal=Journal(ROOT);journal.begin('initial-installation',None,'initial-1.0.0',publication_paths=paths)
        journal.checkpoint('state_validated',validated=True,preservation='backups/pre-migration-20261111T191326Z',freshness=freshness,acceptance=str(Path(checks).resolve()))
        for name in ('home','french'):
            source=STAGE/'state'/name;target=ROOT/'data'/name
            if target.exists():
                expected={str(p.relative_to(source)):sha256(p) for p in source.rglob('*') if p.is_file()}
                actual={str(p.relative_to(target)):sha256(p) for p in target.rglob('*') if p.is_file()}
                if actual!=expected:raise RuntimeError('Existing canonical state differs; preserve and reconcile instead of overwriting it.')
            else:
                temporary=ROOT/'data'/(name+'.installing-'+uuid.uuid4().hex);shutil.copytree(source,temporary);replace_with_retry(temporary,target)
            journal.checkpoint('state_published_'+name)
        for source in configuration.rglob('*'):
            if source.is_file():atomic_json(ROOT/'config'/source.relative_to(configuration),json.loads(source.read_text('utf-8')))
        journal.publish_generation()
        journal.checkpoint('before_shortcut_publication')
        for item in publications:
            target=Path(item['target']);target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.publishing');shutil.copy2(item['staged'],temporary);replace_with_retry(temporary,target)
        # The credential-bearing original is sealed in both original and journal recovery.
        public_home=json.loads(old_home_config.read_text('utf-8'));public_home.pop('bot_token',None);atomic_json(old_home_config,public_home)
        commands=CommandService(ROOT,recover=False)
        try:
            for identity in ('home','french'):commands.enroll(json.loads((ROOT/'config/bots'/(identity+'.json')).read_text('utf-8')),enabled=True,desired='running')
        finally:commands.close()
        journal.checkpoint('installation_validated',canonical_state_present=True,launchers_published=len(publications))
        journal.complete()
        for identity in ('home','french'):Installation(ROOT).require_state(identity)
    outcome={'status':'Completed','generation':'initial-1.0.0','initial_intent':'enabled/running enrolled once; credentials and health still gate execution','production_polling_started':False,'local_acceptance':str(Path(checks).resolve()),'canonical_state':'data/'}
    atomic_json(STAGE/'activation-result.json',outcome);return outcome

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--apply',action='store_true');parser.add_argument('--checks',type=Path);args=parser.parse_args()
    if args.apply and not args.checks:parser.error('--apply requires --checks fresh-local-summary.json')
    result=apply(args.checks) if args.apply else prepare() if args.prepare else validate_state()
    print(json.dumps(result))
if __name__=='__main__':main()
