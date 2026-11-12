"""Current-user DPAPI credentials; online verification precedes replacement."""
from __future__ import annotations
import argparse,ctypes,getpass,json,os,re,subprocess,tempfile,urllib.request,urllib.error,sys,warnings
from pathlib import Path
from ctypes import wintypes
from .locks import user_sid
from .paths import Installation

class CredentialError(RuntimeError):pass
class IdentityError(CredentialError):pass
class ConnectivityError(RuntimeError):pass
class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(data,decrypt=False):
    if os.name!='nt':raise CredentialError('Windows DPAPI is required.')
    buffer=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)):raise CredentialError('Current-user credential decryption/protection failed.')
    try:return ctypes.string_at(out.data,out.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[wintypes.HLOCAL];ctypes.windll.kernel32.LocalFree(out.data)

def private_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    subprocess.run(['icacls',str(path),'/reset'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
    subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r','*'+user_sid()+':(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
    return path

def verify_identity(token,expected_id=None,expected_username=None):
    if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}',token or ''):raise CredentialError('Missing or invalid credential format. Use masked local credential setup.')
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/getMe',data=b'{}',headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:body=json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401,404):raise CredentialError('Telegram rejected the credential.') from None
        raise ConnectivityError('Telegram identity service is temporarily unavailable.') from None
    except (OSError,ValueError):raise ConnectivityError('Cannot reach Telegram to verify identity; no credential was changed.') from None
    if not body.get('ok'):raise CredentialError('Telegram rejected the credential.')
    me=body['result']
    if expected_id is not None and int(me['id'])!=int(expected_id):raise IdentityError('Credential belongs to a different bot. Existing state and credential were preserved.')
    if expected_username and me.get('username','').casefold()!=expected_username.casefold():raise IdentityError('Credential username does not match the enrolled bot.')
    return {'id':int(me['id']),'username':me.get('username','')}

class CredentialStore:
    def __init__(self,root):self.root=Path(root);self.directory=self.root/'.secrets'
    def path(self,ref):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',ref):raise CredentialError('Invalid credential reference.')
        return self.directory/(ref+'.dpapi')
    def load(self,ref):
        try:return protect(self.path(ref).read_bytes(),True).decode('utf-8')
        except OSError:raise CredentialError('Credential unavailable. Run masked local credential setup.') from None
    def store_protected(self,ref,token):
        private_directory(self.directory);target=self.path(ref);sealed=protect(token.encode())
        if protect(sealed,True).decode()!=token:raise CredentialError('Credential protection verification failed.')
        fd,temp=tempfile.mkstemp(prefix='credential-',dir=self.directory)
        try:
            with os.fdopen(fd,'wb') as out:out.write(sealed);out.flush();os.fsync(out.fileno())
            os.replace(temp,target)
        finally:Path(temp).unlink(missing_ok=True)
    def stage_replace(self,ref,token,expected_id=None,expected_username=None):
        identity=verify_identity(token,expected_id,expected_username)
        self.store_protected(ref,token);return identity

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--bot',required=True);args=parser.parse_args()
    installation=Installation(args.root);bot=installation.bot(args.bot)
    if bot.get('telegram_id') is None:raise SystemExit('Use python -m bot_center.credential_setup --root ROOT --bot ID to enroll an unknown numeric identity before storing credentials.')
    if not sys.stdin.isatty():raise SystemExit('Credential input requires a local interactive terminal. Run SETUP_FRENCH_CREDENTIAL.cmd.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        token=getpass.getpass('Telegram token (hidden, local only): ').strip()
    try:
        identity=CredentialStore(installation.root).stage_replace(bot['credential_ref'],token,bot.get('telegram_id'),bot.get('expected_username'))
        print('Verified and protected credential for expected bot identity',identity['id'])
    except (CredentialError,ConnectivityError) as error:print(str(error));raise SystemExit(2)

if __name__=='__main__':main()
