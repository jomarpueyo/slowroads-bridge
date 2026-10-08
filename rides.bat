@echo off
rem Opens the Slow Roads Ride window on the Rides tab (your latest ride, records and charts).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m bridge.app --book
