@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "BOT_CENTER_ROOT=%~dp0..\.."
"%~dp0..\..\.venvs\french-reader\Scripts\python.exe" -m french_notes --root "%BOT_CENTER_ROOT%" %*
exit /b %ERRORLEVEL%
