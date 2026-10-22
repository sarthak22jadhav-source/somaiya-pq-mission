# ---------------------------------------------------------------------------
# Starts the dashboard, captures screenshots, then stops the server.
# Usage:  powershell -ExecutionPolicy Bypass -File run_screenshots.ps1
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Starting Flask server..."
$server = Start-Process -FilePath "python" -ArgumentList "app.py" `
    -WorkingDirectory $root -PassThru `
    -RedirectStandardOutput "server.log" -RedirectStandardError "server.err.log"

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5000/health" `
            -UseBasicParsing -TimeoutSec 3
        $ready = $true
        break
    } catch { }
}

if (-not $ready) {
    Write-Host "Server did not become ready."
    Get-Content "server.err.log" -ErrorAction SilentlyContinue
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "Server is up (PID $($server.Id)). Capturing screenshots..."
& (Join-Path $root "capture_screenshots.ps1")

Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
Write-Host "Server stopped."
exit 0
