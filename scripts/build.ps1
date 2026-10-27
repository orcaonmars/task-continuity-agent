$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'validate.ps1')
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
$Python=$env:TASK_CONTINUITY_TEST_PYTHON
if(!$Python -or !(Test-Path $Python)){
 $candidate=Join-Path $Root '.venv\Scripts\python.exe'
 if(Test-Path $candidate){$Python=$candidate}else{$Python=(Get-Command python -ErrorAction Stop).Source}
}
& $Python (Join-Path $PSScriptRoot 'package-release.py')
if($LASTEXITCODE -ne 0){throw 'Packaging failed.'}
