"""Prepare local French release evidence using the maintained coordinator."""
from __future__ import annotations
import argparse,json,subprocess,sys,time
from pathlib import Path
from bot_center_runtime.paths import Installation
from .audit import parity,protect,integrity
from .library import SourceReader,build

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--work-dir',required=True);parser.add_argument('--data-dir');args=parser.parse_args()
    installation=Installation(args.root);root=installation.root
    source=Path(args.data_dir or installation.require_state('french')['notes_dir'])
    work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    protect(source,work);snapshot=build(SourceReader(source).capture())
    coverage=parity(snapshot,work)
    result=subprocess.run([str(root/'.venvs/core/Scripts/python.exe'),str(root/'tools/testing/run_tests.py'),'--suite','french.unit','--fresh'],check=False)
    protected=integrity(work)
    report={'created':time.time(),'coverage':coverage,'coordinator_exit':result.returncode,'preservation':protected,'source_version':snapshot.version,
            'passed':result.returncode==0 and coverage['passed'] and not protected['changed'] and not protected['missing'],
            'live_telegram':'Not performed by local release preparation','phone':'Not performed','publication':'Local only; no remote publication'}
    (work/'release-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'evidence':str(work/'release-evidence.json')}))
    return 0 if report['passed'] else 2

if __name__=='__main__':raise SystemExit(main())
