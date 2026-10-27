$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $Py)) { throw 'Run install.ps1 first.' }
$env:TASK_CONTINUITY_ROOT = $Root
if (!$env:TASK_CONTINUITY_DATA) { $env:TASK_CONTINUITY_DATA = Join-Path $Root 'data' }
$Logs = Join-Path $env:TASK_CONTINUITY_DATA 'Logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
function Write-WatchdogEvent([string]$Detail, [int]$Failures) {
    # Diagnostics must not stop recovery when antivirus or a viewer locks a log.
    try {
        $log = Join-Path $Logs 'watchdog.log'
        if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -ge 1MB) {
            Move-Item -LiteralPath $log -Destination ($log + '.previous') -Force -ErrorAction Stop
        }
        Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) $Detail; retry $Failures." -ErrorAction Stop
    } catch {
        # Drop this diagnostic until the lock clears; keep the restart loop alive.
    }
}
$hash = [Security.Cryptography.SHA256]::Create()
$key = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())))).Replace('-', '').Substring(0, 16)
$hash.Dispose()
$mutex = [Threading.Mutex]::new($false, ('Local\TaskContinuityWatchdog-' + $key))
$owns = $false
try {
    try { $owns = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owns = $true }
    if (!$owns) { return }
    $failures = 0
    while ($true) {
        $started = Get-Date
        try {
            foreach ($name in @('supervisor-out.log', 'supervisor-error.log')) {
                $path = Join-Path $Logs $name
                if (Test-Path -LiteralPath $path) { Move-Item -LiteralPath $path -Destination ($path + '.previous') -Force }
            }
            # ArgumentList joins array elements on Windows; quote the script path.
            $child = Start-Process -FilePath $Py -ArgumentList ('"' + (Join-Path $Root 'app\agent.py') + '" --no-browser') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs 'supervisor-out.log') -RedirectStandardError (Join-Path $Logs 'supervisor-error.log') -PassThru
            $null = $child.Handle
            $child.WaitForExit()
            $code = $child.ExitCode
            $child.Dispose()
            if ($code -eq 0) { break }
            $detail = "Supervisor exited $code"
        } catch { $detail = $_.Exception.Message }
        if (((Get-Date) - $started).TotalMinutes -ge 5) { $failures = 0 }
        $failures++
        Write-WatchdogEvent $detail $failures
        Start-Sleep -Seconds ([Math]::Min(60, 5 * $failures))
    }
} finally {
    if ($owns) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
