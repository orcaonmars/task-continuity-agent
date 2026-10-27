$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$Python=$null
$candidates=@()
if($env:TASK_CONTINUITY_TEST_PYTHON){$candidates+=$env:TASK_CONTINUITY_TEST_PYTHON}
$candidates+=(Join-Path $Root '.venv\Scripts\python.exe')
$globalPython=(Get-Command python -ErrorAction SilentlyContinue)
if($globalPython){$candidates+=$globalPython.Source}
foreach($candidate in $candidates){if($candidate -and (Test-Path $candidate)){$Python=$candidate;break}}
if(!$Python){throw 'Python not found.'}
Write-Host 'Task Continuity Agent validation' -ForegroundColor Cyan
& $Python -m compileall -q (Join-Path $Root 'app') (Join-Path $Root 'tests') (Join-Path $Root 'scripts')
if($LASTEXITCODE -ne 0){throw 'Python compile failed.'}
& $Python -m pip check
if($LASTEXITCODE -ne 0){throw 'Python dependency consistency check failed.'}
& $Python -m unittest discover -s (Join-Path $Root 'tests') -v
if($LASTEXITCODE -ne 0){throw 'Python tests failed.'}
$ps=@(Get-ChildItem -LiteralPath $Root -File -Filter *.ps1)+@(Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter *.ps1 -Recurse)
foreach($script in $ps){
 $path=$script.FullName;$rel=$path.Substring($Root.Length+1);$tokens=$null;$errors=$null
 [System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null
 if($errors.Count){$msg=($errors|ForEach-Object{$_.Message}) -join '; ';throw "PowerShell syntax failed: $rel : $msg"}
}
& $Python (Join-Path $PSScriptRoot 'check-release.py')
if($LASTEXITCODE -ne 0){throw 'Public-source release checks failed.'}
Write-Host 'VALIDATION PASSED' -ForegroundColor Green
