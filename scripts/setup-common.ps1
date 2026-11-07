# Shared first-run helpers. Dot-sourcing this file does not install anything.

function Assert-SetupUser {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open Start Task Continuity.cmd normally, without Run as administrator.'
    }
}

function Test-SetupPython([string]$Executable, [string[]]$Arguments = @()) {
    if (!$Executable -or $Executable -like '*\Microsoft\WindowsApps\python*.exe') { return $null }
    try {
        $probe = & $Executable @Arguments -c 'import json,sys,venv,ensurepip; print(json.dumps(dict(path=sys.executable,major=sys.version_info.major,minor=sys.version_info.minor)))' 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $runtime = ($probe | Select-Object -Last 1) | ConvertFrom-Json
        if ($runtime.major -eq 3 -and $runtime.minor -ge 11 -and (Test-Path -LiteralPath $runtime.path)) {
            return [string]$runtime.path
        }
    } catch { return $null }
    return $null
}

function Find-SetupPython([string]$Root) {
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) {
        $found = Test-SetupPython $venv
        if ($found) { return $found }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\python*.exe') {
        $found = Test-SetupPython $command.Source
        if ($found) { return $found }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.12', '-3.14', '-3.13', '-3.11', '-3')) {
            $found = Test-SetupPython -Executable $launcher.Source -Arguments @($version)
            if ($found) { return $found }
        }
    }
    if ($env:LOCALAPPDATA) {
        foreach ($version in @('312', '314', '313', '311')) {
            $path = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
            if (Test-Path -LiteralPath $path) {
                $found = Test-SetupPython $path
                if ($found) { return $found }
            }
        }
    }
    return $null
}

function Invoke-SetupWinget([string]$PackageId, [string]$LogPath) {
    if ($PackageId -notin @('Python.Python.3.12', 'ElementLabs.LMStudio')) { throw 'Unsupported setup package.' }
    $command = Get-Command winget -ErrorAction SilentlyContinue
    if (!$command) { throw 'Windows App Installer (winget) is not available. Use the manual download link shown in setup.' }
    $installArguments = @('install', '--id', $PackageId, '--exact', '--source', 'winget',
        '--scope', 'user', '--silent', '--accept-package-agreements',
        '--accept-source-agreements', '--disable-interactivity')
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $command.Source @installArguments *> $LogPath
    } finally { $ErrorActionPreference = $previousPreference }
    if ($LASTEXITCODE -ne 0) {
        throw "The per-user installer did not finish (exit $LASTEXITCODE). Details: $LogPath. No administrator fallback was attempted."
    }
}

function Get-SetupData([string]$Root) {
    if ($env:TASK_CONTINUITY_DATA) { return [IO.Path]::GetFullPath($env:TASK_CONTINUITY_DATA) }
    return Join-Path $Root 'data'
}

function Get-SetupRequirementsHash([string]$Root) {
    $stream = [IO.File]::OpenRead((Join-Path $Root 'requirements.txt'))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $hasher.Dispose() }
}

function Test-SetupReady([string]$Root, [string]$Data) {
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    $marker = Join-Path $Data 'State\setup-complete.json'
    if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $marker)) { return $false }
    try {
        $state = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        $hash = Get-SetupRequirementsHash $Root
        if ($state.setup_version -ne 1 -or $state.requirements_sha256 -ne $hash) { return $false }
        if (!(Test-SetupPython $python)) { return $false }
        & $python -m pip check *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        & $python -c 'import mcp,requests,psutil,PIL,playwright' *> $null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-SetupLMStudio {
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\LM Studio\LM Studio.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'LM Studio\LM Studio.exe' }
    foreach ($path in $candidates) { if (Test-Path -LiteralPath $path) { return $path } }
    return $null
}

function Invoke-SetupInstall([string]$Root, [string]$Python, [string]$LogPath, [switch]$NoShortcut) {
    # A child process keeps Windows PowerShell 5.1 from treating ordinary native
    # stderr (including unittest progress) as a terminating PowerShell error.
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Root 'install.ps1') + '" -PythonExecutable "' + $Python + '"'
    if ($NoShortcut) { $arguments += ' -NoShortcut' }
    $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $LogPath -RedirectStandardError ($LogPath + '.stderr')
    if ($process.ExitCode -ne 0) { throw "Application installation failed. Details: $LogPath and $LogPath.stderr" }
}

function Open-SetupApplication([string]$Root) {
    & (Join-Path $Root 'start-agent.ps1')
}
