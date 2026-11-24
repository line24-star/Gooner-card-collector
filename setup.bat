@echo off
setlocal
cd /d "%~dp0"
title Media Organizer - Setup

where python >nul 2>&1 || (echo [!] Python not found. Install Python 3.12+ from python.org & pause & exit /b 1)
where npm    >nul 2>&1 || (echo [!] Node.js not found. Install Node 20+ from nodejs.org  & pause & exit /b 1)

if not exist "backend\.venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv backend\.venv || goto :fail
)

echo Installing backend dependencies...
"backend\.venv\Scripts\python.exe" -m pip install -q --upgrade pip || goto :fail
"backend\.venv\Scripts\python.exe" -m pip install -q -e "backend[dev]" || goto :fail

echo Installing frontend dependencies...
pushd frontend
call npm install || (popd & goto :fail)
popd

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Setup complete. Run start.bat to launch the app.
if "%~1"=="" pause
exit /b 0

:fail
echo.
echo [!] Setup failed - see errors above.
if "%~1"=="" pause
exit /b 1
