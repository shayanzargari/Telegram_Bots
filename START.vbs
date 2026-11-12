Option Explicit
Dim shell, fso, root, python, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(root, ".venvs\core\Scripts\pythonw.exe")
command = """" & python & """ -m bot_center.launcher --root """ & root & """"
shell.Run command, 0, False
