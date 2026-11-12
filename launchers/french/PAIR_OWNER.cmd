@echo off
call "%~dp0START_BOT.cmd" --pair-owner %*
exit /b %ERRORLEVEL%
