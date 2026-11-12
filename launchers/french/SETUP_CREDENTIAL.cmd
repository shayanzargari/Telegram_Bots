@echo off
"%~dp0..\..\.venvs\core\Scripts\python.exe" -m bot_center_runtime.credentials --root "%~dp0..\.." --bot french
exit /b %ERRORLEVEL%
