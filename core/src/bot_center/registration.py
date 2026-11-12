"""One generator/registration implementation for dashboard and CLI adapters.

Registry publication is a recoverable multi-file operation; new projects are
always enrolled disabled and stopped. Existing projects are inspected, never
replaced with template source or newly initialized state.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import venv
from bot_center_runtime.redaction import TOKEN

TEMPLATE_VERSION='1.0.0'
RUNTIME_VERSION='1.0.0'
RESERVED={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
_LOCK=threading.RLock()


class RegistrationError(ValueError):pass


def normalized(value):
    return unicodedata.normalize('NFC',str(value)).casefold()


def valid_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*',value) or len(value)>48 or value.upper() in RESERVED:
        raise RegistrationError('Bot ID must be a nonreserved lowercase name of at most 48 letters, digits and internal hyphens.')
    return value


def _json(path,default=None):
    return json.loads(path.read_text('utf-8')) if path.is_file() else default


def _atomic(path,content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        _replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def _replace(source,target):
    for attempt in range(7):
        try:os.replace(source,target);return
        except PermissionError as error:
            if os.name!='nt' or getattr(error,'winerror',None) not in {5,32,33} or attempt==6:raise
            time.sleep(.01*(2**attempt))


def _encoded(value):
    return (json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+'\n').encode('utf-8')


@contextmanager
def registration_lease(root):
    path=root/'data/core/registration.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with _LOCK,path.open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RegistrationError('Another registration is in progress.') from None
        else:
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise RegistrationError('Another registration is in progress.') from None
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class RegistrationService:
    def __init__(self,root,command_service=None,template_root=None):
        self.root=Path(root).resolve(strict=True)
        self.command_service=command_service
        self.template_root=Path(template_root).resolve() if template_root else self.root/'templates/python_bot'
        self.journals=self.root/'data/core/registrations'

    def _resolve(self,value,required=False):
        if not isinstance(value,str) or not value or '\x00' in value:
            raise RegistrationError('Canonical paths must be nonempty strings.')
        raw=Path(value)
        if any(part in {'.','..'} or part.rstrip(' .')!=part or part.split('.')[0].upper() in RESERVED or re.search(r'[<>:"|?*\x00-\x1f]',part) for part in raw.parts if part not in {raw.anchor,'\\','/'}):
            raise RegistrationError('Canonical path contains an unsafe or reserved component.')
        path=raw if raw.is_absolute() else self.root/raw
        resolved=path.resolve(strict=required)
        if not resolved.is_relative_to(self.root):
            raise RegistrationError('Registered runtime paths must stay inside this installation.')
        # A link/junction is not interchangeable with the inventoried canonical
        # location. Reject even an in-root redirect to avoid alias collisions.
        current=path
        while current!=self.root and current.is_relative_to(self.root):
            if current.exists():
                attrs=getattr(current.stat(follow_symlinks=False),'st_file_attributes',0)
                if current.is_symlink() or attrs&0x400:
                    raise RegistrationError('Linked or junction runtime paths require explicit relocation before registration.')
            current=current.parent
        return resolved

    def _entries(self):
        registry=_json(self.root/'config/registry.json',{'schema':1,'bots':[]})
        entries=[]
        for entry in registry.get('bots',[]):
            if isinstance(entry,str):entry=_json(self._resolve(entry,True))
            elif isinstance(entry,dict) and 'manifest' in entry:entry=_json(self._resolve(entry['manifest'],True))
            if not isinstance(entry,dict):raise RegistrationError('Existing registry is malformed.')
            entries.append(entry)
        return registry,entries

    def validate(self,manifest,existing=True,allow_registered_id=None):
        if not isinstance(manifest,dict):raise RegistrationError('Manifest must be an object.')
        # Generic rejection deliberately omits malformed values from diagnostics.
        if TOKEN.search(json.dumps(manifest,ensure_ascii=True)) or any(key.lower() in {'token','bot_token','password','enabled','desired','attention'} for key in manifest):
            raise RegistrationError('Manifest contains credentials or runtime intent; use the protected credential and command interfaces.')
        value=dict(manifest);valid_id(value.get('id'))
        if value.get('schema_version')!=1 or value.get('contract_version')!=1:
            raise RegistrationError('Unsupported manifest schema or managed contract version.')
        display=value.get('display_name')
        if not isinstance(display,str) or not display.strip() or display!=display.strip() or len(display)>96 or any(ord(c)<32 for c in display):
            raise RegistrationError('Display name must be 1–96 visible characters without surrounding whitespace.')
        package=value.get('package','')
        if not isinstance(package,str) or not re.fullmatch(r'[a-z][a-z0-9_]*',package) or package.upper() in RESERVED:
            raise RegistrationError('Package must be a valid lowercase Python package name.')
        module=value.get('module','')
        if not isinstance(module,str) or not re.fullmatch(re.escape(package)+r'(?:\.[a-z][a-z0-9_]*)+',module):
            raise RegistrationError('Entry point must be an importable module owned by the declared package.')
        telegram_id=value.get('telegram_id')
        if telegram_id is not None and (isinstance(telegram_id,bool) or not isinstance(telegram_id,int) or telegram_id<=0):
            raise RegistrationError('Expected Telegram identity must be a positive numeric bot ID.')
        capabilities=value.get('capabilities',[])
        if not isinstance(capabilities,list) or any(not isinstance(c,str) for c in capabilities) or len(set(capabilities))!=len(capabilities) or any(c not in {'polling','maintenance'} for c in capabilities):
            raise RegistrationError('Unsupported or duplicated capability.')
        credential=value.get('credential_ref')
        if not isinstance(credential,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',credential):
            raise RegistrationError('Credential reference must be a nonsecret local identifier.')
        paths={key:self._resolve(value[key],required=existing and key in {'python','cwd','data_dir'}) for key in ('python','cwd','data_dir','log_dir')}
        if paths['python'].name.casefold() not in {'python.exe','python'} or not paths['python'].is_relative_to(self.root/'.venvs'):
            raise RegistrationError('Executable must be a Python interpreter in the installation environments.')
        for key in ('cwd','data_dir','log_dir'):
            if paths[key]==self.root or paths[key].is_relative_to(self.root/'config') or paths[key].is_relative_to(self.root/'.secrets'):
                raise RegistrationError('Runtime working/state/log paths must use dedicated namespaces.')
        if paths['data_dir']==paths['log_dir']:
            raise RegistrationError('State and logs need separate namespaces.')
        project=self._resolve(value.get('project_dir','bots/'+value['id']),required=existing)
        required=value.get('required_state')
        if not isinstance(required,list) or not required or any(not isinstance(v,str) or not v or Path(v).is_absolute() or '..' in Path(v).parts for v in required):
            raise RegistrationError('Declare the expected state files; missing state is never initialized during registration.')
        for relative in required:
            required_path=(paths['data_dir']/relative).resolve()
            if not required_path.is_relative_to(paths['data_dir']):raise RegistrationError('Required state escapes its namespace.')
            if existing and not required_path.is_file():raise RegistrationError('Expected state is missing. Restore it before registering this project.')
        argv=value.get('argv')
        if argv is not None:
            expected=[value['python'],'-m','bot_center_runtime.runner','--root',str(self.root),'--bot',value['id']]
            if argv!=expected:raise RegistrationError('Executable arguments must use the supported installed module runner.')
        jobs=value.get('jobs',[])
        if not isinstance(jobs,list):raise RegistrationError('Jobs must be a list of declared contracts.')
        job_ids=[]
        for job in jobs:
            if not isinstance(job,dict):raise RegistrationError('Job contract must be an object.')
            name=valid_id(job.get('id'))
            if name in job_ids:raise RegistrationError('Duplicate job identity.')
            job_ids.append(name)
            if any(k in job for k in ('shell','command','script')):raise RegistrationError('Jobs cannot contain arbitrary shell command text.')
            if job.get('module') and not re.fullmatch(re.escape(package)+r'(?:\.[a-z][a-z0-9_]*)+',job['module']):
                raise RegistrationError('Job entry point must belong to the declared package.')
        _,entries=self._entries()
        for other in entries:
            if other['id']==allow_registered_id:continue
            if normalized(other['id'])==normalized(value['id']) or normalized(other.get('display_name',''))==normalized(display) or normalized(other.get('package',''))==normalized(package):
                raise RegistrationError('Bot ID, display name or package collides with a registered project.')
            if telegram_id is not None and other.get('telegram_id')==telegram_id:
                raise RegistrationError('This numeric Telegram identity is already registered under another project.')
            for key in ('data_dir','log_dir'):
                if key in other:
                    old_path=self._resolve(other[key]);new_path=paths[key]
                    if normalized(old_path)==normalized(new_path) or old_path.is_relative_to(new_path) or new_path.is_relative_to(old_path):
                        raise RegistrationError('Runtime paths collide with a registered namespace.')
        if existing:
            if not (project/'pyproject.toml').is_file() or not (project/'tests').is_dir():
                raise RegistrationError('Existing project needs its own package metadata and applicable tests before registration.')
            source=project/'src'/package
            if not source.is_dir():raise RegistrationError('Declared existing source package was not found; no template replacement was attempted.')
            target=source.joinpath(*module.split('.')[1:]).with_suffix('.py')
            if not target.is_file() and not target.with_suffix('').is_dir():raise RegistrationError('Declared module entry point does not exist in the existing package.')
        return value

    def inspect(self,manifest):
        value=self.validate(manifest,existing=True)
        project=self._resolve(value.get('project_dir','bots/'+value['id']),True)
        records=[]
        for directory,dirs,names in os.walk(project,followlinks=False):
            dirs[:]=[d for d in dirs if d not in {'.git','.venv','node_modules','__pycache__','build','dist'} and not (Path(directory)/d).is_symlink()]
            for name in sorted(names):
                path=Path(directory)/name
                if path.is_symlink():continue
                records.append({'path':path.relative_to(project).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
        return {'manifest':value,'project':str(project),'files':records,'instructions':[r['path'] for r in records if r['path'].endswith('AGENTS.md')],
                'dependencies':[r['path'] for r in records if Path(r['path']).name in {'pyproject.toml','requirements.txt','requirements.lock','package-lock.json'}],
                'tests':[r['path'] for r in records if r['path'].startswith('tests/')]}

    def _manifest(self,bot_id,display_name,telegram_id,capabilities):
        package=bot_id.replace('-','_')+'_bot'
        python='.venvs/'+bot_id+'/Scripts/python.exe'
        return {'schema_version':1,'contract_version':1,'id':bot_id,'display_name':display_name,'package':package,
            'module':package+'.managed','python':python,'argv':[python,'-m','bot_center_runtime.runner','--root',str(self.root),'--bot',bot_id],
            'cwd':'data/'+bot_id,'data_dir':'data/'+bot_id,'log_dir':'logs/'+bot_id,'project_dir':'bots/'+bot_id,
            'credential_ref':bot_id,'telegram_id':telegram_id,'capabilities':capabilities,'jobs':[],
            'required_state':['namespace.json','bot.sqlite3'],'template_version':TEMPLATE_VERSION,'runtime_version':RUNTIME_VERSION}

    def _render(self,manifest,stage):
        substitutions={'__BOT_ID__':manifest['id'],'__BOT_DISPLAY_NAME__':manifest['display_name'],
            '__BOT_PACKAGE__':manifest['package'],'__BOT_DISTRIBUTION__':manifest['id']+'-bot'}
        if not self.template_root.is_dir():raise RegistrationError('Maintained Python bot template is missing.')
        stage.mkdir(parents=True,exist_ok=False)
        for directory,dirs,names in os.walk(self.template_root):
            dirs[:]=[d for d in dirs if d not in {'__pycache__','build','dist'}]
            for name in names:
                source=Path(directory)/name
                relative=source.relative_to(self.template_root).as_posix()
                for key,replacement in substitutions.items():relative=relative.replace(key,replacement)
                if relative.endswith('.template'):relative=relative[:-9]
                content=source.read_text('utf-8')
                for key,replacement in substitutions.items():
                    if key=='__BOT_DISPLAY_NAME__' and (relative.endswith('.py') or relative.endswith('.toml')):
                        replacement=replacement.replace('\\','\\\\').replace('"','\\"')
                    content=content.replace(key,replacement)
                target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')

    def _install(self,manifest,shared_wheel=None):
        environment=self.root/'.venvs'/manifest['id'];python=environment/'Scripts/python.exe'
        if environment.exists():raise RegistrationError('Independent environment already exists; creation never overwrites it.')
        wheelhouse=self.root/'work/build/wheelhouse';wheelhouse.mkdir(parents=True,exist_ok=True)
        shared=Path(shared_wheel).resolve(strict=True) if shared_wheel else wheelhouse/f'bot_center_runtime-{RUNTIME_VERSION}-py3-none-any.whl'
        if not shared.is_file():raise RegistrationError('Build the matching shared-runtime wheel before installing a new bot.')
        project=self._resolve(manifest['project_dir'],True)
        build=self.root/'work/build'/manifest['id'];build.mkdir(parents=True,exist_ok=True)
        report=build/'install.json'
        commands=[
            [str(python),'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',str(wheelhouse),str(project)],
            [str(python),'-m','pip','install','--no-index','--no-deps','--report',str(report),str(shared),str(wheelhouse/(manifest['id'].replace('-','_')+'_bot-1.0.0-py3-none-any.whl'))],
        ]
        venv.EnvBuilder(with_pip=True).create(environment)
        # setuptools/wheel are build inputs, obtained from the pinned environment
        # wheelhouse when present rather than copying or moving an environment.
        build_inputs=list(wheelhouse.glob('setuptools-*.whl'))+list(wheelhouse.glob('wheel-*.whl'))
        bootstrap=[str(python),'-m','pip','install','--no-index','--no-deps',*[str(p) for p in build_inputs]] if build_inputs else [str(python),'-m','pip','install','setuptools==80.9.0','wheel==0.45.1']
        commands.insert(0,bootstrap)
        with (build/'build.log').open('wb') as output:
            for command in commands:
                result=subprocess.run(command,cwd=build,stdout=output,stderr=subprocess.STDOUT,timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise RegistrationError('Bot environment build failed; consult its local build log. The project was not activated.')
        freeze=subprocess.check_output([str(python),'-m','pip','freeze','--all'],text=True)
        (project/'requirements.lock').write_text(freeze,encoding='utf-8')
        subprocess.run([str(python),'-m',manifest['module'],'--initialize',str(self._resolve(manifest['data_dir']))],check=True,cwd=build,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (self._resolve(manifest['log_dir'])).mkdir(parents=True,exist_ok=False)
        check=subprocess.run([str(python),'-m','unittest','discover','-s',str(project/'tests'),'-p','test_*.py'],cwd=build,capture_output=True,text=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (build/'contracts.log').write_text(check.stdout+check.stderr,encoding='utf-8')
        if check.returncode:raise RegistrationError('Generated bot contract checks failed; registration remains unpublished.')
        return {'python':str(python),'install_report':str(report),'contract_checks':'Passed','dependency_lock':str(project/'requirements.lock')}

    def create(self,bot_id,display_name,telegram_id=None,capabilities=None,install=True,shared_wheel=None):
        valid_id(bot_id)
        manifest=self._manifest(bot_id,display_name,telegram_id,['polling'] if capabilities is None else capabilities)
        self.validate(manifest,existing=False)
        with registration_lease(self.root):
            self.validate(manifest,existing=False)
            final=self._resolve(manifest['project_dir'])
            for path in (final,self._resolve(manifest['data_dir']),self._resolve(manifest['log_dir']),self.root/'.venvs'/bot_id,self.root/'config/bots'/f'{bot_id}.json'):
                if path.exists():raise RegistrationError('Project, environment, manifest or runtime namespace already exists; nothing was overwritten.')
            operation=uuid.uuid4().hex
            stage=self.root/'work/registration'/operation/'project'
            journal={'schema':1,'operation_id':operation,'kind':'create','bot_id':bot_id,'phase':'staging','manifest':manifest,'created':time.time(),'created_paths':[],'files':[]}
            self._write_journal(journal)
            try:
                self._render(manifest,stage)
                final.parent.mkdir(parents=True,exist_ok=True);_replace(stage,final)
                journal['created_paths'].append(str(final.relative_to(self.root)));journal['phase']='project_created';self._write_journal(journal)
                if not install:
                    journal['phase']='staged';self._write_journal(journal)
                    return {'operation_id':operation,'status':'Staged','project':str(final),'manifest':manifest,'registered':False,'enabled':False,'desired':'stopped'}
                journal['installation']=self._install(manifest,shared_wheel)
                self.validate(manifest,existing=True)
                return self._publish(manifest,journal)
            except BaseException:
                journal['phase']='failed';journal['failure']='Creation or publication failed; preserved generated artifacts require explicit recovery.';self._write_journal(journal)
                raise

    def register(self,manifest_path):
        path=Path(manifest_path).resolve(strict=True)
        manifest=_json(path)
        inspection=self.inspect(manifest)
        with registration_lease(self.root):
            self.validate(manifest,existing=True)
            operation=uuid.uuid4().hex
            journal={'schema':1,'operation_id':operation,'kind':'register','bot_id':manifest['id'],'phase':'staging','manifest':manifest,'created':time.time(),'files':[],'inspection':inspection}
            self._write_journal(journal)
            return self._publish(manifest,journal)

    def bind_identity(self,bot_id,telegram_id):
        """Declare a missing expected identity; online token verification is separate.

        Existing identities cannot be reassigned by this routine. Registration
        remains disabled/stopped unless the user already changed its core intent.
        """
        valid_id(bot_id)
        if isinstance(telegram_id,bool) or not isinstance(telegram_id,int) or telegram_id<=0:
            raise RegistrationError('Expected Telegram identity must be a positive numeric bot ID.')
        with registration_lease(self.root):
            registry,entries=self._entries()
            matches=[(index,value) for index,value in enumerate(entries) if value['id']==bot_id]
            if len(matches)!=1:raise RegistrationError('Bot is not uniquely registered.')
            index,manifest=matches[0]
            if manifest.get('telegram_id') not in {None,telegram_id}:
                raise RegistrationError('This project already has a different expected identity; deliberate identity migration is required.')
            if manifest.get('telegram_id') is None:
                commands=self.command_service;opened=False
                if commands is None:
                    from .commands import CommandService
                    commands=CommandService(self.root,recover=False);opened=True
                try:
                    if callable(getattr(commands,'bot',None)):
                        state=commands.bot(bot_id)
                        if state.get('enabled') or state.get('desired')!='stopped' or state.get('run_id'):
                            raise RegistrationError('Disable and stop this project before binding its missing expected identity.')
                finally:
                    if opened:commands.close()
            manifest={**manifest,'telegram_id':telegram_id}
            self.validate(manifest,existing=True,allow_registered_id=bot_id)
            entry=registry['bots'][index]
            if isinstance(entry,str):relative=entry;content=_encoded(manifest)
            elif isinstance(entry,dict) and 'manifest' in entry:relative=entry['manifest'];content=_encoded(manifest)
            else:
                registry=dict(registry);registry['bots']=list(registry['bots']);registry['bots'][index]=manifest
                relative='config/registry.json';content=_encoded(registry)
            target=self._resolve(relative,True);before=target.read_bytes()
            operation=uuid.uuid4().hex
            journal={'schema':1,'operation_id':operation,'kind':'bind_identity','bot_id':bot_id,'phase':'prepared','manifest':manifest,'created':time.time(),
                'files':[{'path':str(target.relative_to(self.root)),'before':base64.b64encode(before).decode(),'after':base64.b64encode(content).decode(),'after_sha256':hashlib.sha256(content).hexdigest()}]}
            self._write_journal(journal)
            return self._finish_publication(journal)

    def _write_journal(self,journal):
        self.journals.mkdir(parents=True,exist_ok=True)
        _atomic(self.journals/(journal['operation_id']+'.json'),_encoded(journal))

    def _publish(self,manifest,journal):
        registry,_=self._entries();registry=dict(registry)
        registry['bots']=list(registry.get('bots',[]))+[{'manifest':'config/bots/'+manifest['id']+'.json'}]
        suites=_json(self.root/'config/suites.json',{'schema':1,'suites':[]})
        suite={'id':manifest['id']+'.contract','owner':manifest['id'],'environment':manifest['id'],
            'command':['{python}','-m','unittest','discover','-s','{root}/'+manifest.get('project_dir','bots/'+manifest['id'])+'/tests','-p','test_*.py'],
            'inputs':[manifest.get('project_dir','bots/'+manifest['id']),'shared','config/registry.json'],
            'profiles':['smoke','shared-compatibility','acceptance'],'resources':[],'category':'contracts','platform':'any','timeout_seconds':120,'output':'{run_dir}/'+manifest['id']+'.contract'}
        suites=dict(suites);suites['suites']=list(suites.get('suites',[]))+[suite]
        operations=_json(self.root/'config/operations.json',{'schema':1,'operations':[]});operations=dict(operations)
        operation={'id':manifest['id']+'.contract','owner':manifest['id'],'purpose':'Verify installed managed bot update and state contracts','kind':'developer_tool',
            'entry_point':suite['command'],'environment':manifest['id'],'inputs':suite['inputs'],'write_scope':['work/tests'],
            'resources':[],'timeout_seconds':120,'cancellation':'terminate isolated fixture only','repetition_safety':'safe','output':'work/tests','retention':'routine','verification':[suite['id']]}
        operations['operations']=list(operations.get('operations',[]))+[operation]
        map_path=self.root/'docs/PROJECT_MAP.md';project_map=map_path.read_text('utf-8') if map_path.is_file() else '# Project map\n'
        project_map+='\nRegistered bot `'+manifest['id']+'`: `'+manifest.get('project_dir','bots/'+manifest['id'])+'`, manifest `config/bots/'+manifest['id']+'.json`, installed environment `.venvs/'+manifest['id']+'`; initially disabled/stopped.\n'
        changes=[('config/bots/'+manifest['id']+'.json',_encoded(manifest)),('config/suites.json',_encoded(suites)),('config/operations.json',_encoded(operations)),('docs/PROJECT_MAP.md',project_map.encode()),('config/registry.json',_encoded(registry))]
        if not journal.get('files'):
            for relative,content in changes:
                target=self.root/relative;old=target.read_bytes() if target.exists() else None
                if old is not None and TOKEN.search(old.decode('utf-8',errors='ignore')):
                    raise RegistrationError('A publication input contains plaintext credentials; protect them before journaling configuration.')
                journal['files'].append({'path':relative,'before':None if old is None else base64.b64encode(old).decode(),'after':base64.b64encode(content).decode(),'after_sha256':hashlib.sha256(content).hexdigest()})
            journal['phase']='prepared';self._write_journal(journal)
        return self._finish_publication(journal)

    def _finish_publication(self,journal):
        # Validate the entire publication before changing any target. Recovery
        # must not discover newer source/config halfway through a blind restore.
        self._preflight_publication(journal)
        for item in journal['files']:
            target=self._resolve(item['path'])
            current=target.read_bytes() if target.exists() else None
            before=base64.b64decode(item['before']) if item['before'] is not None else None
            after=base64.b64decode(item['after'])
            if current not in (before,after):raise RegistrationError('Publication input changed; preserve it and reconcile the registration journal before continuing.')
            _atomic(target,after)
        journal['phase']='published';self._write_journal(journal)
        commands=self.command_service;opened=False
        if commands is None:
            from .commands import CommandService
            commands=CommandService(self.root,recover=False)
            opened=True
        try:
            if 'enrollment' not in journal:
                try:existing=commands.bot(journal['bot_id'])
                except (KeyError,RuntimeError):existing=None
                journal['enrollment']={'created':existing is None}
                journal['phase']='enrolling';self._write_journal(journal)
            commands.enroll(journal['manifest'],enabled=False,desired='stopped')
            journal['phase']='enrolled';self._write_journal(journal)
        finally:
            if opened:commands.close()
        journal['phase']='complete';journal['completed']=time.time();self._write_journal(journal)
        return {'operation_id':journal['operation_id'],'status':'Completed','manifest':journal['manifest'],'registered':True,'enabled':False,'desired':'stopped','credential_verification':'Not performed'}

    def _preflight_publication(self,journal):
        for item in journal.get('files',[]):
            target=self._resolve(item['path']);current=target.read_bytes() if target.exists() else None
            before=base64.b64decode(item['before']) if item['before'] is not None else None
            after=base64.b64decode(item['after'])
            if current not in (before,after):
                raise RegistrationError('A publication input changed; reconcile the preserved registration journal before modifying configuration.')

    def _rollback_enrollment(self,journal):
        if not journal.get('enrollment',{}).get('created'):return
        commands=self.command_service;opened=False
        if commands is None:
            from .commands import CommandService
            commands=CommandService(self.root,recover=False);opened=True
        try:
            if not hasattr(commands,'db'):
                # In-memory test/service adapters must not invent deletion of
                # unknown persistence. Production uses CommandService SQLite.
                return
            with commands.transaction() as db:
                row=db.execute('SELECT * FROM bots WHERE id=?',(journal['bot_id'],)).fetchone()
                if row is None:return
                state=dict(row)
                if state.get('enabled') or state.get('desired')!='stopped' or state.get('revision',0)!=0 or state.get('run_id') or state.get('attention') or state.get('external_hold') or state.get('observed')!='Stopped':
                    raise RegistrationError('Registered project acquired newer runtime work; rollback would discard user intent. Complete/reconcile the preserved publication instead.')
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,column in [('operations','target'),('schedules','target'),('failure_history','bot_id')]:
                    if table in tables and db.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1',(journal['bot_id'],)).fetchone():
                        raise RegistrationError('Registered project has accepted work or evidence; automatic rollback is blocked to preserve it.')
                db.execute('DELETE FROM bots WHERE id=?',(journal['bot_id'],))
        finally:
            if opened:commands.close()

    def recover(self,operation_id,complete=False):
        if not re.fullmatch(r'[a-f0-9]{32}',operation_id):raise RegistrationError('Invalid registration operation identity.')
        with registration_lease(self.root):
            journal=_json(self.journals/(operation_id+'.json'))
            if journal is None:raise RegistrationError('Registration journal was not found.')
            if journal['phase']=='complete':return {'status':'Completed','operation_id':operation_id}
            if complete:
                if not journal.get('files'):raise RegistrationError('Project is only staged or its build failed; finish its environment/state preparation and register its manifest explicitly.')
                return self._finish_publication(journal)
            self._preflight_publication(journal)
            self._rollback_enrollment(journal)
            for item in reversed(journal.get('files',[])):
                target=self._resolve(item['path']);current=target.read_bytes() if target.exists() else None
                before=base64.b64decode(item['before']) if item['before'] is not None else None
                after=base64.b64decode(item['after'])
                if current not in (before,after):raise RegistrationError('A publication target changed after failure; automatic rollback would overwrite newer work.')
                if before is None:target.unlink(missing_ok=True)
                else:_atomic(target,before)
            journal['phase']='rolled_back';self._write_journal(journal)
            # Generated source/environments/state remain preserved and unregistered.
            return {'status':'Rolled back','operation_id':operation_id,'preserved_paths':journal.get('created_paths',[])}
