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
from app.config import BROKER, QUEUE, TOKEN
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
    catalog = json.load(response) if response.status == 200 else {}
    if not isinstance(catalog.get("items"), list):
        sys.exit("The API port is in use. Check the running service and PHOTOSHOP_API_TOKEN.")
except ConnectionRefusedError:
    print("start-api")
else:
    print("reuse-api" if catalog.get("queue") == QUEUE else "upgrade-api")
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

    $sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $queue = & $python -c 'from app.config import QUEUE; print(QUEUE)'
    if ($LASTEXITCODE -ne 0 -or !$queue) { throw 'Cannot determine the project queue.' }
    $queue = $queue.Trim()

    function Get-ProjectWorkerProcesses {
        @(Get-CimInstance Win32_Process -Filter "SessionId=$sessionId AND Name='python.exe'" |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine -match [regex]::Escape($PSScriptRoot) -and
                $_.CommandLine -match '\bcelery\b' -and
                $_.CommandLine -match '\bapp\.worker:celery\b' -and
                $_.CommandLine -match '(?:^|\s)worker(?:\s|$)'
            })
    }

    function Get-WorkerNode([string]$commandLine) {
        $match = [regex]::Match($commandLine, '--hostname(?:=|\s+)"?([^\s"]+)"?')
        $name = if ($match.Success) { $match.Groups[1].Value.Trim('"') } else { 'celery@%h' }
        $name = $name -replace '%h', $env:COMPUTERNAME -replace '%n', $env:COMPUTERNAME
        return $name
    }

    function Get-DeclaredQueues([string]$commandLine) {
        $match = [regex]::Match($commandLine, '(?:-Q|--queues)(?:\s+|=)"?([^\s"]+)"?')
        if (!$match.Success) { return @() }
        return @($match.Groups[1].Value.Trim('"') -split ',')
    }

    function Get-LocalWorkerGroups {
        $groups = @{}
        foreach ($process in @(Get-ProjectWorkerProcesses)) {
            $node = Get-WorkerNode $process.CommandLine
            if (!$groups.ContainsKey($node)) {
                $groups[$node] = [pscustomobject]@{
                    Node = $node
                    Declared = @(Get-DeclaredQueues $process.CommandLine)
                    Processes = New-Object System.Collections.Generic.List[int]
                    CommandLine = $process.CommandLine
                }
            }
            $groups[$node].Processes.Add($process.ProcessId)
        }
        @($groups.Values)
    }

    function Get-InspectedQueues {
        $script = @'
import json
from app.worker import celery

replies = celery.control.inspect(timeout=4).active_queues() or {}
print(json.dumps({name: [queue["name"] for queue in queues] for name, queues in replies.items()}))
'@
        $raw = $script | & $python -
        $result = @{}
        if ($LASTEXITCODE -ne 0 -or !$raw) { return $result }
        try {
            $parsed = $raw | ConvertFrom-Json
            foreach ($property in $parsed.PSObject.Properties) {
                $result[$property.Name] = @($property.Value)
            }
        } catch { }
        return $result
    }

    function Get-EffectiveQueues($worker, $inspected) {
        if ($worker.Declared.Count -gt 0) { return @($worker.Declared) }
        if ($inspected.ContainsKey($worker.Node)) { return @($inspected[$worker.Node]) }
        return @()
    }

    function Test-WorkerUsesQueue($worker, $inspected) {
        return (Get-EffectiveQueues $worker $inspected) -contains $queue
    }

    function Stop-StaleProjectWorkers {
        $workers = @(Get-LocalWorkerGroups)
        if (!$workers) { return }
        $inspected = Get-InspectedQueues
        $stale = @($workers | Where-Object { -not (Test-WorkerUsesQueue $_ $inspected) })
        if (!$stale) {
            Write-Host "Celery worker already consumes $queue."
            return
        }
        $nodes = @($stale | ForEach-Object { $_.Node } | Where-Object { $_ } | Select-Object -Unique)
        if (!$nodes) { throw 'Cannot determine the node name of the local Celery worker.' }
        $shutdown = @'
import sys

from app.worker import celery

names = [name for name in sys.argv[1:] if name]
if names:
    celery.control.shutdown(destination=names)
    print("shutdown sent to " + ", ".join(names))
'@
        $message = $shutdown | & $python - $nodes
        if ($LASTEXITCODE -ne 0) { throw "Cannot ask the old worker to stop: $($nodes -join ', ')" }
        Write-Host $message
        $pids = @($stale | ForEach-Object { $_.Processes | ForEach-Object { $_ } } | Sort-Object -Unique)
        $deadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $deadline) {
            $alive = @($pids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
            if (!$alive) { break }
            Start-Sleep -Seconds 1
        }
        $alive = @($pids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive) {
            $details = ($stale | ForEach-Object {
                "node $($_.Node), PID $(($_.Processes) -join ',')`n$($_.CommandLine)"
            }) -join "`n"
            throw "The old-queue worker is still running. Stop it after the current render finishes, then rerun this script:`n$details"
        }
        $remaining = @(Get-LocalWorkerGroups)
        if ($remaining) {
            Write-Host "Still running Celery workers: $(($remaining | ForEach-Object { $_.Node }) -join ', ')"
        }
    }

    if ($apiState -in @('reuse-api', 'upgrade-api')) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port)
        if ($apiState -eq 'upgrade-api' -or $listeners.LocalAddress -notcontains '0.0.0.0') {
            $local = @($listeners | Where-Object { $_.LocalAddress -in @('127.0.0.1','0.0.0.0') })
            if ($local.Count -ne 1) { throw "Cannot identify the API listener for port $Port." }
            $ownerId = $local[0].OwningProcess
            $oldApi = Get-Process -Id $ownerId
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
            $projectExecutables = @($python, (Join-Path (Split-Path -Parent $python) 'uvicorn.exe'))
            $uvicornCommand = '\buvicorn(?:\.exe)?"?\s+"?app\.main:app\b'
            $fromProject = $owner.ExecutablePath -in $projectExecutables
            if (!$fromProject -and $owner.ParentProcessId) {
                # A Windows venv launcher can delegate to the base Python executable.
                $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.ParentProcessId)"
                $fromProject = $parent.ExecutablePath -in $projectExecutables -and $parent.CommandLine -match $uvicornCommand
            }
            if (!$fromProject -or $owner.CommandLine -notmatch $uvicornCommand) {
                throw "Stop the API on port $Port (PID $ownerId) in its original terminal, then rerun this script."
            }
            Write-Host "Restarting project API (PID $ownerId) with the current queue and bind address."
            Stop-Process -InputObject $oldApi -ErrorAction Stop
            if (!$oldApi.WaitForExit(10000)) { throw "API PID $ownerId did not exit." }
            $apiState = 'start-api'
        }
    }

    # The old API is already stopped at this point (if it had to be upgraded).
    # Do not let a worker from the old queue keep stealing tasks while the new API starts.
    Stop-StaleProjectWorkers

    if ($apiState -eq 'start-api') {
        $api = Start-StudioProcess 'api' @('-m','uvicorn','app.main:app','--host','0.0.0.0','--port',"$Port")
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
    $bound = @(Get-NetTCPConnection -State Listen -LocalPort $Port)
    if ($bound.LocalAddress -notcontains '0.0.0.0') {
        throw "Expected 0.0.0.0:$Port. Actual listening addresses: $($bound.LocalAddress -join ', ')."
    }
    Write-Host "API listening on 0.0.0.0:$Port"
    Write-Host "Queue: $queue"
    $lanAddresses = Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
        Select-Object -ExpandProperty IPAddress -Unique
    foreach ($address in $lanAddresses) { Write-Host "LAN URL: http://${address}:$Port/" }

    $workers = @(Get-LocalWorkerGroups)
    if ($workers) {
        $inspected = Get-InspectedQueues
        $wrong = @($workers | Where-Object { -not (Test-WorkerUsesQueue $_ $inspected) })
        if ($wrong.Count) {
            throw "Local workers are not consuming ${queue}: $(($wrong | ForEach-Object { $_.Node }) -join ', ')"
        }
        $needsVerify = @($workers | Where-Object { $_.Declared.Count -eq 0 })
        if ($needsVerify.Count) {
            $verify = @'
import sys
from app.config import QUEUE
from app.worker import celery

replies = celery.control.inspect(timeout=4).active_queues() or {}
for name, queues in replies.items():
    if any(q["name"] == QUEUE for q in queues):
        print(f"Desktop worker {name} is consuming {QUEUE}.")
        break
else:
    sys.exit(1)
'@
            $verify | & $python -
            if ($LASTEXITCODE -ne 0) {
                throw "Worker PID $(($needsVerify | ForEach-Object { $_.Processes }) -join ',') has not confirmed queue $queue. A solo worker may be rendering; check the job log. If idle, restart that project worker and rerun this script."
            }
        }
        Write-Host "Reusing Celery worker on $queue."
    } else {
        $worker = Start-StudioProcess 'worker' @('-m','celery','-A','app.worker:celery','worker','--pool=solo',
            '--concurrency=1','-Q',$queue,"--hostname=$queue@%h",'-l','INFO')
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
        $verify = @'
import sys
from app.config import QUEUE
from app.worker import celery

replies = celery.control.inspect(timeout=4).active_queues() or {}
for name, queues in replies.items():
    if any(q["name"] == QUEUE for q in queues):
        print(f"Desktop worker {name} is consuming {QUEUE}.")
        break
else:
    sys.exit(1)
'@
        $verify | & $python -
        if ($LASTEXITCODE -ne 0) {
            throw "Worker PID $($worker.Id) has not confirmed queue $queue. See $workerLog"
        }
        Write-Host "Celery worker ready (PID $($worker.Id)) on $queue."
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    Pop-Location
}
