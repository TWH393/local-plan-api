@echo off
setlocal
set "appExe=%~dp0dist\LocalPlanAPI\LocalPlanAPI.exe"
if not exist "%appExe%" (
  echo Program not built. Run build-windows.ps1 first.
  pause
  exit /b 1
)
start "" "%appExe%"
