@echo off
REM Opens the Jarvis Control Center against the real production backend.
REM
REM Milestone 9B.10: this previously launched an isolated, test-mode
REM instance on port 8010 (its own OpenCode port, its own throwaway
REM database) -- that was correct for reviewing the Control Center
REM before it was merged. Now that it is merged into develop and IS the
REM real dashboard, that isolated setup is obsolete: it would show a
REM fake, empty system instead of the real one. This now starts (or
REM attaches to) the actual production backend on its real port (8443,
REM HTTPS, the real certs) and opens the real dashboard. OpenCode is
REM never started separately here -- app/main.py's own startup hook
REM already starts/attaches to it (README.md SS5); this script does not
REM duplicate that.

setlocal

set JARVIS_VENV=C:\Users\Admin\AppData\Local\hermes\hermes-agent\venv
set JARVIS_PORT=8443
set JARVIS_URL=https://127.0.0.1:%JARVIS_PORT%/dashboard

cd /d "%~dp0"

REM If something is already listening on the production port, assume it's
REM the real backend already running (the common case day-to-day) and
REM just open the dashboard -- starting a second instance would fail to
REM bind the port anyway, and silently doing nothing is worse than saying
REM why.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %JARVIS_PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %ERRORLEVEL%==0 (
    echo Jarvis is already running on port %JARVIS_PORT% -- opening the dashboard.
    start "%JARVIS_URL%"
    goto end
)

if not exist "%JARVIS_VENV%\Scripts\uvicorn.exe" (
    echo Could not find the Jarvis virtual environment at:
    echo   %JARVIS_VENV%
    echo Edit JARVIS_VENV at the top of this file if it has moved, or see
    echo README.md SS5 to create one.
    goto end
)

if not exist "certs\jarvis-lan-key.pem" (
    echo Missing certs\jarvis-lan-key.pem / jarvis-lan-cert.pem.
    echo See README.md SS5 to generate a local HTTPS certificate first.
    goto end
)

echo Starting Jarvis on port %JARVIS_PORT%...
start "Jarvis" cmd /k ""%JARVIS_VENV%\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port %JARVIS_PORT% --ssl-keyfile certs\jarvis-lan-key.pem --ssl-certfile certs\jarvis-lan-cert.pem"

echo Waiting for it to become healthy...
:waitloop
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %JARVIS_PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not %ERRORLEVEL%==0 goto waitloop

echo Opening the dashboard...
start "%JARVIS_URL%"

:end
endlocal
