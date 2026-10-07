# Desktop shortcuts: the Slow Roads Ride window, your ride book and report.bat. Run once (setup.ps1 also runs it):
#   powershell -ExecutionPolicy Bypass -File scripts\shortcuts.ps1
# Safe to run again: existing shortcuts with the same names are updated in place.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")   # follows OneDrive-redirected desktops
$shell = New-Object -ComObject WScript.Shell
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$rideIcon = Join-Path $root "assets\ride.ico"

$items = @(
    @{ Name = "Slow Roads Ride";   Target = $pythonw; Args = "-m bridge.app";        Icon = "$rideIcon,0";
       Desc = "Ride Slow Roads with the KICKR CORE" },
    @{ Name = "Slow Roads Rides";  Target = $pythonw; Args = "-m bridge.app --book"; Icon = "$rideIcon,0";
       Desc = "Your ride book: latest ride, records and charts" },
    @{ Name = "Slow Roads Report"; Target = (Join-Path $root "report.bat"); Args = "";
       Icon = (Join-Path $env:SystemRoot "System32\imageres.dll,15"); Desc = "Bundle logs to send to the developer" }
)
foreach ($i in $items) {
    if (-not (Test-Path $i.Target)) { Write-Warning "missing $($i.Target)"; continue }
    $lnk = $shell.CreateShortcut((Join-Path $desktop ($i.Name + ".lnk")))
    $lnk.TargetPath = $i.Target
    $lnk.Arguments = $i.Args
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $i.Icon
    $lnk.Description = $i.Desc
    $lnk.Save()
    Write-Host "shortcut: $($i.Name) -> $($i.Target) $($i.Args)"
}
