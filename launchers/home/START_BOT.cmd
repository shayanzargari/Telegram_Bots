@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "BOT_CENTER_ROOT=%~dp0..\.."
"%~dp0..\..\.venvs\home\Scripts\python.exe" -m home_bot --root "%BOT_CENTER_ROOT%" %*
exit /b %ERRORLEVEL%
