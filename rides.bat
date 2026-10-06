@echo off
rem Your ride book: scoreboard in this window, charts in the browser (logs\dashboard.html, stays on this PC).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m bridge.ridebook
echo.
".venv\Scripts\python.exe" -m bridge.dashboard
echo.
echo More: .venv\Scripts\python -m bridge.ridebook list ^| show N ^| hide N ^| note N text
echo       .venv\Scripts\python -m bridge.plan    (ride plan, monthly challenge)   .venv\Scripts\python -m bridge.sharecard    (picture of your last ride)
pause
