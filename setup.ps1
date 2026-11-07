param([switch]$NoLaunch, [switch]$NoShortcut, [switch]$InstallLMStudio, [switch]$Repair)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\setup-common.ps1')

function Invoke-TaskSetup {
    param([string]$Root, [switch]$NoLaunch, [switch]$NoShortcut, [switch]$InstallLMStudio, [switch]$Repair)
    Assert-SetupUser
    $Root = [IO.Path]::GetFullPath($Root)
    $Data = Get-SetupData $Root
    $logs = Join-Path $Data 'Logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $identity = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant() + '|' + $Data.ToLowerInvariant()))).Replace('-', '') }
    finally { $hasher.Dispose() }
    $mutex = [Threading.Mutex]::new($false, ('Local\TaskContinuitySetup-' + $identity.Substring(0, 16)))
    $owned = $false
    try {
        try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (!$owned) { Write-Host 'Setup is already running. Wait for the other setup window to finish.'; return }
        Write-Host 'Task Continuity Agent' -ForegroundColor Cyan
        Write-Host 'Checking setup. The first run needs an internet connection.'
        $needsInstall = $Repair -or !(Test-SetupReady $Root $Data)
        if ($needsInstall) {
            Write-Host '[1/3] Checking Python...'
            $python = Find-SetupPython $Root
            if (!$python) {
                Write-Host 'Installing Python 3.12 for your Windows account. This can take a few minutes.'
                try { Invoke-SetupWinget 'Python.Python.3.12' (Join-Path $logs 'setup-python.log') }
                catch { throw ($_.Exception.Message + ' Install Python 3.11+ from https://www.python.org/downloads/windows/, then run Start Task Continuity.cmd again.') }
                $python = Find-SetupPython $Root
                if (!$python) { throw 'Python was installed but could not be found yet. Close this window and run Start Task Continuity.cmd again.' }
            }
            Write-Host '[2/3] Installing the app and checking it works. Please keep this window open.'
            $installLog = Join-Path $logs 'setup-install.log'
            Invoke-SetupInstall $Root $python $installLog -NoShortcut:$NoShortcut
            $statePath = Join-Path $Data 'State'
            New-Item -ItemType Directory -Force -Path $statePath | Out-Null
            @{ setup_version = 1; requirements_sha256 = (Get-SetupRequirementsHash $Root) } |
                ConvertTo-Json | Set-Content -LiteralPath (Join-Path $statePath 'setup-complete.json') -Encoding UTF8
        } else {
            Write-Host 'The app is already installed. No dependency download is needed.'
        }
        $config = Join-Path $Data 'config.json'
        if ($env:TASK_CONTINUITY_CONFIG) { $config = $env:TASK_CONTINUITY_CONFIG }
        $provider = 'lmstudio'
        if (Test-Path -LiteralPath $config) { $provider = (Get-Content -LiteralPath $config -Raw | ConvertFrom-Json).provider }
        $lmstudio = Find-SetupLMStudio
        $wantStudio = $InstallLMStudio.IsPresent
        if ($needsInstall -and !$NoLaunch -and !$lmstudio -and $provider -eq 'lmstudio' -and !$wantStudio) {
            Write-Host ''
            Write-Host 'LM Studio runs the AI model on your computer. You can use another compatible server instead.'
            $answer = Read-Host 'Install LM Studio for your account too? [Y/n]'
            $wantStudio = $answer.Trim() -notin @('n', 'N', 'no', 'No')
        }
        if ($wantStudio -and !$lmstudio) {
            Write-Host 'Installing LM Studio. Models are chosen separately inside LM Studio.'
            try { Invoke-SetupWinget 'ElementLabs.LMStudio' (Join-Path $logs 'setup-lmstudio.log') }
            catch { Write-Warning ($_.Exception.Message + ' You can also install it from https://lmstudio.ai/. The app setup can continue.') }
            $lmstudio = Find-SetupLMStudio
        }
        Write-Host '[3/3] App setup is ready.' -ForegroundColor Green
        if (!$NoLaunch) {
            if ($needsInstall -and $lmstudio -and $provider -eq 'lmstudio') { Start-Process -FilePath $lmstudio -WindowStyle Normal }
            Write-Host 'Opening the app. Its setup panel explains how to load a model and connect it.'
            Open-SetupApplication $Root
        }
    } finally {
        if ($owned) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

# Tests can import the entry point without installing or opening anything.
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-TaskSetup -Root $PSScriptRoot -NoLaunch:$NoLaunch -NoShortcut:$NoShortcut -InstallLMStudio:$InstallLMStudio -Repair:$Repair }
    catch { Write-Host ''; Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
