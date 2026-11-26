# PHAROS Windows x64 launcher; keep terminal open and stop using Ctrl+C.
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'PHAROS'))
$ErrorActionPreference = 'Stop'
$Py = Join-Path $InstallRoot 'venv\Scripts\python.exe'
$Supervisor = Join-Path $PSScriptRoot 'windows_supervisor.py'
& $Py $Supervisor $InstallRoot
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PHAROS startup failed. See the diagnostic message above and logs under PHAROS\logs.'
    Read-Host 'Press Enter to close'
    exit 1
}
