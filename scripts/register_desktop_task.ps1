[CmdletBinding()]
param(
    [string]$RenderUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [ipaddress]$BindAddress = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment not found: $python. Complete the setup in README.md first."
}

$action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $projectRoot `
    -Argument "-m uvicorn app.main:app --host $BindAddress --port $Port --workers 1"
$principal = New-ScheduledTaskPrincipal -UserId $RenderUser -LogonType Interactive
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $RenderUser
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName 'PhotoshopMockupServer' -TaskPath '\' -Action $action `
    -Principal $principal -Trigger $trigger -Settings $settings -Force
