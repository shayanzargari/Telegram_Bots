@echo off
setlocal EnableExtensions DisableDelayedExpansion
"%~dp0..\..\.venvs\core\Scripts\python.exe" -m bot_center --root "%~dp0..\.." stop --bot home
if errorlevel 1 exit /b %ERRORLEVEL%
rem Also cooperatively stop an explicitly launched standalone Home instance.
> "%~dp0..\..\data\home\stop.request" echo stop
