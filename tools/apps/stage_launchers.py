"""Prepare compatibility publications; coordinator activates under the journal."""
from pathlib import Path
import json,os,subprocess,hashlib,time
ROOT=Path(__file__).resolve().parents[2]

def main():
    output=ROOT/'work/migration/initial/launcher-publications';output.mkdir(parents=True,exist_ok=True)
    inventory={str(Path(row['path']).resolve()).casefold():row['sha256'] for row in json.loads((ROOT/'work/inventory/files.json').read_text('utf-8'))}
    observations_path=output/'original-observations.json'
    observations=json.loads(observations_path.read_text('utf-8')) if observations_path.exists() else {}
    mapping={
      'Our_Home_Telegram_Bot/START_BOT.bat':'@echo off\ncall "%~dp0..\\launchers\\home\\START_BOT.cmd" %*\nexit /b %ERRORLEVEL%\n',
      'Our_Home_Telegram_Bot/STOP_BOT.bat':'@echo off\ncall "%~dp0..\\launchers\\home\\STOP_BOT.cmd" %*\nexit /b %ERRORLEVEL%\n',
      'Our_Home_Telegram_Bot/OPEN_LAST_LIST.bat':'@echo off\ncall "%~dp0..\\launchers\\home\\OPEN_LAST_LIST.cmd" %*\n',
      'Our_Home_Telegram_Bot/START_LOCAL_TEST.bat':'@echo off\n"%~dp0..\\.venvs\\home\\Scripts\\python.exe" -m home_bot.simulator %*\n',
      'Our_Home_Telegram_Bot/STOP_LOCAL_TEST.bat':'@echo off\nif exist "%~dp0..\\work\\simulator\\home" > "%~dp0..\\work\\simulator\\home\\stop.request" echo stop\n',
      'Our_Home_Telegram_Bot/ENABLE_AUTOSTART.bat':'@echo off\necho Bot Control Center uses manual START.cmd. No Windows autostart entry was created.\nexit /b 2\n',
      'Our_Home_Telegram_Bot/DISABLE_AUTOSTART.bat':'@echo off\necho No Bot Control Center autostart entries are installed. See the inventoried startup definitions in the recovery runbook.\n',
      'French Notes/START_BOT.bat':'@echo off\ncall "%~dp0..\\launchers\\french\\START_BOT.cmd" %*\nexit /b %ERRORLEVEL%\n',
      'French Notes/PREPARE_AUDIO.bat':'@echo off\ncall "%~dp0..\\launchers\\french\\PREPARE_AUDIO.cmd" %*\nexit /b %ERRORLEVEL%\n',
      'French Notes/PAIR_OWNER.bat':'@echo off\ncall "%~dp0..\\launchers\\french\\PAIR_OWNER.cmd" %*\nexit /b %ERRORLEVEL%\n',
      'French Notes/French Notes.vbs':'Option Explicit\nDim shell,files,target\nSet shell=CreateObject("WScript.Shell")\nSet files=CreateObject("Scripting.FileSystemObject")\ntarget=files.GetAbsolutePathName(files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName),"..\\launchers\\french\\OPEN_EDITOR.vbs"))\nshell.Run "wscript.exe " & Chr(34) & target & Chr(34),0,False\n',
      'Our_Home_Telegram_Bot/RUN_BACKGROUND.vbs':'Option Explicit\nDim shell,files,root,python,command,code\nSet shell=CreateObject("WScript.Shell")\nSet files=CreateObject("Scripting.FileSystemObject")\nroot=files.GetAbsolutePathName(files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName),".."))\npython=Chr(34) & root & "\\.venvs\\core\\Scripts\\pythonw.exe" & Chr(34)\ncommand=python & " -m bot_center --root " & Chr(34) & root & Chr(34)\ncode=shell.Run(command & " start-core",0,True)\nIf code=0 Then code=shell.Run(command & " start --bot home",0,True)\nWScript.Quit code\n',
    }
    publications=[]
    for index,(target,text) in enumerate(mapping.items()):
        staged=output/(str(index)+Path(target).suffix);staged.write_text(text.replace('\n','\r\n'),encoding='utf-8',newline='')
        publications.append({'target':str(ROOT/target),'staged':str(staged),'kind':'compatibility launcher','source_preserved_in':'backups/pre-migration-20261111T191326Z'})
    shortcut=output/'French Notes.lnk'
    script="$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"+str(shortcut).replace("'","''")+"');$s.TargetPath='C:\\Windows\\System32\\wscript.exe';$s.Arguments='\""+str(ROOT/'launchers/french/OPEN_EDITOR.vbs')+"\"';$s.WorkingDirectory='"+str(ROOT)+"';$s.Save()"
    subprocess.run(['powershell','-NoProfile','-Command',script],check=True,creationflags=0x08000000)
    publications.append({'target':str(ROOT/'French Notes/French Notes.lnk'),'staged':str(shortcut),'kind':'owned shortcut','source_preserved_in':'backups/pre-migration-20261111T191326Z'})
    desktop=Path(os.environ['USERPROFILE'])/'Desktop/French Notes.lnk'
    if desktop.is_file():publications.append({'target':str(desktop),'staged':str(shortcut),'kind':'inventoried application-owned desktop shortcut','rollback_capture':'Journal publication_paths before activation'})
    for item in publications:
        target=Path(item['target']).resolve(strict=True)
        if target.is_symlink():raise RuntimeError('Unexpected publication target link; reconcile inventory before activation.')
        actual=hashlib.sha256(target.read_bytes()).hexdigest();key=str(target).casefold()
        expected=inventory.get(key) or observations.get(key,{}).get('sha256')
        if expected and actual!=expected:raise RuntimeError('Inventoried launcher changed; publication is blocked: '+str(target))
        if key not in inventory and key not in observations:observations[key]={'sha256':actual,'observed_at':time.time(),'reason':'Inventoried application-owned shortcut outside initial workspace listing; Journal protects exact bytes before publication.'}
        item['before_sha256']=actual
        item['staged_sha256']=hashlib.sha256(Path(item['staged']).read_bytes()).hexdigest()
    observations_path.write_text(json.dumps(observations,indent=2),encoding='utf-8')
    (output/'publication-plan.json').write_text(json.dumps(publications,indent=2),encoding='utf-8')
    print(json.dumps({'publications':len(publications),'plan':str(output/'publication-plan.json'),'activated':False}))

if __name__=='__main__':main()
