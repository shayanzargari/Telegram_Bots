Option Explicit

Const ShortcutName = "Our Home Bot - Shayan and Dorsa.lnk"
Const ShortcutDescription = "Our Home Bot - current-user login startup"

Dim OurShell, OurFSO, AppFolder, ScriptPath, Mode, IsQuiet
Set OurShell = CreateObject("WScript.Shell")
Set OurFSO = CreateObject("Scripting.FileSystemObject")
ScriptPath = WScript.ScriptFullName
AppFolder = OurFSO.GetParentFolderName(ScriptPath)
Mode = ""
If WScript.Arguments.Count > 0 Then Mode = LCase(WScript.Arguments(0))
IsQuiet = (Mode = "--autostart")

Select Case Mode
    Case "", "--autostart"
        StartInBackground
    Case "--enable-autostart"
        EnableAutostart
    Case "--disable-autostart"
        DisableAutostart
    Case Else
        Fail "Unknown option. Double-click RUN_BACKGROUND.vbs to start the bot."
End Select

Function Quoted(Value)
    Quoted = Chr(34) & Value & Chr(34)
End Function

Function ShortcutPath()
    ShortcutPath = OurFSO.BuildPath(OurShell.SpecialFolders("Startup"), ShortcutName)
End Function

Sub AppendLauncherLog(Message)
    On Error Resume Next
    Dim DataFolder, Stream
    DataFolder = OurFSO.BuildPath(AppFolder, "data")
    If Not OurFSO.FolderExists(DataFolder) Then Exit Sub
    Set Stream = OurFSO.OpenTextFile(OurFSO.BuildPath(DataFolder, "launcher.log"), 8, True)
    If Err.Number = 0 Then
        Stream.WriteLine CStr(Now) & "  " & Message
        Stream.Close
    End If
    On Error GoTo 0
End Sub

Sub Fail(Message)
    AppendLauncherLog Message
    If Not IsQuiet Then WScript.Echo Message
    WScript.Quit 1
End Sub

Sub RequireSetup()
    If Not OurFSO.FileExists(OurFSO.BuildPath(AppFolder, "START_BOT.bat")) Then
        Fail "START_BOT.bat is missing. Extract the complete ZIP first."
    End If
    If Not OurFSO.FileExists(OurFSO.BuildPath(AppFolder, "data\config.json")) Then
        Fail "Run START_BOT.bat first and complete the setup. Then start the background bot."
    End If
End Sub

Sub StartInBackground()
    RequireSetup
    Dim BatchPath, LogPath, CommandLine, Result, ErrorMessage
    BatchPath = OurFSO.BuildPath(AppFolder, "START_BOT.bat")
    LogPath = OurFSO.BuildPath(AppFolder, "data\launcher.log")
    CommandLine = Quoted(OurShell.ExpandEnvironmentStrings("%ComSpec%")) & " /d /c " & _
        Quoted(Quoted(BatchPath) & " --background >> " & Quoted(LogPath) & " 2>&1")
    On Error Resume Next
    OurShell.CurrentDirectory = AppFolder
    Result = OurShell.Run(CommandLine, 0, False)
    If Err.Number <> 0 Then
        ErrorMessage = Err.Description
        On Error GoTo 0
        Fail "The background launcher could not start: " & ErrorMessage
    End If
    On Error GoTo 0
    AppendLauncherLog "Background start requested."
    If Not IsQuiet Then
        WScript.Echo "Background start requested." & vbCrLf & _
            "Check /menu in your Telegram group to confirm it is working." & vbCrLf & _
            "If it does not respond, check data\bot.log and data\launcher.log."
    End If
End Sub

Sub EnableAutostart()
    RequireSetup
    Dim LinkPath, Link, ErrorMessage
    LinkPath = ShortcutPath()
    On Error Resume Next
    Set Link = OurShell.CreateShortcut(LinkPath)
    If Err.Number <> 0 Then
        ErrorMessage = Err.Description
        On Error GoTo 0
        Fail "The startup shortcut could not be opened: " & ErrorMessage
    End If
    On Error GoTo 0
    If OurFSO.FileExists(LinkPath) Then
        If Link.Description <> ShortcutDescription Then
            Fail "A different shortcut already uses this name. It has not been changed: " & LinkPath
        End If
    End If
    On Error Resume Next
    Link.TargetPath = OurFSO.BuildPath(OurFSO.GetSpecialFolder(0), "System32\wscript.exe")
    Link.Arguments = Quoted(ScriptPath) & " --autostart"
    Link.WorkingDirectory = AppFolder
    Link.WindowStyle = 7
    Link.Description = ShortcutDescription
    Link.Save
    If Err.Number <> 0 Then
        ErrorMessage = Err.Description
        On Error GoTo 0
        Fail "The startup shortcut could not be saved: " & ErrorMessage
    End If
    On Error GoTo 0
    WScript.Echo "Autostart enabled for your Windows user only." & vbCrLf & _
        "The bot will start after you sign in. It will not run before sign-in." & vbCrLf & _
        "To start it now, double-click RUN_BACKGROUND.vbs." & vbCrLf & _
        "Keep this bot folder in its current location."
End Sub

Sub DisableAutostart()
    Dim LinkPath, Link, ErrorMessage
    LinkPath = ShortcutPath()
    If Not OurFSO.FileExists(LinkPath) Then
        WScript.Echo "No Our Home Bot startup shortcut was found."
        Exit Sub
    End If
    On Error Resume Next
    Set Link = OurShell.CreateShortcut(LinkPath)
    If Err.Number <> 0 Then
        ErrorMessage = Err.Description
        On Error GoTo 0
        Fail "The startup shortcut could not be inspected: " & ErrorMessage
    End If
    On Error GoTo 0
    If Link.Description <> ShortcutDescription Then
        Fail "The shortcut has a different owner marker. It has not been removed: " & LinkPath
    End If
    On Error Resume Next
    OurFSO.DeleteFile LinkPath, False
    If Err.Number <> 0 Then
        ErrorMessage = Err.Description
        On Error GoTo 0
        Fail "The startup shortcut could not be removed: " & ErrorMessage
    End If
    On Error GoTo 0
    WScript.Echo "Autostart disabled." & vbCrLf & _
        "If the bot is running now, use STOP_BOT.bat to stop it."
End Sub
