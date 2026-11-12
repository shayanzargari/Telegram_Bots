@echo off
if exist "%~dp0..\..\data\home\exports\tasks-latest.html" (
  start "" "%~dp0..\..\data\home\exports\tasks-latest.html"
) else (
  echo No exported list is available. Inspect Home status in Bot Control Center.
  exit /b 2
)
