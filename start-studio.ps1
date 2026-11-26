# Run from the signed-in Windows desktop that owns the Photoshop session.
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Install the project requirements in .venv first.' }

# Serialize simultaneous launches from this desktop.
$mutexName = "Local\PhotoshopMockupStudio-$Port"
$mutex = [Threading.Mutex]::new($false, $mutexName)
$locked = $false
Push-Location -LiteralPath $PSScriptRoot
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (!$locked) { throw 'Another startup check is in progress.' }

    $probe = @'
import http.client
import json
import sys
from urllib.parse import urlsplit

from redis import Redis
from app.config import BROKER, TOKEN
from app.main import app

if not TOKEN.strip():
    sys.exit("Set PHOTOSHOP_API_TOKEN in .env.")
try:
    with Redis.from_url(BROKER, socket_connect_timeout=3, socket_timeout=3) as redis:
        redis.ping()
except Exception as exc:
    endpoint = urlsplit(BROKER)
    sys.exit(f"Redis PING failed at {endpoint.hostname}:{endpoint.port or 6379} ({type(exc).__name__}). Check CELERY_BROKER_URL and connectivity.")

conn = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=4)
try:
    conn.request("GET", "/openapi.json")
    schema = conn.getresponse()
    if schema.status != 200 or json.load(schema).get("info", {}).get("title") != app.title:
        sys.exit("Another service is using the API port.")
    conn.request("GET", "/api/templates", headers={"Authorization": "Bearer " + TOKEN})
    response = conn.getresponse()
    if response.status != 200 or not isinstance(json.load(response).get("items"), list):
        sys.exit("The API port is in use. Check the running service and PHOTOSHOP_API_TOKEN.")
except ConnectionRefusedError:
    print("start-api")
else:
    print("reuse-api")
finally:
    conn.close()
'@
    $apiState = $probe | & $python - $Port
    if ($LASTEXITCODE -ne 0) { throw 'Startup checks failed.' }
    $psApp = New-Object -ComObject Photoshop.Application
    Write-Host "Photoshop $($psApp.Version) / Redis PONG"
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($psApp)

    $logDir = Join-Path $PSScriptRoot 'data\studio\logs'
    [void][IO.Directory]::CreateDirectory($logDir)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    function Start-StudioProcess([string]$role, [string[]]$arguments) {
        Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -PassThru `
            -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$role-$stamp.out.log") `
            -RedirectStandardError (Join-Path $logDir "$role-$stamp.err.log")
    }

    if ($apiState -eq 'start-api') {
        $api = Start-StudioProcess 'api' @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port")
        $ready = $false
        for ($i = 0; $i -lt 25; $i++) {
            if ($api.HasExited) { throw "API exited. See $logDir\api-$stamp.err.log" }
            try {
                $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 2
                if ($page.StatusCode -eq 200) { $ready = $true; break }
            } catch { Start-Sleep -Milliseconds 400 }
        }
        if (!$ready) { throw "API startup not confirmed. See $logDir\api-$stamp.err.log" }
    }
    Write-Host "API available at http://127.0.0.1:$Port/"

    $sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $binDir = Split-Path -Parent $python
    $workers = @(Get-CimInstance Win32_Process -Filter "SessionId=$sessionId AND (Name='python.exe' OR Name='celery.exe')" |
        Where-Object {
            $_.ExecutablePath -and (Split-Path -Parent $_.ExecutablePath) -eq $binDir -and
            $_.CommandLine -match '\bcelery\b' -and $_.CommandLine -match '\bapp\.worker:celery\b' -and
            $_.CommandLine -match '(?:^|\s)worker(?:\s|$)' -and
            ($_.CommandLine -notmatch '(?:-Q\s*|--queues[=\s]+)([^\s]+)' -or
                ($Matches[1].Trim('"') -split ',') -contains 'photoshop.mockups')
        })
    if ($workers) {
        Write-Host "Existing desktop worker detected (PID $($workers[0].ProcessId)). See the queue for progress."
    } else {
        $worker = Start-StudioProcess 'worker' @('-m','celery','-A','app.worker:celery','worker','--pool=solo',
            '--concurrency=1','-Q','photoshop.mockups','--hostname=mockup@%h','-l','INFO')
        $workerLog = Join-Path $logDir "worker-$stamp.err.log"
        $ready = $false
        for ($i = 0; $i -lt 60; $i++) {
            if ($worker.HasExited) { throw "Worker exited. See $workerLog" }
            if (Select-String -LiteralPath $workerLog -Pattern ' ready\.' -Quiet -ErrorAction SilentlyContinue) {
                $ready = $true; break
            }
            Start-Sleep -Milliseconds 500
        }
        if (!$ready) { throw "Worker started (PID $($worker.Id)), readiness not yet confirmed. See $workerLog" }
        Write-Host "Celery worker ready (PID $($worker.Id))."
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    Pop-Location
}
