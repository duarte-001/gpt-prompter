<#
.SYNOPSIS
  Creates a desktop shortcut to StockAssistant.exe using the bundled icon.ico
  (same art as the app) so Windows does not fall back to a generic shell icon.

.PARAMETER ExePath
  Full path to StockAssistant.exe (default: dist layout under this repo).
#>
param(
    [string] $ExePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $ExePath) {
    $ExePath = Join-Path $repoRoot "dist\StockAssistant\StockAssistant.exe"
}
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "StockAssistant.exe not found: $ExePath`nBuild the app first (python build.py) or pass -ExePath."
}
$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
$installDir = Split-Path $ExePath -Parent
$iconIco = Join-Path $installDir "icon.ico"
if (-not (Test-Path -LiteralPath $iconIco)) {
    Write-Warning "icon.ico not found next to exe (rebuild with current build.py). Using embedded exe icon."
    $iconTarget = "$ExePath,0"
} else {
    $iconTarget = "$iconIco,0"
}

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Stock Assistant.lnk"
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $ExePath
$shortcut.WorkingDirectory = $installDir
$shortcut.IconLocation = $iconTarget
$shortcut.WindowStyle = 1
$shortcut.Description = "Stock Assistant"
$shortcut.Save()

Write-Host "Shortcut created: $lnkPath"
Write-Host "Icon: $iconTarget"
