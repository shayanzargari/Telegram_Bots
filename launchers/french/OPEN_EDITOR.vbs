Option Explicit
Dim shell, files, root, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetAbsolutePathName(files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "..\.."))
command = Chr(34) & root & "\.venvs\french-editor\Scripts\pythonw.exe" & Chr(34) & " -m french_notes.app --root " & Chr(34) & root & Chr(34)
shell.Run command, 0, False
