# ============================================================
# HongLouZhiWen - Frontend launcher (Windows side)
# ============================================================
# Why the frontend must run on the WINDOWS side:
#   WSL2 localhost forwarding is one-way (Windows -> WSL).
#   A node process started inside WSL is NOT reachable from the
#   Windows browser at 127.0.0.1:5173.
#
# Why the process must be fully detached:
#   Start-Process children stay attached to the parent. When the
#   parent shell exits or is killed, the frontend dies with it.
#   schtasks launches via Task Scheduler, so the process survives.
#
# CRITICAL - no spaces around && in a set chain:
#   `set VAR=value && next` puts the space BEFORE && into VAR.
#   That produced HLM_BACKEND="http://127.0.0.1:8000 " and every
#   proxy call failed with "Failed to parse URL ...".
#   Always write `set VAR=value&& next`.
# ============================================================
param([int]$Port = 5173)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = Join-Path $Root 'web'

if (-not (Test-Path (Join-Path $WebDir 'server.js'))) {
    Write-Host '[web] ERROR: web\server.js not found' -ForegroundColor Red
    exit 1
}

Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*server.js*' } |
    ForEach-Object {
        Write-Host "[web] stopping old pid=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Milliseconds 600

Write-Host "[web] starting on http://127.0.0.1:$Port"

$inner = 'cmd /c cd /d "' + $WebDir + '"&& set HLM_WEB_PORT=' + $Port + '&& set HLM_BACKEND=http://127.0.0.1:8000&& node server.js > _srv.log 2> _srv.err'

$taskName = 'HLM_Web_Launcher'
& schtasks /create /tn $taskName /tr $inner /sc once /st 00:00 /f 2>&1 | Out-Null
& schtasks /run /tn $taskName 2>&1 | Out-Null
Start-Sleep -Milliseconds 900
& schtasks /delete /tn $taskName /f 2>&1 | Out-Null

$ready = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 1000
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Host "[web] ready on http://127.0.0.1:$Port" -ForegroundColor Green
    exit 0
}

Write-Host '[web] ERROR: not ready after 25s' -ForegroundColor Red
$errFile = Join-Path $WebDir '_srv.err'
if (Test-Path $errFile) {
    $tail = Get-Content $errFile -Tail 8 -ErrorAction SilentlyContinue
    if ($tail) { Write-Host '--- _srv.err ---'; $tail | ForEach-Object { Write-Host $_ } }
}
exit 1