$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) { throw 'Run start.cmd once to create .venv first.' }
& $buildPython -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
& $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name LocalPlanAPI --add-data 'plan_api/dashboard.html;plan_api' --collect-data certifi run.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Output 'Portable executable: dist/LocalPlanAPI/LocalPlanAPI.exe'
