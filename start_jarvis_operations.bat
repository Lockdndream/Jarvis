@echo off
REM ADR-022: starts the standalone Jarvis Operations console, a separate
REM process from the main Jarvis backend (see app/operations_console.py).
REM Binds 127.0.0.1 only -- a local-operator console, never reachable
REM from the LAN. Proxies OpenCode start/stop/restart/status to the real
REM Jarvis backend's Operations API; does not manage OpenCode directly.

setlocal

set JARVIS_VENV=C:\Users\Admin\AppData\Local\hermes\hermes-agent\venv
set OPERATIONS_PORT=8500
set OPERATIONS_URL=http://127.0.0.1:%OPERATIONS_PORT%/

cd /d "%~dp0"

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %OPERATIONS_PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %ERRORLEVEL%==0 (
    echo Jarvis Operations is already running on port %OPERATIONS_PORT% -- opening it.
    start "%OPERATIONS_URL%"
    goto end
)

if not exist "%JARVIS_VENV%\Scripts\python.exe" (
    echo Could not find the Jarvis virtual environment at:
    echo   %JARVIS_VENV%
    echo Edit JARVIS_VENV at the top of this file if it has moved, or see
    echo README.md SS5 to create one.
    goto end
)

echo Starting Jarvis Operations on port %OPERATIONS_PORT% (127.0.0.1 only)...
start "Jarvis Operations" cmd /k ""%JARVIS_VENV%\Scripts\python.exe" -m app.operations_console"

echo Waiting for it to become available...
:waitloop
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %OPERATIONS_PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not %ERRORLEVEL%==0 goto waitloop

echo Opening Jarvis Operations...
start "%OPERATIONS_URL%"

:end
endlocal
