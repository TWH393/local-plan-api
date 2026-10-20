@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
if not exist ".venv\requirements.ready" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
  echo ready>".venv\requirements.ready"
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Startup failed. Python 3.11 or newer is required. See README.md.
pause
exit /b 1
