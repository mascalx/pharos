# PHAROS native Windows x64 installer — experimental.
# Install package + pinned Qdrant + official Kiwix with a local foreground launcher.
# No Windows service, admin rights, Docker, public listener or data removal.
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'PHAROS'),
    [string]$RepoUrl = 'https://github.com/mascalx/pharos.git',
    [switch]$SkipKiwix,
    [switch]$NoDesktopShortcut
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
if (-not [Environment]::Is64BitOperatingSystem) { throw 'PHAROS requires 64-bit Windows.' }
if ($PSVersionTable.PSVersion.Major -lt 5) { throw 'PowerShell 5.1+ is required.' }
$AllowedRepos = @(
    'git@github.com:mascalx/pharos.git', 'git@github.com:mascalx/pharos',
    'https://github.com/mascalx/pharos.git', 'https://github.com/mascalx/pharos'
)
if ($AllowedRepos -notcontains $RepoUrl) { throw 'Only the official PHAROS GitHub repository is permitted.' }
# Only install missing prerequisites, from the official winget source, with consent.
function Update-ProcessPath {
    $MachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$env:Path;$MachinePath;$UserPath"
    foreach ($Directory in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher'),
        (Join-Path $env:ProgramFiles 'Git\cmd')
    )) {
        if (Test-Path -LiteralPath $Directory -PathType Container) { $env:Path += ";$Directory" }
    }
}
function Test-CompatiblePython {
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) { return $false }
    & py.exe -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) and sys.maxsize > 2**32 else 1)'
    return ($LASTEXITCODE -eq 0)
}
function Install-Prerequisite([string]$Id, [string]$Description) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Missing $Description. Install it from its official website, or install Microsoft App Installer (winget), then rerun setup. Git: https://git-scm.com/download/win ; Python: https://www.python.org/downloads/windows/"
    }
    $Consent = Read-Host "Install $Description using Windows Package Manager ($Id)? [y/N]"
    if ($Consent -notin @('y', 'Y')) { throw "Setup stopped. Install $Description manually and rerun." }
    & winget.exe install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installation of $Description failed. Resolve the winget error and rerun." }
    Update-ProcessPath
}
Update-ProcessPath
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    Install-Prerequisite 'Git.Git' 'Git for Windows'
}
if (-not (Test-CompatiblePython)) {
    Install-Prerequisite 'Python.Python.3.13' 'Python 3.13 x64 with py launcher'
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue) -or -not (Test-CompatiblePython)) {
    throw 'Prerequisites installed but not visible or compatible. Open a new PowerShell terminal and rerun setup.'
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$Repo = Join-Path $InstallRoot 'source'
$Venv = Join-Path $InstallRoot 'venv'
$ConfigDir = Join-Path $InstallRoot 'config'
$Config = Join-Path $ConfigDir 'config.toml'
$Py = Join-Path $Venv 'Scripts\python.exe'
$BinDir = Join-Path $InstallRoot 'bin'
$Qdrant = Join-Path $BinDir 'qdrant.exe'
$QdrantTag = 'v1.18.3'
$QdrantUrl = 'https://github.com/qdrant/qdrant/releases/download/v1.18.3/qdrant-x86_64-pc-windows-msvc.zip'
$QdrantSha256 = '984619bbd4032ace578656174c465c5d6b71d1267ecad5b7b4c21cc6549ca833'
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (-not (Test-Path -LiteralPath $Repo)) {
    & git clone -- $RepoUrl $Repo
    if ($LASTEXITCODE -ne 0) { throw 'Unable to clone the official PHAROS repository.' }
} else {
    if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git'))) {
        throw 'Existing source directory is not a Git checkout.'
    }
    $Origin = (& git -C $Repo remote get-url origin).Trim()
    if ($AllowedRepos -notcontains $Origin) { throw 'Untrusted existing Git origin.' }
    if (-not $?) { throw 'Unable to inspect the local Git origin.' }
    & git -C $Repo pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Git checkout has conflicts or could not be fast-forwarded.' }
}
if (-not (Test-Path -LiteralPath $Py)) {
    & py -3 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python virtual environment.' }
}
& $Py -m pip install --disable-pip-version-check $Repo
if ($LASTEXITCODE -ne 0) { throw 'PHAROS Python package installation failed.' }
if (-not (Test-Path -LiteralPath $Config)) {
    & $Py -m pharos.cli init --directory $ConfigDir
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create PHAROS configuration.' }
}
$DataDir = Join-Path $ConfigDir 'pharos-data'
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$Marker = Join-Path $DataDir '.pharos-volume'
if (-not (Test-Path -LiteralPath $Marker)) {
    & $Py -m pharos.cli storage-init --config $Config --confirm-path $DataDir
    if ($LASTEXITCODE -ne 0) { throw 'Data-volume validation failed. Existing files have not been deleted.' }
}
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
if (-not (Test-Path -LiteralPath $Qdrant)) {
    $Temp = Join-Path $InstallRoot ('qdrant-download-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Temp | Out-Null
    try {
        $Archive = Join-Path $Temp 'qdrant.zip'
        Invoke-WebRequest -Uri $QdrantUrl -OutFile $Archive -UseBasicParsing
        $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne $QdrantSha256) { throw 'Qdrant download SHA-256 mismatch.' }
        $Expanded = Join-Path $Temp 'expanded'
        Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded
        $Matches = @(Get-ChildItem -LiteralPath $Expanded -Filter 'qdrant.exe' -File -Recurse)
        if ($Matches.Count -ne 1) { throw 'Qdrant package does not contain exactly one executable.' }
        Copy-Item -LiteralPath $Matches[0].FullName -Destination $Qdrant -ErrorAction Stop
    }
    finally {
        Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$DBPath = (Join-Path $ConfigDir 'pharos-db').Replace('\','/')
$QdrantPath = (Join-Path $ConfigDir 'qdrant.yaml')
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir 'pharos-db') | Out-Null
$QdrantConfig = @"
storage:
  storage_path: "$DBPath/qdrant"
  snapshots_path: "$DBPath/qdrant-snapshots"
service:
  host: 127.0.0.1
  http_port: 6333
"@
if (-not (Test-Path -LiteralPath $QdrantPath)) {
    [IO.File]::WriteAllText($QdrantPath, $QdrantConfig, (New-Object Text.UTF8Encoding($false)))
}
if (-not $SkipKiwix) {
    # Official publisher binaries, selected for Windows x64 and verified by
    # existing Kiwix downloader. Any failure aborts installation safely.
    & $Py -c 'import sys; from pharos.config import load_config; from pharos.kiwix_runtime import KiwixManagedService; KiwixManagedService(load_config(sys.argv[1])).install()' $Config
    if ($LASTEXITCODE -ne 0) { throw 'Official Kiwix binary installation failed.' }
}

# Current-user shortcuts only. Re-running setup refreshes the same links.
# ICO uses the original PNG, embedded losslessly in the Windows icon container.
try {
    $Icon = Join-Path $InstallRoot 'pharos.ico'
    $Png = Join-Path $Repo 'src\pharos\web\pharos-icon.png'
    $IconCode = @'
import pathlib, struct, sys
png = pathlib.Path(sys.argv[1]).read_bytes()
if png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR':
    raise ValueError('Invalid PHAROS icon PNG')
w, h = struct.unpack('>II', png[16:24])
if not (1 <= w <= 256 and 1 <= h <= 256):
    raise ValueError('Unsupported PHAROS icon dimensions')
header = struct.pack('<HHH', 0, 1, 1)
entry = struct.pack('<BBBBHHII', w % 256, h % 256, 0, 0, 1, 32, len(png), 22)
pathlib.Path(sys.argv[2]).write_bytes(header + entry + png)
'@
    $IconCode | & $Py - $Png $Icon
    if ($LASTEXITCODE -ne 0) { throw 'PHAROS icon creation failed.' }
    $StartScript = Join-Path $Repo 'scripts\start-windows.ps1'
    if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) { throw 'PHAROS launcher missing.' }
    $PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $LinkArgs = '-NoProfile -File "' + $StartScript + '" -InstallRoot "' + $InstallRoot + '"'
    $Shell = New-Object -ComObject WScript.Shell
    $Folders = @([Environment]::GetFolderPath([Environment+SpecialFolder]::Programs))
    if (-not $NoDesktopShortcut) {
        $Folders += [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
    }
    foreach ($Folder in $Folders) {
        if ([string]::IsNullOrWhiteSpace($Folder)) { throw 'Windows shortcut folder unavailable.' }
        New-Item -ItemType Directory -Force -Path $Folder | Out-Null
        $Link = $Shell.CreateShortcut((Join-Path $Folder 'PHAROS.lnk'))
        $Link.TargetPath = $PowerShellExe
        $Link.Arguments = $LinkArgs
        $Link.WorkingDirectory = $InstallRoot
        $Link.IconLocation = $Icon + ',0'
        $Link.Description = 'Start PHAROS local server'
        $Link.WindowStyle = 1
        $Link.Save()
    }
    Write-Host 'PHAROS shortcuts created in Start menu and requested desktop locations.'
}
catch {
    Write-Warning ("PHAROS is installed, but shortcut creation failed: " + $_.Exception.Message)
}

Write-Host ''
Write-Host 'PHAROS Windows preview installed without modifying existing data.' -ForegroundColor Green
Write-Host 'Start with:'
Write-Host ("  powershell -NoProfile -File `"{0}`" -InstallRoot `"{1}`"" -f (Join-Path $Repo 'scripts\start-windows.ps1'),$InstallRoot)
Write-Host 'Local URL: http://127.0.0.1:8080/'
Write-Host 'Note: Windows startup is foreground-only; no automatic Windows service is installed.'
