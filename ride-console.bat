@echo off
rem Console version of ride.bat: status line and keyboard menu in this window, recorder in a second one.
rem Most rides use ride.bat (the Slow Roads Ride window). Extra options pass to the bridge.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
if exist "logs\active-ride.txt" del "logs\active-ride.txt"
start "slowroads recorder" /min ".venv\Scripts\python.exe" tools\ride_recorder.py
".venv\Scripts\python.exe" -m bridge --menu %*
echo.
echo Ride finished. Logs are in %~dp0logs  (ride-, drive-, bridge-, speed-, shots-)
echo Something went wrong? Double-click report.bat and attach the zip it makes to a GitHub issue.
pause
