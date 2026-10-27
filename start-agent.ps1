param([switch]$Background)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $Py)) { throw 'Run install.ps1 first.' }
$env:TASK_CONTINUITY_ROOT = $Root
if (!$env:TASK_CONTINUITY_DATA) { $env:TASK_CONTINUITY_DATA = Join-Path $Root 'data' }
$env:PYTHONIOENCODING = 'utf-8'
$port = & $Py -c "import os,sys; sys.path.insert(0,os.path.join(os.environ['TASK_CONTINUITY_ROOT'],'app')); from common import ensure_data_layout,settings,standard_user; standard_user(); ensure_data_layout(); print(settings()['ui_port'])"
if ($LASTEXITCODE -ne 0) { throw 'Configuration/startup validation failed. See the error above.' }
$watch = Join-Path $Root 'watch-agent.ps1'
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$url = 'http://127.0.0.1:' + $port + '/'
function Test-OwnAgent {
    try {
        $health = Invoke-RestMethod ($url + 'api/health') -TimeoutSec 2
        return $health.app_id -eq 'task-continuity-agent' -and $health.source_root -eq $Root
    } catch { return $false }
}
if (Test-OwnAgent) {
    if (!$Background) { Start-Process $url }
    Write-Output "Agent is already ready at $url."
    return
}
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, [int]$port)
try { $probe.Start() }
catch { throw "Port $port is occupied by another service. Set a different ui_port in data\config.json, then relaunch." }
finally { $probe.Stop() }
$p = Start-Process -FilePath $shell -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $watch + '"') -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-OwnAgent) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (!$ready) { throw ('The UI did not become ready. Inspect ' + (Join-Path $env:TASK_CONTINUITY_DATA 'Logs\supervisor-error.log')) }
if (!$Background) { Start-Process $url }
Write-Output "Agent ready at $url (watchdog PID $($p.Id))."
$p.Dispose()
