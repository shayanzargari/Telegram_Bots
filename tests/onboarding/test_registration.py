from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bot_center.registration import RegistrationService,RegistrationError,TOKEN

TEMPLATE=Path(__file__).resolve().parents[2]/'templates/python_bot'


class Commands:
    def __init__(self):self.enrolled=[];self.states={}
    def enroll(self,manifest,enabled=False,desired='stopped'):
        self.enrolled.append((manifest['id'],enabled,desired))
        self.states.setdefault(manifest['id'],{'enabled':enabled,'desired':desired,'run_id':None})
    def bot(self,bot_id):return self.states[bot_id]


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        (self.root/'config').mkdir();(self.root/'docs').mkdir()
        (self.root/'config/registry.json').write_text('{"schema":1,"bots":[]}',encoding='utf-8')
        self.commands=Commands();self.service=RegistrationService(self.root,self.commands,template_root=TEMPLATE)

    def tearDown(self):self.temporary.cleanup()

    def existing(self,bot_id='garden',display='Garden'):
        staged=self.service.create(bot_id,display,telegram_id=777,install=False)
        manifest=staged['manifest'];project=self.root/manifest['project_dir']
        interpreter=self.root/manifest['python'];interpreter.parent.mkdir(parents=True);interpreter.write_bytes(b'isolated executable marker')
        state=self.root/manifest['data_dir'];state.mkdir(parents=True)
        (state/'namespace.json').write_text('{"schema":1}',encoding='utf-8')
        db=sqlite3.connect(state/'bot.sqlite3');db.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY)');db.commit();db.close()
        (self.root/manifest['log_dir']).mkdir(parents=True)
        path=self.root/'work'/f'{bot_id}.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(manifest),encoding='utf-8')
        return manifest,path,project

    def test_stage_only_never_registers_enrolls_or_initializes_state(self):
        result=self.service.create('garden','Garden',install=False)
        self.assertEqual(result['status'],'Staged');self.assertFalse(result['registered'])
        self.assertEqual(json.loads((self.root/'config/registry.json').read_text())['bots'],[])
        self.assertFalse((self.root/'data/garden').exists());self.assertEqual(self.commands.enrolled,[])
        self.assertTrue((self.root/'bots/garden/pyproject.toml').is_file())

    def test_creation_rejects_windows_invalid_reserved_and_traversal_names(self):
        for name in ('CON','con','aux','nul','com1','lpt9','garden.','garden ','../garden','a/b','a\\b','a:b','Garden','','-x','x-','a__b'):
            with self.subTest(name=name),self.assertRaises(RegistrationError):self.service.create(name,'Fixture',install=False)
        self.assertFalse((self.root/'bots').exists())

    def test_preexisting_project_state_and_environment_not_overwritten(self):
        for relative in ('bots/garden','data/garden','.venvs/garden','logs/garden'):
            target=self.root/relative;target.mkdir(parents=True);sentinel=target/'keep';sentinel.write_text('unique')
            with self.subTest(relative=relative),self.assertRaises(RegistrationError):self.service.create('garden','Garden',install=False)
            self.assertEqual(sentinel.read_text(),'unique');sentinel.unlink();target.rmdir()

    def test_existing_register_preserves_source_rules_locks_and_state(self):
        manifest,path,project=self.existing()
        (project/'requirements.lock').write_text('unique pinned dependency\n')
        (project/'AGENTS.md').write_text('unique rules\n')
        before={p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in project.rglob('*') if p.is_file()}
        state_hash=hashlib.sha256((self.root/'data/garden/bot.sqlite3').read_bytes()).hexdigest()
        result=self.service.register(path)
        after={p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in project.rglob('*') if p.is_file()}
        self.assertEqual(before,after);self.assertEqual(state_hash,hashlib.sha256((self.root/'data/garden/bot.sqlite3').read_bytes()).hexdigest())
        self.assertEqual((result['enabled'],result['desired']),(False,'stopped'))
        self.assertEqual(self.commands.enrolled,[('garden',False,'stopped')])
        self.assertEqual(result['credential_verification'],'Not performed')

    def test_missing_expected_state_rejected_without_initialization(self):
        manifest,path,project=self.existing();(self.root/'data/garden/bot.sqlite3').unlink()
        with self.assertRaises(RegistrationError):self.service.register(path)
        self.assertFalse((self.root/'data/garden/bot.sqlite3').exists());self.assertEqual(self.commands.enrolled,[])

    def test_offline_cli_registration_persists_disabled_stopped_enrollment(self):
        manifest,path,_=self.existing()
        offline=RegistrationService(self.root,template_root=TEMPLATE)
        result=offline.register(path)
        self.assertEqual(result['status'],'Completed')
        db=sqlite3.connect(self.root/'data/core/core.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT enabled,desired FROM bots WHERE id=?',('garden',)).fetchone(),(0,'stopped'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
        finally:db.close()

    def test_later_expected_identity_binding_is_recoverable_and_does_not_change_intent(self):
        manifest,path,_=self.existing();manifest['telegram_id']=None;path.write_text(json.dumps(manifest))
        self.service.register(path)
        result=self.service.bind_identity('garden',777)
        self.assertEqual(result['status'],'Completed')
        saved=json.loads((self.root/'config/bots/garden.json').read_text())
        self.assertEqual(saved['telegram_id'],777)
        self.assertTrue(all(not enabled and desired=='stopped' for _,enabled,desired in self.commands.enrolled))
        with self.assertRaises(RegistrationError):self.service.bind_identity('garden',778)
        self.assertEqual(json.loads((self.root/'config/bots/garden.json').read_text())['telegram_id'],777)

    def test_missing_identity_binding_rejects_enabled_or_running_intent(self):
        manifest,path,_=self.existing();manifest['telegram_id']=None;path.write_text(json.dumps(manifest));self.service.register(path)
        for state in ({'enabled':True,'desired':'stopped'},{'enabled':False,'desired':'running'},{'enabled':False,'desired':'stopped','run_id':'active'}):
            self.commands.states['garden']=state
            with self.subTest(state=state),self.assertRaises(RegistrationError):self.service.bind_identity('garden',777)
        self.assertIsNone(json.loads((self.root/'config/bots/garden.json').read_text())['telegram_id'])

    def test_duplicate_numeric_identity_and_case_collision_rejected(self):
        manifest,path,_=self.existing();self.service.register(path)
        with self.assertRaises(RegistrationError):self.service.create('greenhouse','Other garden',telegram_id=777,install=False)
        with self.assertRaises(RegistrationError):self.service.create('new-garden','GARDEN',telegram_id=778,install=False)

    def test_namespace_overlap_is_collision(self):
        manifest,path,_=self.existing();self.service.register(path)
        another=self.service._manifest('new-garden','Another',778,['polling']);another['data_dir']='data/garden/child'
        with self.assertRaises(RegistrationError):self.service.validate(another,existing=False)

    def test_manifest_rejects_unsupported_contract_and_shell_execution(self):
        manifest,path,_=self.existing()
        bad=deepcopy(manifest);bad['contract_version']=99
        with self.assertRaises(RegistrationError):self.service.validate(bad)
        bad=deepcopy(manifest);bad['python']='cmd.exe'
        with self.assertRaises((RegistrationError,FileNotFoundError)):self.service.validate(bad)
        bad=deepcopy(manifest);bad['argv']=['cmd.exe','/c','echo unsafe']
        with self.assertRaises(RegistrationError):self.service.validate(bad)

    def test_manifest_secret_canary_and_runtime_intent_rejected_without_echo(self):
        manifest,path,_=self.existing()
        canary='123456789:synthetic_canary_ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        for key,value in [('token',canary),('note',canary),('url','https://api.telegram.org/bot'+canary+'/getMe'),('enabled',True),('desired','running')]:
            bad=deepcopy(manifest);bad[key]=value
            with self.subTest(key=key),self.assertRaises(RegistrationError) as caught:self.service.validate(bad)
            self.assertNotIn(canary,str(caught.exception))

    def test_failed_final_journal_rollback_removes_only_new_untouched_enrollment(self):
        manifest,path,_=self.existing();offline=RegistrationService(self.root,template_root=TEMPLATE)
        original=offline._write_journal
        def fail_completion(journal):
            if journal['phase']=='complete':raise OSError('isolated final journal write fault')
            original(journal)
        with patch.object(offline,'_write_journal',side_effect=fail_completion),self.assertRaises(OSError):offline.register(path)
        journal=[json.loads(p.read_text()) for p in (self.root/'data/core/registrations').glob('*.json') if json.loads(p.read_text())['kind']=='register'][0]
        self.assertTrue(journal['enrollment']['created']);self.assertEqual(journal['phase'],'enrolled')
        offline.recover(journal['operation_id'])
        db=sqlite3.connect(self.root/'data/core/core.sqlite3')
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM bots WHERE id=?',('garden',)).fetchone()[0],0)
        finally:db.close()
        self.assertEqual(json.loads((self.root/'config/registry.json').read_text())['bots'],[])

    def test_failed_registration_rollback_blocks_if_newer_user_intent_exists(self):
        manifest,path,_=self.existing();offline=RegistrationService(self.root,template_root=TEMPLATE)
        original=offline._write_journal
        def fail_completion(journal):
            if journal['phase']=='complete':raise OSError('isolated final journal write fault')
            original(journal)
        with patch.object(offline,'_write_journal',side_effect=fail_completion),self.assertRaises(OSError):offline.register(path)
        journal=[json.loads(p.read_text()) for p in (self.root/'data/core/registrations').glob('*.json') if json.loads(p.read_text())['kind']=='register'][0]
        from bot_center.commands import CommandService
        commands=CommandService(self.root,recover=False)
        try:commands.submit('garden','enable')
        finally:commands.close()
        registry=(self.root/'config/registry.json').read_bytes()
        with self.assertRaises(RegistrationError):offline.recover(journal['operation_id'])
        self.assertEqual((self.root/'config/registry.json').read_bytes(),registry)
        db=sqlite3.connect(self.root/'data/core/core.sqlite3')
        try:self.assertEqual(db.execute('SELECT enabled,revision FROM bots WHERE id=?',('garden',)).fetchone(),(1,1))
        finally:db.close()

    def test_registration_publication_fault_is_recoverable_and_never_starts(self):
        manifest,path,_=self.existing()
        original=(self.root/'config/registry.json').read_bytes()
        import bot_center.registration as module
        original_atomic=module._atomic;counter=0
        def fault(target,content):
            nonlocal counter
            if Path(target).name=='registry.json':raise OSError('isolated publication fault')
            return original_atomic(target,content)
        with patch.object(module,'_atomic',side_effect=fault),self.assertRaises(OSError):self.service.register(path)
        self.assertEqual((self.root/'config/registry.json').read_bytes(),original)
        self.assertEqual(self.commands.enrolled,[])
        journals=list((self.root/'data/core/registrations').glob('*.json'))
        prepared=[json.loads(p.read_text()) for p in journals if json.loads(p.read_text())['kind']=='register'][0]
        self.assertEqual(prepared['phase'],'prepared')
        result=self.service.recover(prepared['operation_id'],complete=True)
        self.assertEqual(result['status'],'Completed');self.assertEqual(self.commands.enrolled,[('garden',False,'stopped')])

    def test_registration_rollback_restores_config_preserves_project(self):
        manifest,path,project=self.existing()
        import bot_center.registration as module
        original_atomic=module._atomic
        def fault(target,content):
            if Path(target).name=='registry.json':raise OSError('isolated publication fault')
            original_atomic(target,content)
        with patch.object(module,'_atomic',side_effect=fault),self.assertRaises(OSError):self.service.register(path)
        journal=[json.loads(p.read_text()) for p in (self.root/'data/core/registrations').glob('*.json') if json.loads(p.read_text())['kind']=='register'][0]
        self.service.recover(journal['operation_id'])
        self.assertEqual(json.loads((self.root/'config/registry.json').read_text())['bots'],[])
        self.assertFalse((self.root/'config/bots/garden.json').exists());self.assertTrue(project.is_dir())

    def test_recovery_will_not_overwrite_newer_config_work(self):
        manifest,path,_=self.existing()
        import bot_center.registration as module
        original_atomic=module._atomic
        def fault(target,content):
            if Path(target).name=='registry.json':raise OSError('isolated publication fault')
            original_atomic(target,content)
        with patch.object(module,'_atomic',side_effect=fault),self.assertRaises(OSError):self.service.register(path)
        journal=[json.loads(p.read_text()) for p in (self.root/'data/core/registrations').glob('*.json') if json.loads(p.read_text())['kind']=='register'][0]
        (self.root/'docs/PROJECT_MAP.md').write_text('newer user edit')
        with self.assertRaises(RegistrationError):self.service.recover(journal['operation_id'],complete=True)
        self.assertEqual((self.root/'docs/PROJECT_MAP.md').read_text(),'newer user edit')

    def test_template_display_quotes_do_not_break_python_or_toml(self):
        result=self.service.create('garden','Garden "notes"',install=False)
        import tomllib
        project=Path(result['project'])
        meta=tomllib.loads((project/'pyproject.toml').read_text())
        self.assertIn('Garden "notes"',meta['project']['description'])
        compile((project/'src/garden_bot/__init__.py').read_text(),'<generated>','exec')


if __name__=='__main__':unittest.main()
