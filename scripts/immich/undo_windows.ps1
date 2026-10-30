<#
Homecloud: remove the Immich test install from a Windows PC, after you type DELETE.

Run it yourself in PowerShell, from the repository folder:
  powershell -ExecutionPolicy Bypass -File scripts\immich\undo_windows.ps1              Immich's containers, images and folder
  powershell -ExecutionPolicy Bypass -File scripts\immich\undo_windows.ps1 -Dir DIR     also the download folder DIR

Options
  -AppDir DIR     Immich's folder from the setup (default $env:USERPROFILE\immich-app)
  -Dir DIR        a download folder to delete too. It must hold .homecloud\manifest.jsonl.
  -KeyFile FILE   the saved Immich API key (default $env:USERPROFILE\.homecloud\immich-api-key)

It first lists everything it would remove, with sizes, and removes nothing until you type DELETE.
It never touches iCloud, your iPhone or the Photos app. It does not uninstall Docker Desktop or
Tailscale; at the end it prints how to do that by hand.

Sources
  [D1] docker compose down (--volumes, --rmi all, --remove-orphans):
       https://docs.docker.com/reference/cli/docker/compose/down/
  [D2] docker image ls --filter reference, docker image rm: https://docs.docker.com/reference/cli/docker/image/
  [D3] Uninstall Docker Desktop: https://docs.docker.com/desktop/uninstall/
  [I1] Immich backups live in UPLOAD_LOCATION/backups: https://docs.immich.app/administration/backup-and-restore
#>
[CmdletBinding()]
param(
    [string]$AppDir = '',
    [string]$Dir = '',
    [string]$KeyFile = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Say([string]$Text = '') { Write-Host $Text }
function Stop-Script([string]$Text, [int]$Code = 1) { Write-Host $Text; exit $Code }

function Get-AbsolutePath([string]$Path) {
    if ($Path -eq '~') { $Path = $env:USERPROFILE }
    elseif ($Path.StartsWith('~\') -or $Path.StartsWith('~/')) { $Path = Join-Path $env:USERPROFILE $Path.Substring(2) }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-Under([string]$Path, [string]$Parent) {
    if (-not $Parent) { return $false }
    return ($Path.TrimEnd('\') + '\').StartsWith($Parent.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-Deletable([string]$Path, [string]$What) {
    $root = [System.IO.Path]::GetPathRoot($Path).TrimEnd('\')
    $never = @($root, $env:USERPROFILE, $env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:APPDATA, $env:LOCALAPPDATA,
        [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('MyDocuments'), [Environment]::GetFolderPath('MyPictures'))
    foreach ($n in $never) {
        if ($n -and ($Path.TrimEnd('\') -ieq $n.TrimEnd('\'))) { Stop-Script "Refusing to delete $Path as $What." 2 }
    }
    if ((Test-Under $Path $env:SystemRoot) -or (Test-Under $Path $env:APPDATA) -or (Test-Under $Path $env:LOCALAPPDATA)) {
        Stop-Script "Refusing to delete $Path as ${What}: it is inside a Windows or app data folder." 2
    }
    if ($env:USERPROFILE -and (Test-Under $env:USERPROFILE $Path)) { Stop-Script "Refusing to delete $Path as ${What}: it contains your home folder." 2 }
    $users = if ($env:USERPROFILE) { [System.IO.Path]::GetDirectoryName($env:USERPROFILE.TrimEnd('\')) } else { $null }
    if ($users -and ([System.IO.Path]::GetDirectoryName($Path.TrimEnd('\')) -ieq $users)) { Stop-Script "Refusing to delete $Path as ${What}: it is a whole home folder." 2 }
    if ($env:OneDrive -and (Test-Under $env:OneDrive $Path)) { Stop-Script "Refusing to delete $Path as ${What}: it contains your OneDrive folder." 2 }
    $pictures = [Environment]::GetFolderPath('MyPictures')
    if (($pictures -and (Test-Under $Path (Join-Path $pictures 'iCloud Photos'))) -or ($Path -match '(?i)[\\/]iCloud ?Photos([\\/]|$)') -or ($Path -match '(?i)[\\/]iCloudDrive([\\/]|$)')) {
        Stop-Script "Refusing to delete $Path as ${What}: it is inside the iCloud Photos folder, and a delete there removes photos from iCloud." 2
    }
}

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    return @{ Code = $code; Output = (@($out) -join "`n") }
}

function Get-SizeText([string]$Path) {
    try {
        $files = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue)
        $bytes = 0.0
        foreach ($f in $files) { $bytes += $f.Length }
        if ($bytes -ge 1000000000) { return ('{0:N1} GB in {1} files' -f ($bytes / 1000000000), $files.Count) }
        return ('{0:N1} MB in {1} files' -f ($bytes / 1000000), $files.Count)
    } catch { return 'size unknown' }
}

function Get-EnvValue([string]$Key) {
    $envFile = Join-Path $AppDir '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return '' }
    $line = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match ('^' + [regex]::Escape($Key) + '=') }) | Select-Object -Last 1
    if (-not $line) { return '' }
    $value = $line.Substring($Key.Length + 1)
    if ($value -match "^'(.*)'$") { return $Matches[1] }
    if ($value -match '^"(.*)"$') { return $Matches[1] }
    return ($value -replace ' #.*$', '').Trim()
}

if (-not $AppDir) { $AppDir = Join-Path $env:USERPROFILE 'immich-app' }
if (-not $KeyFile) { $KeyFile = Join-Path $env:USERPROFILE '.homecloud\immich-api-key' }
$AppDir = Get-AbsolutePath $AppDir
$KeyFile = Get-AbsolutePath $KeyFile
Assert-Deletable $AppDir "Immich's folder"
if ($Dir) {
    $Dir = Get-AbsolutePath $Dir
    Assert-Deletable $Dir 'the download folder'
    if (-not (Test-Path -LiteralPath (Join-Path $Dir '.homecloud\manifest.jsonl'))) {
        Stop-Script "$Dir has no .homecloud\manifest.jsonl, so it is not a Homecloud download folder. Nothing was removed." 2
    }
}

# ---------------------------------------------------------------- what is there

$compose = Test-Path -LiteralPath (Join-Path $AppDir 'docker-compose.yml')
$upload = Get-EnvValue 'UPLOAD_LOCATION'
if ($upload) {
    if ($upload.StartsWith('./') -or $upload.StartsWith('.\')) { $upload = Join-Path $AppDir $upload.Substring(2) }
    elseif (-not [System.IO.Path]::IsPathRooted($upload)) { $upload = Join-Path $AppDir $upload }
    $upload = Get-AbsolutePath ($upload -replace '/', '\')
    Assert-Deletable $upload "Immich's library"
}
# On Windows the database is the Docker volume pgdata (Immich's Windows note); "down --volumes" removes it.

$docker = $null
$cmd = Get-Command docker -ErrorAction SilentlyContinue
if ($cmd) { $docker = $cmd.Source }
else {
    foreach ($c in @((Join-Path "$env:LOCALAPPDATA" 'Programs\DockerDesktop\resources\bin\docker.exe'), (Join-Path "$env:ProgramFiles" 'Docker\Docker\resources\bin\docker.exe'))) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { $docker = $c; break }
    }
}
$dockerRunning = $false
if ($docker) {
    $env:PATH = (Split-Path -Parent $docker) + ';' + $env:PATH
    $dockerRunning = ((Invoke-Native $docker @('info')).Code -eq 0)
}
if ($compose -and $docker -and -not $dockerRunning) {
    Stop-Script 'Docker Desktop is installed but not running, so Immich''s containers cannot be removed. Open Docker Desktop, wait until the engine runs, then run this again. Nothing was removed.' 1
}

Say 'This removes:'
$n = 0
if ($compose -and $dockerRunning) {
    $n++
    Say "  $n. Immich's containers, network, Docker volumes (including the database volume pgdata) and images"
    Say '     (docker compose down --volumes --rmi all), and any other image from ghcr.io/immich-app.'
} elseif ($dockerRunning) {
    $n++
    Say "  $n. Images from ghcr.io/immich-app left on this PC, such as the Immich CLI."
}
if ($upload -and (Test-Path -LiteralPath $upload) -and -not (Test-Under $upload $AppDir)) {
    $n++
    Say "  $n. Immich's library: $upload ($(Get-SizeText $upload))"
}
if (Test-Path -LiteralPath $AppDir) {
    $n++
    Say "  $n. Immich's folder: $AppDir ($(Get-SizeText $AppDir))"
    if ($upload -and (Test-Under $upload $AppDir)) { Say "     with Immich's library: $upload" }
}
if ($Dir) {
    $n++
    Say "  $n. The download folder: $Dir ($(Get-SizeText $Dir)), with its manifest"
}
if (Test-Path -LiteralPath $KeyFile) {
    $n++
    Say "  $n. The saved Immich API key: $KeyFile"
}
if ($n -eq 0) { Say "  nothing: no Immich install was found in $AppDir." }
Say ''
if ($upload) {
    Say 'Immich''s library holds every photo and video uploaded to Immich, including uploads from the'
    Say 'iPhone app, and Immich''s database backups (in its backups folder). If you already turned off'
    Say 'iCloud Photos, some of those photos may exist nowhere else. Copy what you need first.'
}
Say 'Not touched: iCloud, your iPhone, the Photos app, Docker Desktop and Tailscale.'
Say ''
if ($n -eq 0) { exit 0 }

$interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
if (-not $interactive) {
    Say 'Nothing was removed. This step needs your typed confirmation. Open PowerShell yourself,'
    Say 'go to the kit folder and run the same command there:'
    $line = "  powershell -ExecutionPolicy Bypass -File scripts/immich/undo_windows.ps1 -AppDir ""$AppDir"""
    if ($Dir) { $line += " -Dir ""$Dir""" }
    Say $line
    exit 2
}
$answer = Read-Host 'Type DELETE in capitals and press Enter to remove the items above'
if ($answer -cne 'DELETE') { Stop-Script 'Nothing was removed.' 2 }

# ---------------------------------------------------------------- remove

$failed = $false
if ($dockerRunning) {
    if ($compose) {
        Say 'Removing Immich''s containers, volumes and images...'
        # [D1] down: stops and removes containers and networks; --volumes also the named volumes
        # (pgdata, model-cache), --rmi all also the images the services use.
        Push-Location -LiteralPath $AppDir
        try {
            $old = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & $docker compose down --volumes --rmi all --remove-orphans 2>&1 | ForEach-Object { Write-Host "$_" }
            if ($LASTEXITCODE -ne 0) { $failed = $true }
            $ErrorActionPreference = $old
        } finally { Pop-Location }
    }
    # [D2] Other Immich images, for example the CLI image or the machine learning image after its
    # service was commented out.
    $ids = @((Invoke-Native $docker @('image', 'ls', '--filter', 'reference=ghcr.io/immich-app/*', '--format', '{{.ID}}')).Output -split "`n" |
        Where-Object { $_.Trim() } | Sort-Object -Unique)
    if ($ids.Count -gt 0) {
        $r = Invoke-Native $docker (@('image', 'rm') + $ids)
        if ($r.Code -ne 0) { Say 'Some Immich images are still used by other containers; they stay.' }
    }
}
function Remove-Folder([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Path) { Say "Could not remove all of $Path. Delete what is left in File Explorer."; $script:failed = $true }
    else { Say "Removed $Path" }
}
if ($upload -and -not (Test-Under $upload $AppDir)) { Remove-Folder $upload }
Remove-Folder $AppDir
if ($Dir) { Remove-Folder $Dir }
if (Test-Path -LiteralPath $KeyFile) { Remove-Item -LiteralPath $KeyFile -Force; Say "Removed $KeyFile" }

# ---------------------------------------------------------------- by hand

Say ''
Say 'To finish by hand, only if you want to:'
Say ''
Say 'Docker Desktop (skip this if you use Docker for anything else). Docker''s guide warns that'
Say 'uninstalling it deletes all Docker containers, images and volumes on this PC.'
Say '  1. Open Settings > Apps > Installed apps (Apps & features on Windows 10), select Docker Desktop,'
Say '     then Uninstall.'
Say '  2. Docker''s guide lists leftover folders you can delete afterwards:'
Say '       C:\ProgramData\Docker, C:\ProgramData\DockerDesktop, C:\Program Files\Docker,'
Say '       %LOCALAPPDATA%\Docker, %APPDATA%\Docker, %APPDATA%\Docker Desktop, %USERPROFILE%\.docker'
Say ''
Say 'Tailscale:'
Say '  1. Open Settings > Apps > Installed apps, select Tailscale, then Uninstall. Tailscale''s own docs'
Say '     have an uninstall page with the full steps; this kit did not check them.'
Say '  2. In the Tailscale admin console (https://login.tailscale.com/admin/machines), open the menu'
Say '     next to this PC and remove it.'
Say '  3. On the iPhone, press and hold the Tailscale and Immich icons and choose Remove App. That does'
Say '     not delete any photo from the iPhone.'
Say ''
Say 'Keeping the PC awake: if you ran scripts\always_on\windows.ps1 -Apply, undo it with'
Say '  powershell -ExecutionPolicy Bypass -File scripts/always_on/windows.ps1 -Undo'
if ($failed) { exit 1 }
exit 0
