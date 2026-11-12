@echo off
setlocal EnableExtensions DisableDelayedExpansion
"%~dp0..\..\.venvs\french-editor\Scripts\python.exe" -m french_notes.app --root "%~dp0..\.." %*
exit /b %ERRORLEVEL%
