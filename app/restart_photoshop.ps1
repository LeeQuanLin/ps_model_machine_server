$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$desktopSession = [Diagnostics.Process]::GetCurrentProcess().SessionId
function Get-DesktopPhotoshop {
    [Diagnostics.Process]::GetProcessesByName('Photoshop') |
        Where-Object { $_.SessionId -eq $desktopSession -and !$_.HasExited }
}

$original = @(Get-DesktopPhotoshop)
if ($original.Count -gt 1) { throw 'Multiple Photoshop instances in this desktop session.' }
if ($original.Count -eq 1) {
    $old = $original[0]
    $app = New-Object -ComObject Photoshop.Application
    try {
        $current = @(Get-DesktopPhotoshop)
        if ($current.Count -ne 1 -or $current[0].Id -ne $old.Id) {
            throw 'Photoshop instance changed before restart.'
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
        try { $quitResult = $app.DoJavaScript($quitScript) }
        catch {
            # Quit may disconnect COM before it can return the result.
            if (!$old.WaitForExit(15000)) { throw }
        }
        if ($quitResult -eq 'unsaved') { throw 'Save or close unsaved Photoshop documents to resume.' }
    } finally {
        try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
        catch [Runtime.InteropServices.InvalidComObjectException] {}
    }
    if (!$old.WaitForExit(20000)) { throw 'Photoshop did not exit before the timeout.' }
}

$newApp = New-Object -ComObject Photoshop.Application
try {
    $version = [string]$newApp.Version
    $running = @(Get-DesktopPhotoshop)
    if (!$version -or $running.Count -ne 1) { throw 'Could not verify the new Photoshop instance.' }
    Write-Output "Photoshop $version (PID $($running[0].Id))"
} finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($newApp)
}
