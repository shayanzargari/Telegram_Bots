@echo off
call "%~dp0START_BOT.cmd" --prepare-audio %*
exit /b %ERRORLEVEL%
