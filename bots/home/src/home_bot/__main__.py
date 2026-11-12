"""Canonical standalone Home launcher; owner stays alive for the worker lifetime."""
import sys

def main():
    if {'--backup','--restore','--doctor','--data-dir','--background'}.intersection(sys.argv[1:]):
        from .bot import main as compatible
        return compatible()
    from bot_center_runtime.runner import main as launch
    return launch(['--bot','home',*sys.argv[1:]])

if __name__=='__main__':raise SystemExit(main())
