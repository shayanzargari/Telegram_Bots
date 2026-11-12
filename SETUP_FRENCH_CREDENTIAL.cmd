@echo off
setlocal
"%~dp0.venvs\core\Scripts\python.exe" "%~dp0tools\installation\provision_french.py" --root "%~dp0."
pause
