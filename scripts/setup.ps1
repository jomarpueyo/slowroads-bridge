# One-time initialization for a fresh Windows PC.
# Installs Python 3.13, Git and the ViGEmBus 1.22.0 driver with winget, then creates
# .venv, installs dependencies and runs the test suite. Safe to re-run: each step
# checks what is already there. Writes a transcript to logs\setup-*.log.
#
#   Get-ChildItem -Recurse | Unblock-File
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Start-Transcript -Path "logs\setup-$stamp.log" | Out-Null

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Ensure-Winget($id, $label) {
    $listed = winget list --id $id --exact --accept-source-agreements 2>$null | Out-String
    if ($listed -match [regex]::Escape($id)) {
        Write-Host "$label already installed"
        return
    }
    Write-Host "installing $label ($id)..."
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget install $id failed (exit $LASTEXITCODE)" }
}

try {
    Step "Checking winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    }

    Step "Python 3.13"
    Ensure-Winget "Python.Python.3.13" "Python 3.13"
    Step "Git"
    Ensure-Winget "Git.Git" "Git"
    Step "ViGEmBus 1.22.0 driver (needs admin approval)"
    Ensure-Winget "ViGEm.ViGEmBus" "ViGEmBus"
    Refresh-Path

    Step "Locating Python"
    $py = $null
    foreach ($cand in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                        "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path $cand) { $py = $cand; break }
    }
    if (-not $py -and (Get-Command py -ErrorAction SilentlyContinue)) { $py = "py -3.13" }
    if (-not $py) { throw "Python 3.13 installed but not found; open a new terminal and re-run." }
    Write-Host "using $py"

    Step "Virtual environment (.venv)"
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Invoke-Expression "& $py -m venv .venv"
    }
    $venvPy = Join-Path $root ".venv\Scripts\python.exe"
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    & $venvPy -m pip install -r requirements-calibration.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install (calibration extras) failed" }

    Step "Git repository"
    if (-not (Test-Path ".git") -and (Get-Command git -ErrorAction SilentlyContinue)) {
        git init --quiet
        Write-Host "initialized git repo"
    }

    Step "Environment check"
    & $venvPy tools\check_env.py

    Step "Test suite"
    & $venvPy -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }

    Step "Done"
    if (Test-Path "settings.json") {
        Write-Host "Ready. Start a ride with ride.bat (see QUICKSTART.md)."
    } else {
        Write-Host "Next: calibrate once with calibrate.bat, then ride with ride.bat (see QUICKSTART.md)."
    }
}
finally {
    Stop-Transcript | Out-Null
}
