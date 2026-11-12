"""Process-local write boundary; does not change Windows permissions or other apps."""
import os
from pathlib import Path
import sys


class ReadOnlyGuard:
    def __init__(self,*roots):
        self.roots=tuple(Path(p).resolve() for p in roots)
        self.rejected=[]
        self.active=True

    def protected(self,value):
        if not isinstance(value,(str,bytes,os.PathLike)):return False
        path=Path(os.fsdecode(value)).resolve()
        return any(path==root or root in path.parents for root in self.roots)

    def __call__(self,event,args):
        if not self.active:return
        targets=[]
        if event=='open':
            path,mode,flags=args
            if (mode and any(c in mode for c in 'wax+')) or (flags and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
                targets=[path]
        elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.utime','shutil.rmtree','sqlite3.connect'):
            targets=args[:1]
        elif event in ('os.rename','os.link'):
            targets=args[:2]
        elif event in ('os.symlink','shutil.copyfile'):
            targets=args[1:2]
        if any(self.protected(p) for p in targets):
            self.rejected.append(event)
            raise PermissionError('The Telegram process cannot write to notes or desktop application state.')

    def install(self):
        sys.addaudithook(self)
        return self
