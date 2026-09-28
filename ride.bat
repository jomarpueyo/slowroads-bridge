@echo off
rem Start a ride: the bridge (pedals -> car) plus the recorder (speedometer, screenshots, game
rem settings, for tuning afterwards). Double-click, or run from any folder.
rem Extra options pass to the bridge, e.g.  ride.bat --gear 2.5   or   ride.bat --dry-run
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
if exist "logs\active-ride.txt" del "logs\active-ride.txt"
start "slowroads recorder" /min ".venv\Scripts\python.exe" tools\ride_recorder.py
".venv\Scripts\python.exe" -m bridge %*
echo.
echo Ride finished. Logs are in %~dp0logs  (ride-, drive-, bridge-, speed-, shots-)
echo Something went wrong? Double-click report.bat and attach the zip it makes to a GitHub issue.
pause
