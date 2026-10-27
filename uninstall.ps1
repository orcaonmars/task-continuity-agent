$ErrorActionPreference='Stop'
$shortcut=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Task Continuity Agent.lnk'
if(Test-Path $shortcut){Remove-Item $shortcut -Force}
Write-Host 'Shortcut removed. Repository and local task data were intentionally preserved.' -ForegroundColor Green
Write-Host 'Delete the cloned folder manually if you also want to erase tasks, logs, config, and the virtual environment.'
