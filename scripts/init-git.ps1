$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$git=Get-Command git -ErrorAction SilentlyContinue
if(!$git){
  throw 'Git is not installed or not on PATH. Install Git first, then rerun this script. The folder is already GitHub-upload ready without this step.'
}
if(!(Test-Path (Join-Path $Root '.git'))){
  & $git.Source -C $Root init -b main
  if($LASTEXITCODE -ne 0){throw 'Git initialization failed.'}
}
& $git.Source -C $Root add .
if($LASTEXITCODE -ne 0){throw 'Git staging failed.'}
Write-Host 'Repository initialized and files staged. Review git status, set your Git identity if needed, then commit/push.' -ForegroundColor Green
& $git.Source -C $Root status --short
