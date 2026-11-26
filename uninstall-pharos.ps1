# Conservative removal of PHAROS for the current Windows user.
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'PHAROS'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $Root -PathType Container)) { Write-Host 'PHAROS installation not found.'; return }
$RootItem = Get-Item -LiteralPath $Root -Force
if (($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point installation root: manual removal required.' }
$Source = Join-Path $Root 'source'
$Venv = Join-Path $Root 'venv'
$Bin = Join-Path $Root 'bin'
$Config = Join-Path $Root 'config\config.toml'
$Launcher = Join-Path $Source 'scripts\start-windows.ps1'
if (-not (Test-Path -LiteralPath $Config -PathType Leaf) -or -not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw 'Installation not recognized. No files removed.'
}
foreach ($Path in @($Source,$Venv,$Bin)) {
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Unsafe path: $Path" }
    }
}
if ((Test-Path -LiteralPath $Venv) -and -not (Test-Path -LiteralPath (Join-Path $Venv 'pyvenv.cfg') -PathType Leaf)) {
    throw 'Unrecognized virtual environment.'
}
# Do not kill unrelated processes; require the user to stop PHAROS normally.
$LockPath = Join-Path $Root 'launcher.lock'
$Lock = $null
try {
    $Lock = [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    try { $Lock.Lock(0,1) } catch { throw 'PHAROS is running. Stop it with Ctrl+C and retry.' }
    $Active = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)
    })
    if ($Active.Count -gt 0) { throw 'PHAROS processes are still running. Close them normally and retry.' }
    Write-Host "Remove application virtualenv and PHAROS shortcuts from $Root"
    Write-Host 'Preserve source, configuration, secrets, data, indexes, ZIM, maps, logs and downloaded binaries.'
    $Answer = Read-Host 'Type REMOVE PHAROS to continue'
    if ($Answer -cne 'REMOVE PHAROS') { Write-Host 'Cancelled.'; return }
    $Shell = New-Object -ComObject WScript.Shell
    foreach ($Folder in @(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs),
        [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
    )) {
        if ([string]::IsNullOrWhiteSpace($Folder)) { continue }
        $Path = Join-Path $Folder 'PHAROS.lnk'
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Link = $Shell.CreateShortcut($Path)
            if ($Link.Arguments.Contains('"' + $Launcher + '"') -and $Link.WorkingDirectory -eq $Root) {
                Remove-Item -LiteralPath $Path
            } else { Write-Warning "Unrecognized shortcut retained: $Path" }
        }
    }
    if (Test-Path -LiteralPath $Venv) {
        # Refuse junctions/reparse points anywhere inside a tree to be removed.
        $Pending = New-Object 'System.Collections.Generic.Stack[string]'
        $Pending.Push($Venv)
        while ($Pending.Count -gt 0) {
            foreach ($Child in Get-ChildItem -LiteralPath ($Pending.Pop()) -Force) {
                if (($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Unsafe virtualenv entry: $($Child.FullName)" }
                if ($Child.PSIsContainer) { $Pending.Push($Child.FullName) }
            }
        }
        Remove-Item -LiteralPath $Venv -Recurse -Force
    }
    Write-Host 'PHAROS application removed. Data retained. See docs/UNINSTALL.md.'
}
finally {
    if ($null -ne $Lock) { $Lock.Dispose() }
}
