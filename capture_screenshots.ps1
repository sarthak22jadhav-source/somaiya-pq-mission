# ---------------------------------------------------------------------------
# Locates a Chromium-based browser and captures screenshots of the dashboard.
# Usage:  powershell -ExecutionPolicy Bypass -File capture_screenshots.ps1
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$candidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
$browser = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $browser = $c; break }
}
if (-not $browser) {
    Write-Host "No Chromium browser found; skipping screenshots."
    exit 0
}
Write-Host "Using browser: $browser"

$pages = @(
    @{ Name = "dashboard"; Url = "http://127.0.0.1:5000/" },
    @{ Name = "payloads";  Url = "http://127.0.0.1:5000/payloads" },
    @{ Name = "telemetry";  Url = "http://127.0.0.1:5000/telemetry" }
)

foreach ($p in $pages) {
    $out = Join-Path $root ("static\_shot_" + $p.Name + ".png")
    & $browser --headless=new --disable-gpu --hide-scrollbars `
        --window-size=1440,2400 --virtual-time-budget=6000 `
        --screenshot="$out" $p.Url 2>$null | Out-Null
    if (Test-Path $out) {
        $kb = [math]::Round((Get-Item $out).Length / 1KB, 1)
        Write-Host ("Captured {0,-12} -> {1} ({2} KiB)" -f $p.Name, $out, $kb)
    } else {
        Write-Host ("FAILED to capture {0}" -f $p.Name)
    }
}
exit 0
