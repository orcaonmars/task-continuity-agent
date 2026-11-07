@echo off
setlocal
title Task Continuity Agent
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup could not finish. The message above explains what to do next.
  pause
)
