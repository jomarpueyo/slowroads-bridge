# Desktop shortcuts for ride.bat and report.bat. Run once (setup.ps1 also runs it):
#   powershell -ExecutionPolicy Bypass -File scripts\shortcuts.ps1
# Safe to run again: existing shortcuts with the same names are updated in place.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")   # follows OneDrive-redirected desktops
$shell = New-Object -ComObject WScript.Shell

$items = @(
    @{ Name = "Slow Roads Ride";   Target = "ride.bat";   Icon = "imageres.dll,237"; Desc = "Ride Slow Roads with the KICKR CORE" },
    @{ Name = "Slow Roads Report"; Target = "report.bat"; Icon = "imageres.dll,15"; Desc = "Bundle logs to send to the developer" }
)
foreach ($i in $items) {
    $target = Join-Path $root $i.Target
    if (-not (Test-Path $target)) { Write-Warning "missing $target"; continue }
    $lnk = $shell.CreateShortcut((Join-Path $desktop ($i.Name + ".lnk")))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = Join-Path $env:SystemRoot ("System32\" + $i.Icon)
    $lnk.Description = $i.Desc
    $lnk.Save()
    Write-Host "shortcut: $($i.Name) -> $target"
}
