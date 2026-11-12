"""Build immutable wheels and recreate Python 3.11 environments at final paths.

Run from any directory. --locked never changes dependency versions. The initial
dependency snapshot is extracted from protected legacy requirements, not updated.
"""
from __future__ import annotations
import argparse, hashlib, json, os, platform, subprocess, sys, time, venv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ENVIRONMENTS={'home':('bots/home',), 'french-reader':('bots/french',), 'french-editor':('bots/french',), 'core':('core',), 'browser':()}

def run(argv,**kwargs):
    subprocess.run([str(a) for a in argv],check=True,**kwargs)

def python(name):return ROOT/'.venvs'/name/'Scripts/python.exe'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(name):return name.lower().replace('_','-').replace('.','-')

def dependency_wheels(wheels,pins):
    found=[]
    for pin in pins:
        name,version=pin.split('==',1)
        matches=[p for p in wheels.glob('*.whl') if canonical(p.name.split('-')[0])==canonical(name) and p.name.split('-')[1]==version]
        if len(matches)!=1:raise RuntimeError('A unique compatible retained wheel is required for '+name)
        found.append({'requirement':pin,'file':matches[0].name,'sha256':sha(matches[0])})
    return found

def prepare(names,locked=True,build=True):
    if sys.version_info[:2]!=(3,11):raise SystemExit('Use Python 3.11 for final environments.')
    output=ROOT/'work/build';output.mkdir(parents=True,exist_ok=True)
    wheels=output/'wheelhouse';wheels.mkdir(exist_ok=True)
    # The build environment is itself reproduced and recorded.
    if not python('build').exists():venv.EnvBuilder(with_pip=True).create(python('build').parents[1])
    build_requirements=Path(__file__).parent/'build.requirements.txt'
    run([python('build'),'-m','pip','download','--only-binary=:all:','--dest',wheels,'-r',build_requirements])
    run([python('build'),'-m','pip','install','--no-index','--find-links',wheels,'--disable-pip-version-check','-r',build_requirements])
    packages=[]
    if (ROOT/'shared/pyproject.toml').exists():packages.append('shared')
    for name in names:
        packages.extend(p for p in ENVIRONMENTS[name] if p not in packages)
    if build:
        for package in packages:
            if not (ROOT/package/'pyproject.toml').exists():continue
            run([python('build'),'-m','pip','wheel','--no-deps','--no-build-isolation','--wheel-dir',wheels,ROOT/package])
    dependency_files=[]
    for name in names:
        req=Path(__file__).parent/(name+'.requirements.txt')
        if req.exists():
            pins=[line.strip() for line in req.read_text('utf-8').splitlines() if line.strip() and not line.startswith('#')]
            import re
            if locked and any(not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+',pin) for pin in pins):raise RuntimeError('Locked environments require exact dependency pins.')
            try:dependency_wheels(wheels,pins)
            except RuntimeError:run([python('build'),'-m','pip','download','--only-binary=:all:','--dest',wheels,'-r',req])
            dependency_files.append(req)
    result={'schema':1,'interpreter':sys.version,'platform':platform.platform(),'built_at':time.time(),'environments':{},'wheels':[],'sources':{},'build_dependencies':subprocess.check_output([str(python('build')),'-m','pip','freeze'],text=True).splitlines()}
    for package in packages:
        for p in sorted((ROOT/package/'src').rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                result['sources'][str(p.relative_to(ROOT))]=sha(p)
    for wheel in sorted(wheels.glob('*.whl')):
        result['wheels'].append({'file':wheel.name,'sha256':sha(wheel),'bytes':wheel.stat().st_size})
    for name in names:
        if not python(name).exists():venv.EnvBuilder(with_pip=True).create(python(name).parents[1])
        req=Path(__file__).parent/(name+'.requirements.txt')
        dependency_lock=Path(__file__).parent/(name+'.lock.json')
        if dependency_lock.exists():
            previous=json.loads(dependency_lock.read_text('utf-8'))
            actual=dependency_wheels(wheels,[x['requirement'] for x in previous['dependencies']])
            if actual!=previous['dependencies']:raise RuntimeError('Retained dependency hash differs from the locked environment; no installation was changed.')
        args=[python(name),'-m','pip','install','--force-reinstall','--no-index','--find-links',wheels,'--report',output/(name+'-install.json')]
        if req.exists():args+=['-r',req]
        if (ROOT/'shared/pyproject.toml').exists():args+=['bot-center-runtime==1.0.0']
        for package in ENVIRONMENTS[name]:
            project=ROOT/package/'pyproject.toml'
            if project.exists():
                import tomllib
                meta=tomllib.loads(project.read_text('utf-8'))['project'];args+=[meta['name']+'=='+meta['version']]
        run(args)
        freeze=subprocess.check_output([str(python(name)),'-m','pip','freeze'],text=True)
        (output/(name+'-installed.txt')).write_text(freeze,encoding='utf-8')
        result['environments'][name]={'python':str(python(name)),'installed':freeze.splitlines(),'report':name+'-install.json'}
        application_names={'bot-center-runtime','home-bot','french-notes','bot-control-center'}
        external=[line for line in freeze.splitlines() if '==' in line and canonical(line.split('==')[0]) not in application_names]
        if not dependency_lock.exists():
            dependency_lock.write_text(json.dumps({'schema':1,'python':'3.11','dependencies':dependency_wheels(wheels,external)},indent=2)+'\n',encoding='utf-8')
            if external:req.write_text('\n'.join(external)+'\n',encoding='utf-8')
        run([python(name),'-m','pip','check'])
    (output/'build-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    # Hashes pin actual retained binaries, including native extension wheels.
    (output/'wheelhouse.sha256').write_text('\n'.join(f"{x['sha256']}  {x['file']}" for x in result['wheels'])+'\n',encoding='utf-8')
    print(json.dumps({'prepared':names,'wheels':len(result['wheels']),'manifest':str(output/'build-manifest.json')}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--all',action='store_true');parser.add_argument('--environment',action='append',choices=ENVIRONMENTS);parser.add_argument('--locked',action='store_true');parser.add_argument('--no-build',action='store_true');args=parser.parse_args()
    prepare(list(ENVIRONMENTS) if args.all else args.environment or ['home','french-reader','french-editor'],args.locked,not args.no_build)

if __name__=='__main__':main()
