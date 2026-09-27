@echo off
rem Re-measure the car and update settings.json. Slow Roads in front, gearbox Automatic, car stopped.
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\calibrate_car.py %*
pause
