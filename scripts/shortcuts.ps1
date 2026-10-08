# Desktop shortcut for the Slow Roads Ride window (ride, ride book and problem reports in one). Run once
# (setup.ps1 also runs it):
#   powershell -ExecutionPolicy Bypass -File scripts\shortcuts.ps1
# Safe to run again: the shortcut is updated in place, and the older separate Rides and Report shortcuts are
# removed (the window has tabs for both; report.bat still works if the window can't start).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")   # follows OneDrive-redirected desktops
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { Write-Warning "missing $pythonw (run setup.ps1 first)"; exit 1 }

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Slow Roads Ride.lnk"))
$lnk.TargetPath = $pythonw
$lnk.Arguments = "-m bridge.app"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = (Join-Path $root "assets\ride.ico") + ",0"
$lnk.Description = "Ride Slow Roads with the KICKR CORE: ride, ride book and problem reports"
$lnk.Save()
Write-Host "shortcut: Slow Roads Ride -> $pythonw -m bridge.app"

foreach ($old in "Slow Roads Rides.lnk", "Slow Roads Report.lnk") {
    $path = Join-Path $desktop $old
    if (Test-Path $path) {
        $target = $shell.CreateShortcut($path).TargetPath
        if ($target -like "$root*") { Remove-Item $path; Write-Host "removed old shortcut: $old" }
    }
}
