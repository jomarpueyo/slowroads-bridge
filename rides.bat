@echo off
rem Your ride book in the Slow Roads Ride window: latest ride, records and charts (stays on this PC).
rem Text versions: .venv\Scripts\python -m bridge.ridebook list ^| show N ^| hide N ^| note N text
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m bridge.app --book
