@echo off
chcp 936 >nul 2>&1
title PTSD - Install Dependencies
echo ========================================
echo   PTSD - Install Dependencies
echo ========================================
echo.

REM -- Python virtual environment --
echo [1/3] Creating Python virtual environment...
if exist ".venv\Scripts\python.exe" (
    echo       Already exists, skipping
) else (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Failed to create venv. Please install Python 3.8+
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo       Created successfully
)
echo.

REM -- Python dependencies --
echo [2/3] Installing Python backend dependencies...
.venv\Scripts\pip install -r backend\requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Backend dependency installation failed
    pause
    exit /b 1
)
echo       Backend dependencies installed
echo.

REM -- Node.js frontend dependencies --
echo [3/3] Installing frontend dependencies...
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [WARN] npm not found. Please install Node.js 16+
    echo        https://nodejs.org/
    echo        Then re-run this script.
    pause
    exit /b 1
)
cd frontend
call npm install --silent
if %errorlevel% neq 0 (
    echo [ERROR] Frontend dependency installation failed
    cd ..
    pause
    exit /b 1
)
cd ..
echo       Frontend dependencies installed
echo.

echo ========================================
echo   All dependencies installed!
echo ========================================
echo.
echo Next step: double-click start.bat to launch.
echo.
pause