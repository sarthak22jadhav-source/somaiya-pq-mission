# ---------------------------------------------------------------------------
# Starts the dashboard, runs the live HTTP checks, then stops the server.
# Usage:  powershell -ExecutionPolicy Bypass -File run_live_checks.ps1
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Starting Flask server..."

# Pre-flight: the app no longer falls back to SQLite, so make sure the
# Supabase connection string is present before starting the server.
if (-not $env:DATABASE_URL) {
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile |
            Where-Object { $_ -match "^\s*DATABASE_URL\s*=" } |
            Select-Object -First 1
        if ($line -and ($line -split "=", 2)[1].Trim()) {
            Write-Host "DATABASE_URL found in .env"
        } else {
            Write-Host "ERROR: DATABASE_URL is not set." -ForegroundColor Red
            Write-Host "  Copy .env.example to .env and paste your Supabase URI:" -ForegroundColor Red
            Write-Host "    DATABASE_URL=postgresql://postgres.<ref>:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require"
            Write-Host "  (percent-encode any '@' in the password as %40)" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "ERROR: DATABASE_URL is not set and no .env file exists." -ForegroundColor Red
        Write-Host "  Copy .env.example to .env and paste your Supabase URI." -ForegroundColor Red
        exit 1
    }
}

$server = Start-Process -FilePath "python" -ArgumentList "app.py" `
    -WorkingDirectory $root -PassThru `
    -RedirectStandardOutput "server.log" -RedirectStandardError "server.err.log"

# Wait for the port to accept connections.
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
    Write-Host "Server did not become ready. Logs:"
    Get-Content "server.err.log" -ErrorAction SilentlyContinue
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "Server is up (PID $($server.Id)). Running checks..."
python http_check.py
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Stopping server..."
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
exit $code
