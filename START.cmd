@echo off
setlocal
"%~dp0.venvs\core\Scripts\python.exe" -m bot_center.launcher --root "%~dp0."
if errorlevel 1 pause
