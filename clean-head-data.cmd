@echo off
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\clean-head-data.ps1"
exit /b %ERRORLEVEL%
