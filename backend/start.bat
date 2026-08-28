@echo off
REM Backend only. For the whole system (frontend included) use ..\start.bat.
REM
REM Prefer the project venv over whatever "python" is on PATH: this file used
REM bare "python", which on a machine with a newer system interpreter runs the
REM app on a version the project has never been tested against.
cd /d "%~dp0"
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [env] No .venv found - falling back to "python" on PATH.
    set "PY=python"
)
"%PY%" run.py
