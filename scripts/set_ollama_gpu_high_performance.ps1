# Set Windows "Graphics performance preference" for Ollama to High performance (NVIDIA).
# Same effect as Settings -> Display -> Graphics -> ollama.exe -> High performance.
# Run as your normal user (no admin required for HKCU).

$ErrorActionPreference = "Stop"

$candidates = @(
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    "$env:LOCALAPPDATA\Programs\Ollama\Ollama.exe",
    "${env:ProgramFiles}\Ollama\ollama.exe",
    "${env:ProgramFiles(x86)}\Ollama\ollama.exe"
)

$ollamaPath = $null
foreach ($p in $candidates) {
    if (Test-Path -LiteralPath $p) {
        $ollamaPath = (Resolve-Path -LiteralPath $p).Path
        break
    }
}

if (-not $ollamaPath) {
    try {
        $cmd = Get-Command ollama -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
            $ollamaPath = (Resolve-Path -LiteralPath $cmd.Source).Path
        }
    } catch {}
}

if (-not $ollamaPath) {
    Write-Host "Could not find ollama.exe. Install Ollama or add it to PATH, then run this script again." -ForegroundColor Red
    exit 1
}

Write-Host "Found: $ollamaPath" -ForegroundColor Green

$keyPath = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
if (-not (Test-Path -LiteralPath $keyPath)) {
    New-Item -Path $keyPath -Force | Out-Null
}

# Value name = full path to exe; value = high-performance GPU (see Microsoft / community docs)
$regName = $ollamaPath
$regValue = "GpuPreference=2;"
New-ItemProperty -Path $keyPath -Name $regName -Value $regValue -PropertyType String -Force | Out-Null

Write-Host "Registry set: UserGpuPreferences -> $regName = $regValue" -ForegroundColor Green
Write-Host ""
Write-Host "Next: quit Ollama from the system tray (right-click -> Quit), then start Ollama again." -ForegroundColor Yellow
Write-Host "Then run a prompt and check Task Manager -> GPU while it generates." -ForegroundColor Yellow
