@echo off
REM Opens the Jarvis Control Center dashboard in an isolated instance.
REM Runs on port 8010 with its own OpenCode port (4198) and its own test
REM database -- completely separate from the live demo backend (8443)
REM and live OpenCode server (4097). Safe to run alongside the demo.

cd /d "%~dp0"

set JARVIS_OPENCODE_PORT=4198
set JARVIS_TEST_MODE=1

echo Starting isolated Control Center server on port 8010...
start "Jarvis Control Center - server" cmd /k uvicorn app.main:app --port 8010

echo Waiting for server to start...
timeout /t 5 /nobreak >nul

echo Seeding demo data...
python scripts\dashboard_demo_seed.py --http-port 8010

echo Opening dashboard in your browser...
start http://127.0.0.1:8010/dashboard

echo Done. The server is running in the other window titled
echo "Jarvis Control Center - server" -- close that window to stop it.
pause
