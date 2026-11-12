"""Fresh read-only Codex context discovery; never reads production data/secrets."""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,subprocess,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def run(cwd):
    output=ROOT/'work/context'/('probe-'+str(time.time_ns()));output.mkdir(parents=True)
    prompt=('This is a bounded read-only context/discovery acceptance check. Do not implement or mutate anything, run tests, start apps, use connected services, or read data/.secrets/backups/archive. '
            'Read the applicable AGENTS.md chain, root docs/PROJECT_STATUS.md and PROJECT_MAP.md, and metadata and instructions for the three repository skills add-bot, verify-change, close-task. '
            'Use only local read-only file tools as needed. Report one JSON object with keys cwd, instructions_read (paths), discovered_skills (all three names), objective, verified_work, blockers, next_action. '
            'Derive current work from the checkpoint, not from chat history. If a claim is not verified in the checkpoint preserve that distinction. Stop after returning the object.')
    executable=shutil.which('codex.cmd') or shutil.which('codex')
    args=[executable,'exec','--ephemeral','--sandbox','danger-full-access','--json','--cd',str(cwd),'--output-last-message',str(output/'answer.json'),prompt]
    if executable.lower().endswith('.cmd'):
        # Use the package's JS entry directly, avoiding shell interpolation.
        node=shutil.which('node');entry=Path(executable).parent/'node_modules/@openai/codex/bin/codex.js'
        if entry.is_file():args=[node,str(entry),*args[1:]]
    started=time.monotonic()
    completed=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=300)
    safe=re.sub(r'\d{5,}:[A-Za-z0-9_-]{15,}','[REDACTED]',completed.stdout)
    (output/'events.jsonl').write_text(safe,encoding='utf-8')
    answer=(output/'answer.json').read_text('utf-8') if (output/'answer.json').exists() else ''
    try:parsed=json.loads(answer.strip().removeprefix('```json').removesuffix('```').strip())
    except ValueError:parsed={}
    passed=completed.returncode==0 and bool(parsed.get('objective')) and set(('add-bot','verify-change','close-task'))<=set(parsed.get('discovered_skills',[])) and any('PROJECT_STATUS.md' in path for path in parsed.get('instructions_read',[]))
    report={'status':'Passed' if passed else 'Failed','execution_permissions':'existing unrestricted local account; task instructions prohibit mutation','cwd':str(cwd),'duration_seconds':round(time.monotonic()-started,2),'exit_code':completed.returncode,'answer':'answer.json','events':'events.jsonl','inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'AGENTS.md',ROOT/'docs/PROJECT_STATUS.md',ROOT/'docs/PROJECT_MAP.md']}}
    (output/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({'path':str(output),'status':report['status']}));return 0 if passed else 1
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cwd',type=Path,default=ROOT);args=parser.parse_args();raise SystemExit(run(args.cwd.resolve()))
if __name__=='__main__':main()
