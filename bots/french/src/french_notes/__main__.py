"""Canonical reader launcher and compatible finite maintenance entry points."""
import sys

def main():
    finite={'--prepare-audio','--preflight','--review-source','--pair-owner','--offline-smoke','--offline-hold','--credential-check'}
    if finite.intersection(sys.argv[1:]) or {'--data-dir','--state-dir'}.intersection(sys.argv[1:]):
        from .telegram_bot.__main__ import main as maintenance
        return maintenance()
    from bot_center_runtime.runner import main as launch
    return launch(['--bot','french',*sys.argv[1:]])

if __name__=='__main__':raise SystemExit(main())
