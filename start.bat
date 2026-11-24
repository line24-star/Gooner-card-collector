@echo off
rem Starts the app and opens it in your browser.
rem The server stops when you click "Stop server" or close every browser tab.
setlocal
cd /d "%~dp0"
title Media Organizer

if not exist "backend\.venv\Scripts\python.exe" call setup.bat --quiet || goto :fail
if not exist "frontend\node_modules"            call setup.bat --quiet || goto :fail

cd backend
".venv\Scripts\python.exe" -m app.launcher %*
exit /b %errorlevel%

:fail
echo [!] Could not start. Try running setup.bat.
pause
exit /b 1
