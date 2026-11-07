param([string]$PythonExecutable = '', [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$admin = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) {
    throw 'Run install.ps1 normally, not as Administrator.'
}

. (Join-Path $Root 'scripts\setup-common.ps1')
$Python = if ($PythonExecutable) { Test-SetupPython $PythonExecutable } else { Find-SetupPython $Root }
if (!$Python) { throw 'Python 3.11+ was not found. Double-click Start Task Continuity.cmd to set it up.' }
$Version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$Parts = $Version.Trim().Split('.')
if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 11)) {
    throw "Python 3.11+ is required. Found Python $Version at $Python"
}

$Venv = Join-Path $Root '.venv'
$Data = Get-SetupData $Root
if (!(Test-Path $Venv)) {
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the virtual environment.' }
}

$Py = Join-Path $Venv 'Scripts\python.exe'
if (!(Test-Path -LiteralPath $Py)) { throw 'The virtual environment is incomplete. Rename .venv and rerun install.ps1.' }
if (!(Test-SetupPython $Py)) { throw 'The existing .venv is not a supported Python environment. Rename it and run Start Task Continuity.cmd again.' }
& $Py -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Python dependency installation failed.'
}
& $Py -m pip check
if ($LASTEXITCODE -ne 0) {
    throw 'Installed Python dependencies are inconsistent.'
}

New-Item -ItemType Directory -Force $Data,(Join-Path $Data 'Tasks'),(Join-Path $Data 'Logs'),(Join-Path $Data 'State') | Out-Null
$Config = if ($env:TASK_CONTINUITY_CONFIG) { $env:TASK_CONTINUITY_CONFIG } else { Join-Path $Data 'config.json' }

$env:TASK_CONTINUITY_ROOT = $Root
$env:TASK_CONTINUITY_DATA = $Data
$env:TASK_CONTINUITY_CONFIG = $Config
# The common initializer also recovers a pending-only config after a locked write.
& $Py -c "import os,sys;sys.path.insert(0,os.path.join(os.environ['TASK_CONTINUITY_ROOT'],'app'));from common import ensure_data_layout;ensure_data_layout()"
if ($LASTEXITCODE -ne 0) {
    throw 'Configuration validation failed.'
}

$env:TASK_CONTINUITY_TEST_PYTHON = $Py
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\validate.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Installation validation failed.'
}

if (!$NoShortcut) {
$Shell = New-Object -ComObject WScript.Shell
$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Task Continuity Agent.lnk'
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = 'powershell.exe'
$Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Root 'setup.ps1') + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = 'Task Continuity Agent'
$Shortcut.Save()
}

Write-Host 'Task Continuity Agent installed and validated.' -ForegroundColor Green
Write-Host "Config: $Config"
if (!$NoShortcut) { Write-Host "Shortcut: $ShortcutPath" }
