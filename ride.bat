@echo off
rem Start a ride in the Slow Roads Ride window (no console). The desktop shortcut does the same.
rem Options pass through, e.g.  ride.bat --gear 2.5   or   ride.bat --workout endurance
rem The old console version (status line, keyboard menu): ride-console.bat
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m bridge.app %*
