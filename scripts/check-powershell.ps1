# Parse scripts without executing installers or changing system state.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
foreach ($File in @('install-pharos.ps1', 'uninstall-pharos.ps1', 'scripts/start-windows.ps1', 'scripts/check-powershell.ps1')) {
    $Tokens = $null
    $Errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Root $File), [ref]$Tokens, [ref]$Errors)
    if ($Errors.Count -gt 0) {
        $Errors | ForEach-Object { Write-Host $_.ToString() }
        throw "PowerShell syntax check failed: $File"
    }
    Write-Host "PASS: $File"
}
