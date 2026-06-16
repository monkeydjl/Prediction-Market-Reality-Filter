@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  Prediction Market Reality Filter - whole-system launcher
REM
REM    start.bat          Production: build frontend (if needed) +
REM                       run backend. Everything on one origin:
REM                         http://localhost:8000/app   (new UI)
REM                         http://localhost:8000/dashboard (classic)
REM                         http://localhost:8000/docs  (API docs)
REM    start.bat build    Same, but force-rebuild the frontend first.
REM    start.bat dev      Development: backend :8000 + Next dev :3000
REM                       in two windows (hot reload, API proxied).
REM
REM  %~dp0 = this script's folder, so paths never break if moved.
REM ============================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "MODE=%~1"

REM --- locate python / npm ---------------------------------------------------
where python >nul 2>&1 || (echo [ERROR] python not found on PATH. & goto :fail)
where npm >nul 2>&1   || (echo [ERROR] npm not found on PATH.    & goto :fail)

if /i "%MODE%"=="dev" goto :dev

REM ===================== PRODUCTION (default) ===============================
echo [1/4] Installing backend dependencies...
python -m pip install -q -r "%BACKEND%\requirements.txt" || goto :fail

echo [2/4] Checking frontend dependencies...
if not exist "%FRONTEND%\node_modules" (
    echo       node_modules missing - running npm install...
    pushd "%FRONTEND%" & call npm install || (popd & goto :fail)
    popd
)

echo [3/4] Building frontend...
if /i "%MODE%"=="build" (
    set "DO_BUILD=1"
) else if not exist "%FRONTEND%\out\index.html" (
    set "DO_BUILD=1"
) else (
    set "DO_BUILD=0"
    echo       out\ exists - skipping build. Use "start.bat build" to force a rebuild.
)
if "!DO_BUILD!"=="1" (
    pushd "%FRONTEND%" & call npm run build || (popd & goto :fail)
    popd
)

echo [4/4] Starting backend on http://localhost:8000 ...
echo.
echo   New dashboard : http://localhost:8000/app
echo   Classic UI    : http://localhost:8000/dashboard
echo   API docs      : http://localhost:8000/docs
echo.
pushd "%BACKEND%" & python run.py & popd
goto :eof

REM ===================== DEVELOPMENT =========================================
:dev
echo [dev] Installing backend dependencies...
python -m pip install -q -r "%BACKEND%\requirements.txt" || goto :fail
if not exist "%FRONTEND%\node_modules" (
    echo [dev] Installing frontend dependencies...
    pushd "%FRONTEND%" & call npm install || (popd & goto :fail)
    popd
)
echo [dev] Launching backend (:8000) and Next dev server (:3000) in two windows...
echo.
echo   Frontend (dev) : http://localhost:3000
echo   Backend / API  : http://localhost:8000
echo.
start "PMRF backend :8000" cmd /k "cd /d "%BACKEND%" && python run.py"
start "PMRF frontend :3000" cmd /k "cd /d "%FRONTEND%" && npm run dev"
goto :eof

:fail
echo.
echo [FAILED] Startup aborted. See the error above.
exit /b 1
