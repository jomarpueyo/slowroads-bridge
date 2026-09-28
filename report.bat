@echo off
rem Bundle recent crash reports and logs (personal details removed) into one zip to send to the developer.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m bridge.report %*
echo.
pause
