@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  Prediction Market Reality Filter - whole-system launcher
REM
REM    start.bat          Production (default): install deps, make sure
REM                       backend\.env exists (prompt for the API key the
REM                       first time), build the frontend if needed, free
REM                       ports 8000 and 3000, run the backend + static
REM                       frontend, and open the browser.
REM                         http://localhost:3000           (frontend)
REM                         http://localhost:8000           (backend API)
REM                         http://localhost:8000/docs      (API docs)
REM    start.bat build    Same, but force-rebuild the frontend first.
REM    start.bat dev      Development: backend :8000 + Next dev :3000 in
REM                       two windows (hot reload), browser opens :3000.
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

REM --- make sure backend\.env exists and the API key is filled in ------------
call :ensure_env || goto :fail

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
    echo       build requested - freeing ports that may lock frontend\out...
    call :killport 3000 || goto :fail
    call :killport 8000 || goto :fail
    pushd "%FRONTEND%" & set "NEXT_PUBLIC_API_BASE=http://localhost:8000/api" & call npm run build || (popd & goto :fail)
    popd
)

echo [4/4] Starting backend (:8000) + frontend (:3000) ...
call :killport 8000 || goto :fail
call :killport 3000 || goto :fail
echo.
echo   Frontend      : http://localhost:3000
echo   Backend API   : http://localhost:8000
echo   API docs      : http://localhost:8000/docs
echo.
REM Keep the child windows open on startup errors so the traceback is visible.
REM Use cmd /c rather than cmd /k so successful long-running servers do not
REM leave an extra shell behind after they are stopped.
start "PMRF backend :8000" /D "%BACKEND%" cmd /c "set BACKEND_SERVE_FRONTEND=false&& python run.py || (echo. & echo [FAILED] Backend exited with error. & pause)"
start "PMRF frontend :3000" /D "%ROOT%" cmd /c "python -m http.server 3000 --directory frontend\out --bind localhost || (echo. & echo [FAILED] Frontend static server exited with error. & pause)"
REM open the browser once both servers are up
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 6; Start-Process 'http://localhost:3000'"
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
echo [dev] Freeing ports and launching backend (:8000) + Next dev (:3000)...
call :killport 8000 || goto :fail
call :killport 3000 || goto :fail
echo.
echo   Frontend (dev) : http://localhost:3000
echo   Backend / API  : http://localhost:8000
echo.
REM Keep dev launches direct for the same reason: no persistent cmd /k shell.
set "SERVER_RELOAD=true"
start "PMRF backend :8000" /D "%BACKEND%" cmd /c "set BACKEND_SERVE_FRONTEND=false&& python run.py || (echo. & echo [FAILED] Backend exited with error. & pause)"
set "SERVER_RELOAD="
start "PMRF frontend :3000" /D "%FRONTEND%" cmd /c "npm.cmd run dev || (echo. & echo [FAILED] Frontend dev server exited with error. & pause)"
REM Next dev needs a few seconds to compile before the page is ready
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 8; Start-Process 'http://localhost:3000'"
goto :eof

REM ===================== helpers =============================================
:ensure_env
if not exist "%BACKEND%\.env" (
    echo [env] Creating backend\.env from .env.example ...
    copy /y "%BACKEND%\.env.example" "%BACKEND%\.env" >nul || (echo [ERROR] could not create backend\.env & exit /b 1)
)
findstr /c:"sk-your-key-here" "%BACKEND%\.env" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   [!] backend\.env still has the placeholder OPENAI_API_KEY.
    echo       Notepad will open now - set your real key, SAVE, then CLOSE
    echo       Notepad to continue startup.
    echo.
    notepad "%BACKEND%\.env"
)
exit /b 0

:killport
REM Kill any process currently LISTENING on the given TCP port (and its tree),
REM so a stale server can never collide with the one we are about to start.
set "PORT=%~1"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":!PORT! " ^| findstr "LISTENING"') do (
    echo       port !PORT! busy - stopping old process PID %%P ...
    taskkill /F /T /PID %%P >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not stop process PID %%P on port !PORT!.
        echo         Close that process manually or run this launcher as Administrator.
        exit /b 1
    )
)
for /l %%I in (1,1,10) do (
    netstat -ano | findstr ":!PORT! " | findstr "LISTENING" >nul 2>&1
    if errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
echo [ERROR] Port !PORT! is still busy after stop attempt.
exit /b 1
exit /b 0

:fail
echo.
echo [FAILED] Startup aborted. See the error above.
if not "%PMRF_NO_PAUSE_ON_FAIL%"=="1" pause
exit /b 1
