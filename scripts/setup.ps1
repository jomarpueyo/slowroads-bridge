# One-time initialization for a fresh Windows PC.
# Installs Python 3.13, Git and the ViGEmBus 1.22.0 driver with winget, then creates
# .venv, installs dependencies and runs the test suite. Safe to re-run: each step
# checks what is already there. Writes a transcript to logs\setup-*.log.
#
#   Get-ChildItem -Recurse | Unblock-File
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
#
# Supply chain (docs/SECURITY.md finding 6):
#   - winget packages are pinned to the versions this project was tested with; winget verifies each
#     installer's SHA-256 from its manifest. -AllowLatest installs current versions instead.
#   - Python packages install from hash-locked files (pip --require-hashes): every package, including
#     dependencies, must match a recorded SHA-256. Regenerate with pip-compile --generate-hashes.

param([switch]$AllowLatest)

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

function Ensure-Winget($id, $label, $version) {
    $listed = winget list --id $id --exact --accept-source-agreements 2>$null | Out-String
    if ($listed -match [regex]::Escape($id)) {
        Write-Host "$label already installed"
        return
    }
    $wgArgs = @("install", "--id", $id, "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements")
    if (-not $AllowLatest) { $wgArgs += @("--version", $version) }
    Write-Host "installing $label ($id $(if ($AllowLatest) { 'latest' } else { $version }))..."
    & winget @wgArgs
    if ($LASTEXITCODE -ne 0) {
        throw "winget install $id failed (exit $LASTEXITCODE). If version $version is no longer offered, re-run with -AllowLatest."
    }
}

try {
    Step "Checking winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    }

    Step "Python 3.13"
    Ensure-Winget "Python.Python.3.13" "Python 3.13" "3.13.15"
    Step "Git"
    Ensure-Winget "Git.Git" "Git" "2.55.0.3"
    Step "ViGEmBus 1.22.0 driver (needs admin approval)"
    Ensure-Winget "ViGEm.ViGEmBus" "ViGEmBus" "1.22.0"
    Refresh-Path

    Step "Locating Python"
    # Executable and arguments kept separate and called directly (no Invoke-Expression).
    $pyExe = $null
    $pyArgs = @()
    foreach ($cand in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                        "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path $cand) { $pyExe = $cand; break }
    }
    if (-not $pyExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
        $pyExe = (Get-Command py).Source
        $pyArgs = @("-3.13")
    }
    if (-not $pyExe) { throw "Python 3.13 installed but not found; open a new terminal and re-run." }
    Write-Host "using $pyExe $pyArgs"

    Step "Virtual environment (.venv)"
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & $pyExe @pyArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "could not create .venv" }
    }
    $venvPy = Join-Path $root ".venv\Scripts\python.exe"
    & $venvPy -m pip install --require-hashes -r requirements.txt -r requirements-calibration.txt -r requirements-test.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (a hash mismatch means a package changed; do not bypass it)" }

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
    Write-Host "Ready. Start a ride with ride.bat (see QUICKSTART.md). The first ride pairs with your trainer."
}
finally {
    Stop-Transcript | Out-Null
}
