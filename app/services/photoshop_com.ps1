param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'render')]
    [string]$Operation
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
$progId = 'Photoshop.Application'

function Write-Result($Result) {
    ConvertTo-Json -InputObject $Result -Compress
}

function Fail([int]$Code, [string]$Message) {
    Write-Result @{ status_code = $Code; detail = $Message }
    exit 1
}

function Get-NormalizedPath([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $buffer = [Text.StringBuilder]::new(32768)
    $length = [Mockup.WindowsPath]::GetLongPathNameW($fullPath, $buffer, $buffer.Capacity)
    if ($length -eq 0) {
        throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }
    return $buffer.ToString()
}

function Get-PhotoshopProcesses {
    foreach ($process in [Diagnostics.Process]::GetProcessesByName('Photoshop')) {
        try {
            if ($process.SessionId -eq $sessionId -and -not $process.HasExited) {
                $path = $process.MainModule.FileName
                if (-not $path) { throw 'Could not determine the Photoshop process path.' }
                [PSCustomObject]@{ Process = $process; Path = Get-NormalizedPath $path }
            }
        } catch {
            if (-not $process.HasExited) { throw }
        }
    }
}

function Get-RegisteredExecutable {
    $classKey = Get-Item -LiteralPath "Registry::HKEY_CLASSES_ROOT\$progId\CLSID" -ErrorAction SilentlyContinue
    if (-not $classKey) { return }
    $classId = $classKey.GetValue('')
    $serverKey = Get-Item -LiteralPath "Registry::HKEY_CLASSES_ROOT\CLSID\$classId\LocalServer32" -ErrorAction SilentlyContinue
    if (-not $serverKey) { return }
    $serverExe = $serverKey.GetValue('ServerExecutable')
    if ($serverExe) { return Get-NormalizedPath $serverExe }

    $commandLine = [string]$serverKey.GetValue('')
    if ($commandLine -match '^\s*"([^"]+\.exe)"' -or $commandLine -match '^\s*(.+?\.exe)(?:\s|$)') {
        return Get-NormalizedPath $Matches[1]
    }
}

try {
    if ($sessionId -eq 0) {
        Fail 503 'Run this service in the logged-in Windows user desktop session.'
    }

    Add-Type -Namespace Mockup -Name WindowsPath -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
public static extern uint GetLongPathNameW(string path, System.Text.StringBuilder buffer, uint capacity);
'@
    $expectedExe = Get-NormalizedPath $env:PHOTOSHOP_EXE

    $before = @(Get-PhotoshopProcesses)
    $target = @($before | Where-Object { $_.Path -eq $expectedExe })
    if ($Operation -eq 'stop' -and $target.Count -eq 0) {
        Write-Result @{ status = 'stopped'; changed = $false }
        exit 0
    }
    if ($before.Count -ne $target.Count -or $target.Count -gt 1) {
        Fail 409 'More than one Photoshop process or a different Photoshop executable is running in this session.'
    }

    $registeredExe = Get-RegisteredExecutable
    if (-not $registeredExe) {
        Fail 503 'Photoshop.Application COM is not registered. Check whether this Photoshop package supports COM registration.'
    }
    if ($registeredExe -ne $expectedExe) {
        Fail 409 'Photoshop.Application COM registration points to a different executable than PHOTOSHOP_EXE.'
    }

    $app = $null
    try {
        if ($Operation -eq 'stop') {
            $original = $target[0].Process
            try {
                $app = [Runtime.InteropServices.Marshal]::GetActiveObject($progId)
            } catch {
                # Photoshop can respond through COM without publishing an active object.
                if ($original.HasExited) {
                    Write-Result @{ status = 'stopped'; changed = $false }
                    exit 0
                }
                $app = New-Object -ComObject $progId
            }
        } else {
            $app = New-Object -ComObject $progId
        }

        $appFolder = [string]$app.DoJavaScript('String(app.path.fsName)')
        $appExe = Get-NormalizedPath ([IO.Path]::Combine($appFolder, 'Photoshop.exe'))
        if ($appExe -ne $expectedExe) {
            Fail 409 'The Photoshop COM object belongs to a different installation.'
        }
        $running = @(Get-PhotoshopProcesses)
        if ($running.Count -ne 1 -or $running[0].Path -ne $expectedExe) {
            Fail 503 'Could not verify the configured Photoshop process in this Windows desktop session.'
        }

        if ($Operation -eq 'start') {
            Write-Result @{
                status = 'running'; changed = ($target.Count -eq 0)
                pid = $running[0].Process.Id; version = [string]$app.Version
            }
            exit 0
        }

        if ($Operation -eq 'render') {
            $null = $app.DoJavaScriptFile($env:MOCKUP_JSX_PATH)
            Write-Result @{ rendered = $true }
            exit 0
        }

        if ($original.HasExited -or $running[0].Process.Id -ne $original.Id) {
            Fail 409 'The Photoshop process changed during the request. Retry the request.'
        }

        $quitScript = @'
(function () {
    for (var i = 0; i < app.documents.length; i++) {
        if (!app.documents[i].saved) return 'unsaved';
    }
    executeAction(charIDToTypeID('quit'), undefined, DialogModes.ALL);
    return 'quit';
}());
'@
        $quitResult = $null
        $quitError = $null
        try {
            $quitResult = $app.DoJavaScript($quitScript)
        } catch {
            # Quitting can disconnect COM before DoJavaScript returns.
            $quitError = $_
        }
        if ($quitResult -eq 'unsaved') {
            Fail 409 'Photoshop has unsaved documents. Save or close them before stopping Photoshop.'
        }
    } finally {
        if ($null -ne $app) {
            try {
                $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($app)
            } catch [Runtime.InteropServices.InvalidComObjectException] {
                # Photoshop already disconnected during shutdown.
            }
        }
    }

    if (-not $original.WaitForExit(15000)) {
        if ($quitError) { throw $quitError }
        Fail 504 'Photoshop did not exit in time. Check its desktop for an open dialog.'
    }
    Write-Result @{ status = 'stopped'; changed = $true }
    exit 0
} catch {
    $hresult = '0x{0:X8}' -f $_.Exception.GetBaseException().HResult
    Fail 503 "Photoshop COM operation failed ($hresult). Check the COM registration, Windows session, and Photoshop dialogs."
}
