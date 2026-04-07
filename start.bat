@echo off
chcp 936 >nul 2>&1
title PTSD - Product Test Smart Dog [Latest]
echo ========================================
echo   PTSD - Product Test Smart Dog
echo   [Latest - 最新合并版]
echo ========================================
echo.

REM -- Check dependencies --
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python venv not found. Please run install.bat first.
    pause
    exit /b 1
)

REM -- Kill old processes on port 8000 and 3000 --
echo [1/3] Cleaning up...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTEN 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3000 ^| findstr LISTEN 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul
echo       Done
echo.

REM -- Start backend --
echo [2/3] Starting backend...
start "PTSD Backend" cmd /c "chcp 65001 >nul 2>&1 && cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
timeout /t 3 /nobreak >nul
echo       Backend:  http://localhost:8000
echo.

REM -- Start frontend --
echo [3/3] Starting frontend...
where npm >nul 2>&1
if %errorlevel% equ 0 (
    start "PTSD Frontend" cmd /c "cd /d %~dp0frontend && npm run dev"
    echo       Frontend: http://localhost:3000
) else (
    echo       [WARN] npm not found. Start frontend manually:
    echo              cd frontend ^& npm run dev
)
echo.

echo ========================================
echo   Startup complete!
echo ========================================
echo.
echo   Frontend:  http://localhost:3000
echo   Backend:   http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.
echo   First time? 
echo     1. Wait 10s for services to start
echo     2. Open http://localhost:3000
echo     3. Go to Model Config page, set Judge API key
echo     4. Start testing!
echo.
echo Press any key to close this window...
echo (Backend and frontend keep running)
pause >nul