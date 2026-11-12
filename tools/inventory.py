"""Read-only installation inventory and protected, independently checked recovery.

This initial tool intentionally uses only Python 3.11's standard library.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, io, json, os, sqlite3, struct, subprocess, time, zipfile
from pathlib import Path
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[1]
class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

def crypt(data, decrypt=False):
    buf = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))); dst = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ctypes.WinError()
    try: return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        ctypes.windll.kernel32.LocalFree(dst.data)

def private(path):
    account = subprocess.check_output(['whoami','/user','/fo','csv','/nh'], text=True).strip().split(',')[-1].strip('"')
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*'+account+':(OI)(CI)F'], check=True, stdout=subprocess.DEVNULL)

def files(base):
    for directory, dirs, names in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink() and not (os.stat(Path(directory)/d, follow_symlinks=False).st_file_attributes & 0x400))
        for name in sorted(names):
            p = Path(directory)/name
            if not p.is_symlink(): yield p

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def sql_summary(p):
    con=sqlite3.connect(p.as_uri()+'?mode=ro', uri=True)
    try:
        con.text_factory=bytes
        tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables=[t.decode('utf-8') for t in tables]
        result={'integrity':con.execute('PRAGMA integrity_check').fetchone()[0].decode(), 'tables':{}}
        for table in tables:
            rows=con.execute('SELECT * FROM "'+table.replace('"','""')+'"').fetchall()
            encoded=sorted(json.dumps(row,sort_keys=True,ensure_ascii=False,default=lambda b: b.hex()) for row in rows)
            result['tables'][table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
        return result
    finally: con.close()

def protect_sources(destination, sources):
    destination.mkdir(parents=True,exist_ok=False); private(destination)
    manifest={'schema':1,'created':time.time(),'encryption':'Windows current-user DPAPI per 4MiB frame','sources':[], 'files':[], 'directories':[]}
    for label,source in sources:
        if not source.exists(): continue
        manifest['sources'].append({'label':label,'path':str(source)})
        if source.is_dir():
            for directory, dirs, _ in os.walk(source, followlinks=False):
                dirs[:]=sorted(d for d in dirs if not (Path(directory)/d).is_symlink() and not (os.stat(Path(directory)/d, follow_symlinks=False).st_file_attributes & 0x400))
                manifest['directories'].append({'source':label,'relative':Path(directory).relative_to(source).as_posix()})
        for p in files(source) if source.is_dir() else [source]:
            rel=p.relative_to(source).as_posix() if source.is_dir() else p.name
            item={'source':label,'relative':rel,'bytes':p.stat().st_size,'sha256':digest(p),'member':f'{len(manifest["files"]):08d}.dpapi'}
            if p.suffix in {'.sqlite3','.sqlite','.db'}:
                item['sqlite']=sql_summary(p)
            with p.open('rb') as src, (destination/item['member']).open('wb') as out:
                while True:
                    chunk=src.read(4*1024*1024)
                    if not chunk:break
                    encrypted=crypt(chunk);out.write(struct.pack('<I',len(encrypted)));out.write(encrypted)
                out.flush();os.fsync(out.fileno())
            # Independent private decryption: compare source and recovered stream hashes.
            h=hashlib.sha256()
            with (destination/item['member']).open('rb') as sealed:
                while header:=sealed.read(4): h.update(crypt(sealed.read(struct.unpack('<I',header)[0]),True))
            if h.hexdigest()!=item['sha256']:raise RuntimeError('Protected recovery verification failed')
            manifest['files'].append(item)
    manifest['verified']=True
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {'files':len(manifest['files']),'bytes':sum(f['bytes'] for f in manifest['files']),'path':str(destination),'verified':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--capture',action='store_true');args=parser.parse_args()
    out=ROOT/'work/inventory';out.mkdir(parents=True,exist_ok=True)
    bases=[('home',ROOT/'Our_Home_Telegram_Bot'),('french',ROOT/'French Notes')]
    external=[('reader',Path(os.environ['LOCALAPPDATA'])/'French Notes Telegram'),('editor',Path(os.environ['LOCALAPPDATA'])/'French Notes')]
    inventory=[]
    for label,base in bases+external:
        if not base.exists():continue
        for p in files(base):
            inventory.append({'owner':label,'path':str(p),'relative':p.relative_to(base).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)})
    (out/'files.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    totals={}
    for entry in inventory:totals[entry['owner']]=totals.get(entry['owner'],0)+entry['bytes']
    print(json.dumps({'files':len(inventory),'logical_bytes':totals}))
    if args.capture:
        # Recover all application source/state, the complete nested repo and external app state.
        # Reproducible dependencies and bulky historical output remain inventoried in place.
        sources=[]
        for label,base in bases:
            for child in base.iterdir():
                if child.name in {'.venv','.venv-bot','node_modules','checks','.local-telegram','__pycache__'}:continue
                sources.append((label+'-'+child.name,child))
        sources+=external
        print(json.dumps(protect_sources(ROOT/'backups'/('pre-migration-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())),sources)))

if __name__=='__main__':main()
