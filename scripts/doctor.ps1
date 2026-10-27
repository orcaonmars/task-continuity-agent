param([switch]$Json)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $Root 'data'
$Example = Join-Path $Root 'config.example.json'
$Installed = Join-Path $Data 'config.json'
$ConfigPath = if (Test-Path $Installed) { $Installed } else { $Example }

function Find-CommandPath([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (!(Test-Path $Python)) {
    $Python = Find-CommandPath 'python'
}

$configOk = $false
$configError = ''
$cfg = $null

if ($Python) {
    $env:TASK_CONTINUITY_ROOT = $Root
    $env:TASK_CONTINUITY_DATA = $Data
    $env:TASK_CONTINUITY_CONFIG = $ConfigPath
    try {
        $cfgText = & $Python -c "import json,os,sys; sys.path.insert(0,os.path.join(os.environ['TASK_CONTINUITY_ROOT'],'app')); from common import settings; print(json.dumps(settings()))"
        if ($LASTEXITCODE -ne 0) { throw 'Python rejected config.json.' }
        $cfg = ($cfgText | Select-Object -Last 1) | ConvertFrom-Json
        $configOk = $true
    } catch {
        $configError = $_.Exception.Message
    }
}

$pythonVersion = ''
if ($Python) {
    try { $pythonVersion = (& $Python -c "import sys; print(sys.version.split()[0])").Trim() } catch {}
}

$modelReady = $false
$modelDetail = ''
if ($configOk) {
    try {
        if ($cfg.provider -eq 'lmstudio') {
            $base = $cfg.api_base.TrimEnd('/') -replace '/v1$',''
            $models = Invoke-RestMethod ($base + '/api/v1/models') -TimeoutSec 4
            $loaded = @($models.models | Where-Object { $_.type -eq 'llm' -and $_.loaded_instances.Count -gt 0 })
            $modelReady = $loaded.Count -gt 0
            $modelDetail = if ($modelReady) {
                $loaded[0].loaded_instances[0].id
            } else {
                'LM Studio reachable; no LLM loaded'
            }
        } else {
            $models = Invoke-RestMethod ($cfg.api_base.TrimEnd('/') + '/models') -TimeoutSec 4
            $available = @($models.data)
            $modelReady = $available.Count -gt 0
            $modelDetail = if ($modelReady) { $available[0].id } else { 'Endpoint reachable; no models returned' }
        }
    } catch {
        $modelDetail = $_.Exception.Message
    }
}

$uiReady = $false
if ($configOk) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$($cfg.ui_port)/api/health" -TimeoutSec 2
        $uiReady = $health.app_id -eq 'task-continuity-agent' -and $health.source_root -eq $Root
    } catch {}
}

$Chrome = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (!$Chrome) {
    $appPath = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($appPath) {
        $candidate = $appPath.'(default)'
        if ($candidate -and (Test-Path $candidate)) { $Chrome = $candidate }
    }
}

$Git = Find-CommandPath 'git'
$Ffmpeg = Find-CommandPath 'ffmpeg'
$Ffprobe = Find-CommandPath 'ffprobe'
if (!$Ffmpeg -and (Test-Path 'C:\ffmpeg\bin\ffmpeg.exe')) { $Ffmpeg = 'C:\ffmpeg\bin\ffmpeg.exe' }
if (!$Ffprobe -and (Test-Path 'C:\ffmpeg\bin\ffprobe.exe')) { $Ffprobe = 'C:\ffmpeg\bin\ffprobe.exe' }

$Blender = $null
if ($configOk -and $cfg.blender_exe -and (Test-Path $cfg.blender_exe)) {
    $Blender = $cfg.blender_exe
}
if (!$Blender) { $Blender = Find-CommandPath 'blender' }
if (!$Blender) {
    $blenderRoot = Join-Path $env:ProgramFiles 'Blender Foundation'
    if (Test-Path $blenderRoot) {
        $Blender = Get-ChildItem $blenderRoot -Recurse -Filter blender.exe -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
}

$dataWritable = $false
try {
    New-Item -ItemType Directory -Force (Join-Path $Data 'State') | Out-Null
    $probe = Join-Path $Data ('State\doctor-' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($probe, 'ok')
    Remove-Item $probe -Force
    $dataWritable = $true
} catch {}

$freeGb = $null
try {
    $item = Get-Item $Root
    $freeGb = [math]::Round((Get-PSDrive -Name $item.PSDrive.Name).Free / 1GB, 1)
} catch {}

$extra = [ordered]@{}
if ($configOk -and $cfg.extra_mcp_servers) {
    foreach ($property in $cfg.extra_mcp_servers.PSObject.Properties) {
        $name = $property.Name
        $command = [string]$property.Value.command
        $resolved = if (Test-Path $command) { $command } else { Find-CommandPath $command }
        $extra[$name] = [ordered]@{
            command = $command
            available = [bool]$resolved
            resolved = $resolved
        }
    }
}

$result = [ordered]@{
    core = [ordered]@{
        python = [bool]$Python
        python_version = $pythonVersion
        config_valid = $configOk
        config_error = $configError
        data_writable = $dataWritable
        model_endpoint = $modelReady
        model_detail = $modelDetail
        supervisor_running = $uiReady
        free_disk_gb = $freeGb
    }
    capabilities = if ($configOk) { $cfg.capabilities } else { $null }
    optional = [ordered]@{
        chrome = [bool]$Chrome
        git = [bool]$Git
        ffmpeg = [bool]$Ffmpeg
        ffprobe = [bool]$Ffprobe
        blender = [bool]$Blender
        extra_mcp = $extra
    }
    paths = [ordered]@{
        python = $Python
        chrome = $Chrome
        git = $Git
        ffmpeg = $Ffmpeg
        ffprobe = $Ffprobe
        blender = $Blender
        config = $ConfigPath
        data = $Data
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
    if (!$Python -or !$configOk -or !$dataWritable -or !$modelReady) { exit 1 }
    exit 0
}

Write-Host 'Task Continuity Agent doctor' -ForegroundColor Cyan
Write-Host ('Python/runtime:       ' + $(if($Python){'OK ' + $pythonVersion}else{'MISSING'}))
Write-Host ('Config:               ' + $(if($configOk){'OK'}else{'INVALID ' + $configError}))
Write-Host ('Data state:           ' + $(if($dataWritable){'writable'}else{'NOT WRITABLE'}))
Write-Host ('Local model:          ' + $(if($modelReady){'OK'}else{'NOT READY'}) + '  ' + $modelDetail)
Write-Host ('Supervisor:           ' + $(if($uiReady){'running'}else{'not running'}))
Write-Host ('Free disk:            ' + $(if($null -ne $freeGb){$freeGb.ToString() + ' GB'}else{'unknown'}))
Write-Host ('Chrome:               ' + $(if($Chrome){'OK'}else{'optional / missing'}))
Write-Host ('Git:                  ' + $(if($Git){'OK'}else{'optional / missing'}))
Write-Host ('ffmpeg / ffprobe:     ' + $(if($Ffmpeg -and $Ffprobe){'OK'}else{'optional / incomplete'}))
Write-Host ('Blender:              ' + $(if($Blender){'OK'}else{'optional / missing'}))

if ($configOk -and $cfg.capabilities.blender) {
    Write-Warning 'Live Blender socket actions also require a compatible Blender MCP add-on. Background Blender jobs only require Blender.'
}
if ($extra.Count) {
    foreach ($name in $extra.Keys) {
        Write-Host ("Extra MCP " + $name + ": " + $(if($extra[$name].available){'OK'}else{'command missing'}))
    }
}

if (!$Python -or !$configOk -or !$dataWritable -or !$modelReady) { exit 1 }
