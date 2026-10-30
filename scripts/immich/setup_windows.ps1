<#
Homecloud: install Immich on Windows with Docker Desktop, the way the Immich docs describe it.

Run it from the repository folder in PowerShell:
  powershell -ExecutionPolicy Bypass -File scripts\immich\setup_windows.ps1        shows the plan and asks first
  powershell -ExecutionPolicy Bypass -File scripts\immich\setup_windows.ps1 -Yes   for when you already agreed

Options
  -AppDir DIR           folder for docker-compose.yml and .env (default $env:USERPROFILE\immich-app)
  -UploadLocation DIR   folder where Immich keeps its copy of every photo (default AppDir\library)
  -Ml auto|on|off       machine learning. auto (the default) turns it off when Docker has less than
                        the 6 GB of memory that Immich asks for, as on an 8 GB laptop.
  -TimeoutSeconds N     how long to wait for Immich to answer after the start (default 900)

What it does
  1. Checks that Docker Desktop is installed and running. If not, it prints Docker's download links
     and stops.
  2. New install: downloads docker-compose.yml and example.env from the latest Immich release, writes
     .env (photo folder, a random database password, your time zone if PowerShell can name it) and
     follows Immich's Windows note: the database goes into a Docker volume named pgdata, because
     Postgres does not work on NTFS. With machine learning off, it comments out that service.
     Existing install: changes no file, unless you pass -Ml on or -Ml off.
  3. Starts Immich with "docker compose up -d", waits until it answers, and prints the address.
It never reads or changes iCloud, the Photos app or your download folder.

Sources (each command below names its source)
  [I1] Immich, Docker Compose install: https://docs.immich.app/install/docker-compose
       (text: docs/docs/partials/_docker-compose-install-steps.mdx in github.com/immich-app/immich)
  [I2] Immich requirements, including "Special requirements for Windows users":
       https://docs.immich.app/install/requirements
  [I3] Immich FAQ, "How can I disable machine learning?": https://docs.immich.app/FAQ
  [I4] Immich environment variables (TZ): https://docs.immich.app/install/environment-variables
  [I6] Immich API, GET /api/server/ping and /api/server/version: open-api/immich-openapi-specs.json
  [D1] Docker Desktop on Windows: https://docs.docker.com/desktop/setup/install/windows-install/
  [D2] Docker Desktop settings: https://docs.docker.com/desktop/settings-and-maintenance/settings/
  [D3] Restart policies: https://docs.docker.com/engine/containers/start-containers-automatically/
  [D4] .env syntax: https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/
  [D5] docker compose, docker info, docker inspect: https://docs.docker.com/reference/cli/docker/
  [P1] PowerShell: about_Execution_Policies, Invoke-WebRequest, Invoke-RestMethod, Get-CimInstance,
       Get-NetIPAddress, Get-TimeZone (learn.microsoft.com/powershell)
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [string]$AppDir = '',
    [string]$UploadLocation = '',
    [ValidateSet('auto', 'on', 'off')][string]$Ml = 'auto',
    [int]$TimeoutSeconds = 900
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest in Windows PowerShell 5.1 is slow with the progress bar

# [I1] "Download docker-compose.yml and example.env" from the release. Pinned to v3.3.5, the release
# the kit was checked against; IMMICH_VERSION=v3 in .env keeps Immich updating within version 3.
$ReleaseUrl = 'https://github.com/immich-app/immich/releases/download/v3.3.5'
# [I2] "RAM: Minimum 6GB". Docker reports a 6 GB VM as a bit less than 6,000,000,000 bytes.
$MinDockerMem = 5500000000
$MlMark = '#ML-OFF# '
$MlNote = '#ML-OFF-NOTE#'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Say([string]$Text = '') { Write-Host $Text }
function Stop-Script([string]$Text, [int]$Code = 1) { Write-Host $Text; exit $Code }

function Get-AbsolutePath([string]$Path) {
    if ($Path -eq '~') { $Path = $env:USERPROFILE }
    elseif ($Path.StartsWith('~\') -or $Path.StartsWith('~/')) { $Path = Join-Path $env:USERPROFILE $Path.Substring(2) }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-Under([string]$Path, [string]$Parent) {
    if (-not $Parent) { return $false }
    $p = $Path.TrimEnd('\') + '\'
    $q = $Parent.TrimEnd('\') + '\'
    return $p.StartsWith($q, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-Location([string]$Path, [string]$What) {
    # Keep Immich's files out of folders that sync to a cloud service (OneDrive backs up Desktop,
    # Documents and Pictures when its folder backup is on).
    $synced = @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial,
        [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('MyDocuments'),
        [Environment]::GetFolderPath('MyPictures'))
    foreach ($s in $synced) {
        if ($s -and (Test-Under $Path $s)) {
            Stop-Script "Pick another folder for $What than $Path. It is inside $s, which OneDrive can sync. A new folder directly in your user folder is a safe choice." 2
        }
    }
    if ($Path -match '\\OneDrive( - [^\\]+)?(\\|$)') {
        Stop-Script "Pick another folder for $What than $Path. It looks like a OneDrive folder." 2
    }
}

function Invoke-Native {
    # Runs a program and returns @{ Code; Output }. Windows PowerShell 5.1 turns lines that a program
    # writes to stderr into errors, so this runs with ErrorActionPreference Continue.
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

function Invoke-Shown {
    # Runs a program and shows its output as it comes. Returns the exit code.
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    return $code
}

function Format-GB([double]$Bytes) { return ('{0:N1}' -f ($Bytes / 1000000000)) }

if (-not $AppDir) { $AppDir = Join-Path $env:USERPROFILE 'immich-app' }
$AppDir = Get-AbsolutePath $AppDir
if (-not $UploadLocation) { $UploadLocation = Join-Path $AppDir 'library' }
$UploadLocation = Get-AbsolutePath $UploadLocation
Assert-Location $AppDir 'Immich (-AppDir)'
Assert-Location $UploadLocation 'the photo library (-UploadLocation)'

# ---------------------------------------------------------------- Docker Desktop

function Find-Docker {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # [D1] Per-user install: %LOCALAPPDATA%\Programs\DockerDesktop. All users: C:\Program Files\Docker\Docker.
    # The resources\bin part is the layout of the all-users install; the per-user one is assumed to match.
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe') }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe') }
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c -PathType Leaf) { return $c } }
    return $null
}

$Docker = Find-Docker
if (-not $Docker) {
    Say 'Docker Desktop is not installed. Immich runs inside it.'
    Say ''
    Say 'Download it from Docker''s install page: https://docs.docker.com/desktop/setup/install/windows-install/'
    Say '  Most PCs (Intel or AMD, x86_64): https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
    Say '  Arm PCs (early access):          https://desktop.docker.com/win/main/arm64/Docker%20Desktop%20Installer.exe'
    Say "  This PC reports: $env:PROCESSOR_ARCHITECTURE (AMD64 means x86_64)."
    Say ''
    Say 'Run the installer and keep the recommended per-user installation and WSL 2. Then open Docker Desktop'
    Say 'from the Start menu, accept the terms, and wait until it shows that the engine is running.'
    Say 'Then run this script again.'
    exit 1
}
# The Docker credential helper sits next to docker.exe, so keep that folder on PATH.
$env:PATH = (Split-Path -Parent $Docker) + ';' + $env:PATH

$r = Invoke-Native $Docker @('info')   # [D5] docker info fails while the engine is stopped
if ($r.Code -ne 0) {
    Say 'Docker Desktop is installed but not running.'
    Say 'Open Docker Desktop from the Start menu. Wait until it shows that the engine is running,'
    Say 'then run this script again.'
    exit 1
}
$r = Invoke-Native $Docker @('compose', 'version')   # [I2] Immich needs "docker compose"
if ($r.Code -ne 0) { Stop-Script 'Docker Compose is missing. Update Docker Desktop, then run this again.' 1 }
$osType = (Invoke-Native $Docker @('info', '--format', '{{.OSType}}')).Output.Trim()
if ($osType -ne 'linux') {
    Stop-Script "Docker Desktop runs '$osType' containers. Immich needs Linux containers: right-click the Docker icon in the taskbar and choose Switch to Linux containers, then run this again." 1
}
$dockerMem = 0.0
[void][double]::TryParse((Invoke-Native $Docker @('info', '--format', '{{.MemTotal}}')).Output.Trim(), [ref]$dockerMem)
$hostMem = 0.0
try { $hostMem = [double](Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory } catch { $hostMem = 0.0 }

# ---------------------------------------------------------------- helpers

$ComposeFile = Join-Path $AppDir 'docker-compose.yml'
$EnvFile = Join-Path $AppDir '.env'

function Invoke-Compose([string[]]$Arguments, [switch]$Quiet) {
    # [D5] Run docker compose in the folder that holds docker-compose.yml and .env.
    Push-Location -LiteralPath $AppDir
    try {
        if ($Quiet) { return (Invoke-Native $Docker (@('compose') + $Arguments)).Code }
        return (Invoke-Shown $Docker (@('compose') + $Arguments))
    } finally {
        Pop-Location
    }
}

function Read-Lines([string]$Path) {
    $text = [System.IO.File]::ReadAllText($Path)
    $lines = @($text -split "`r?`n")
    if ($lines.Count -eq 1 -and $lines[0] -eq '') { return , @() }
    if ($lines.Count -gt 1 -and $lines[-1] -eq '') { $lines = $lines[0..($lines.Count - 2)] }
    return , [string[]]$lines
}

function Write-Lines([string]$Path, [string[]]$Lines) {
    # UTF-8 without a byte order mark and with LF line ends, like the files from the release.
    [System.IO.File]::WriteAllText($Path, (($Lines -join "`n") + "`n"), $Utf8NoBom)
}

function Format-EnvValue([string]$Value) {
    # [D4] Unquoted values work for simple text; "Single-quoted (') values are used literally."
    if ($Value -match "['\\`r`n]") { Stop-Script "This path cannot go into .env: $Value (it contains a quote, a backslash or a line break)." 2 }
    if ($Value -match '^[A-Za-z0-9_./:@%+,=-]+$') { return $Value }
    return "'" + $Value + "'"
}

function Set-EnvValue([string[]]$Lines, [string]$Key, [string]$Value) {
    $out = New-Object System.Collections.Generic.List[string]
    $done = $false
    foreach ($line in $Lines) {
        if (-not $done -and $line -match ('^[#\s]*' + [regex]::Escape($Key) + '=')) {
            $out.Add("$Key=$Value"); $done = $true
        } else {
            $out.Add($line)
        }
    }
    if (-not $done) { $out.Add("$Key=$Value") }
    return , $out.ToArray()
}

function Get-MlState {
    $lines = Read-Lines $ComposeFile
    if (@($lines | Where-Object { $_.StartsWith($MlMark) }).Count -gt 0) { return 'off' }
    if (@($lines | Where-Object { $_ -match '^  immich-machine-learning:' }).Count -gt 0) { return 'on' }
    return 'unknown'
}

function Disable-MlService([string[]]$Lines) {
    # [I3] "comment out the immich-machine-learning section of the docker-compose.yml"
    $out = New-Object System.Collections.Generic.List[string]
    $inMl = $false
    foreach ($line in $Lines) {
        if ($line -match '^  immich-machine-learning:\s*$') {
            $inMl = $true
            $out.Add("$MlNote Homecloud turned machine learning off (Immich FAQ: How can I disable machine learning?).")
            $out.Add("$MlNote To turn it back on: powershell -ExecutionPolicy Bypass -File scripts\immich\setup_windows.ps1 -Ml on")
            $out.Add($MlMark + $line)
            continue
        }
        if ($inMl) {
            if ($line -match '^  [^ #]' -or $line -match '^[^ #]') { $inMl = $false }
            elseif ($line -match '^\s*$') { $out.Add($line); continue }
            else { $out.Add($MlMark + $line); continue }
        }
        $out.Add($line)
    }
    return , $out.ToArray()
}

function Enable-MlService([string[]]$Lines) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $Lines) {
        if ($line.StartsWith($MlNote)) { continue }
        if ($line.StartsWith($MlMark)) { $out.Add($line.Substring($MlMark.Length)); continue }
        $out.Add($line)
    }
    return , $out.ToArray()
}

function Add-PgdataVolume([string[]]$Lines) {
    # [I2] Windows note: "Add the following line to the bottom of docker-compose.yml" under volumes:,
    # next to model-cache.
    if (@($Lines | Where-Object { $_ -match '^\s+pgdata:\s*$' }).Count -gt 0) { return , $Lines }
    $lastTop = ''
    foreach ($line in $Lines) { if ($line -match '^[A-Za-z][A-Za-z0-9_-]*:') { $lastTop = $line.Trim() } }
    if ($lastTop -ne 'volumes:') {
        Stop-Script 'The downloaded docker-compose.yml does not end with its volumes: section, so the pgdata volume from Immich''s Windows note cannot be added safely. Nothing was started.' 1
    }
    return , ($Lines + @('  pgdata:'))
}

function Confirm-Plan {
    if ($Yes) { return }
    $interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
    if ($interactive) {
        $answer = Read-Host 'Continue? Type y and press Enter'
        if ($answer -match '^(y|yes)$') { return }
        Stop-Script 'Nothing was changed.' 2
    }
    Stop-Script 'Nothing was changed. Run again with -Yes after the user agrees to the plan above.' 2
}

function Get-FreeGB([string]$Path) {
    try {
        $root = [System.IO.Path]::GetPathRoot($Path)
        $drive = New-Object System.IO.DriveInfo($root)
        return (Format-GB $drive.AvailableFreeSpace)
    } catch { return '?' }
}

function Show-MlOff {
    if ($dockerMem -lt $MinDockerMem) {
        Say ("  Machine learning will be off. Docker has {0} GB of memory and Immich asks for at least 6 GB," -f (Format-GB $dockerMem))
        Say '  or runs with machine learning off on less (Immich requirements). Docker Desktop with WSL 2'
        Say '  gives Linux half of the PC''s memory by default.'
    } else {
        Say '  Machine learning will be off, as you asked with -Ml off.'
    }
    Say '  You lose: search by what is in a photo (for example "dog" or "beach"), People (faces),'
    Say '  search for text in photos, and duplicate detection. The Explore page shows little (Immich FAQ).'
    Say '  You keep: the timeline, albums, favorites, the map, search by date, place, camera or file name,'
    Say '  sharing, and backup from the Immich phone app.'
}

# ---------------------------------------------------------------- plan

$QUploadLocation = Format-EnvValue ($UploadLocation -replace '\\', '/')

$existing = $false
if ((Test-Path -LiteralPath $ComposeFile) -and (Test-Path -LiteralPath $EnvFile)) {
    $existing = $true
} elseif ((Test-Path -LiteralPath $ComposeFile) -or (Test-Path -LiteralPath $EnvFile)) {
    Stop-Script "$AppDir has only one of docker-compose.yml and .env. Fix it by hand, or pick another -AppDir." 2
} elseif ((Test-Path -LiteralPath $AppDir) -and @(Get-ChildItem -LiteralPath $AppDir -Force | Where-Object { $_.Name -notlike '*.download' }).Count -gt 0) {
    Stop-Script "$AppDir exists and is not empty. Pick another folder with -AppDir." 2
}

if ($Ml -eq 'auto') {
    if ($dockerMem -lt $MinDockerMem) { $mlWant = 'off' } else { $mlWant = 'on' }
} else {
    $mlWant = $Ml
}

if ($hostMem -gt 0) {
    Say ("Docker Desktop is running. Memory for Docker: {0} GB. Memory in this PC: {1} GB." -f (Format-GB $dockerMem), (Format-GB $hostMem))
} else {
    Say ("Docker Desktop is running. Memory for Docker: {0} GB." -f (Format-GB $dockerMem))
}
if ($mlWant -eq 'on' -and $dockerMem -lt $MinDockerMem) {
    Say 'Warning: Immich asks for at least 6 GB of memory with machine learning on (Immich requirements).'
    Say 'With WSL 2, Docker''s memory comes from the WSL settings (.wslconfig); or use -Ml off.'
}

$mlChange = ''
if ($existing) {
    $current = Get-MlState
    Say "Immich is already set up in $AppDir. Machine learning: $current."
    if ($Ml -eq 'off' -and $current -eq 'on') { $mlChange = 'off' }
    elseif ($Ml -eq 'on' -and $current -eq 'off') { $mlChange = 'on' }
    elseif ($Ml -eq 'auto' -and $current -ne $mlWant -and $current -ne 'unknown') {
        Say "Note: with this much memory the recommended setting is -Ml $mlWant. Nothing changes unless you pass it."
    }
    Say ''
    Say 'Plan:'
    if ($mlChange -eq 'off') {
        Say "  - Comment out the machine learning service in $ComposeFile."
        Show-MlOff
    } elseif ($mlChange -eq 'on') {
        Say "  - Turn the machine learning service back on in $ComposeFile."
        Say '    Then turn it on in Immich too: Administration > Settings > Machine Learning Settings.'
    } else {
        Say '  - Change no file.'
    }
    Say "  - Start Immich with: docker compose up -d (in $AppDir)."
} else {
    Say ''
    Say 'Plan:'
    Say "  - Create $AppDir and download into it, from the latest Immich release on GitHub:"
    Say "      $ReleaseUrl/docker-compose.yml"
    Say "      $ReleaseUrl/example.env (saved as .env)"
    Say "  - Set in .env: UPLOAD_LOCATION=$QUploadLocation"
    Say '                 DB_DATA_LOCATION=pgdata, a Docker volume, as Immich''s Windows note says'
    Say '                 DB_PASSWORD=<random letters and digits>, and TZ if PowerShell can name your time zone'
    Say '  - Add "pgdata:" to the volumes at the bottom of docker-compose.yml (Immich''s Windows note).'
    if ($mlWant -eq 'off') {
        Say '  - Comment out the machine learning service in docker-compose.yml (Immich FAQ).'
        Show-MlOff
    } else {
        Say '  - Keep machine learning on.'
    }
    Say '  - Start Immich with: docker compose up -d. The first start downloads about 1 GB of images.'
    $driveRoot = [System.IO.Path]::GetPathRoot($UploadLocation)
    Say ("    Free space on drive {0} now: {1} GB. Immich keeps its own copy of every photo, so the" -f $driveRoot, (Get-FreeGB $UploadLocation))
    Say '    library needs room for your photos plus 10 to 20% (Immich requirements).'
}
Say '  The containers have the restart policy "always" from Immich''s compose file, so Docker'
Say '  starts them again whenever Docker Desktop starts.'
Say ''
Confirm-Plan

# ---------------------------------------------------------------- install or update

if (-not $existing) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $createdAppDir = -not (Test-Path -LiteralPath $AppDir)
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    $tmpCompose = Join-Path $AppDir '.docker-compose.yml.download'
    $tmpEnv = Join-Path $AppDir '.example.env.download'
    $installed = $false
    try {
        Say 'Downloading docker-compose.yml and example.env...'
        # [I1] The docs use wget; Invoke-WebRequest downloads the same files [P1].
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/docker-compose.yml" -OutFile $tmpCompose
            Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/example.env" -OutFile $tmpEnv
        } catch {
            Stop-Script "Could not download Immich's files: $($_.Exception.Message). Check the internet connection and run this again." 1
        }
        $composeLines = Read-Lines $tmpCompose
        $envLines = Read-Lines $tmpEnv
        if (-not (@($composeLines | Where-Object { $_ -match '^  immich-server:' }).Count) -or
            -not (@($composeLines | Where-Object { $_ -match 'UPLOAD_LOCATION' }).Count)) {
            Stop-Script 'The downloaded docker-compose.yml does not look like Immich''s. Nothing was started.' 1
        }
        foreach ($key in @('UPLOAD_LOCATION', 'DB_DATA_LOCATION', 'DB_PASSWORD')) {
            if (-not (@($envLines | Where-Object { $_ -match ('^' + $key + '=') }).Count)) {
                Stop-Script "The downloaded example.env has no $key line. Nothing was started." 1
            }
        }

        # [I1] Step 2: "Populate UPLOAD_LOCATION", "Consider changing DB_PASSWORD ... use only the
        # characters A-Za-z0-9", "Set your timezone by uncommenting the TZ= line."
        $envLines = Set-EnvValue $envLines 'UPLOAD_LOCATION' $QUploadLocation
        # [I2] Windows: "change the bind mount to a Docker volume": DB_DATA_LOCATION=pgdata.
        $envLines = Set-EnvValue $envLines 'DB_DATA_LOCATION' 'pgdata'
        $bytes = New-Object byte[] 24
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($bytes)
        $rng.Dispose()
        $password = -join ($bytes | ForEach-Object { $_.ToString('x2') })
        $envLines = Set-EnvValue $envLines 'DB_PASSWORD' $password
        # [I4] TZ takes an identifier like Europe/Prague. .NET 6 and later (PowerShell 7) can convert
        # the Windows time zone; Windows PowerShell 5.1 cannot, so TZ then stays unset.
        $iana = $null
        $converted = $false
        try { $converted = [System.TimeZoneInfo]::TryConvertWindowsIdToIanaId((Get-TimeZone).Id, [ref]$iana) } catch { $converted = $false }
        if ($converted -and $iana -match '^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$') {
            $envLines = Set-EnvValue $envLines 'TZ' $iana
        } else {
            Say 'TZ stays unset (Immich then uses UTC as a fallback for photos without a time zone).'
        }
        $composeLines = Add-PgdataVolume $composeLines
        if ($mlWant -eq 'off') { $composeLines = Disable-MlService $composeLines }
        Write-Lines $ComposeFile $composeLines
        Write-Lines $EnvFile $envLines
        Remove-Item -LiteralPath $tmpCompose, $tmpEnv -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $UploadLocation | Out-Null
        # [D5] "docker compose config --quiet" only validates the files.
        if ((Invoke-Compose @('config', '--quiet')) -ne 0) {
            Stop-Script "Docker Compose does not accept the new files in $AppDir. Nothing was started. Remove $AppDir and run this again, or report the message above." 1
        }
        $installed = $true
        Say "Wrote $ComposeFile and $EnvFile."
        Say 'The .env file holds the database password. Only accounts that can open your user folder can read it.'
    } finally {
        Remove-Item -LiteralPath $tmpCompose, $tmpEnv -Force -ErrorAction SilentlyContinue
        if (-not $installed -and $createdAppDir -and -not (Test-Path -LiteralPath $EnvFile)) {
            Remove-Item -LiteralPath $AppDir -Force -Recurse -ErrorAction SilentlyContinue
        }
    }
} elseif ($mlChange -eq 'off') {
    Copy-Item -LiteralPath $ComposeFile -Destination (Join-Path $AppDir 'docker-compose.yml.before-ml-off') -Force
    Write-Lines $ComposeFile (Disable-MlService (Read-Lines $ComposeFile))
    if ((Invoke-Compose @('config', '--quiet')) -ne 0) { Stop-Script 'Docker Compose does not accept the change. The old file is docker-compose.yml.before-ml-off.' 1 }
} elseif ($mlChange -eq 'on') {
    Write-Lines $ComposeFile (Enable-MlService (Read-Lines $ComposeFile))
    if ((Invoke-Compose @('config', '--quiet')) -ne 0) { Stop-Script "Docker Compose does not accept the change. Check $ComposeFile." 1 }
}

Say 'Starting Immich...'
# [I1] Step 3: "docker compose up -d". --remove-orphans removes the machine learning container after
# its service was commented out ([D5] docker compose up).
$upArgs = @('up', '-d')
if ($mlChange -eq 'off') { $upArgs += '--remove-orphans' }
if ((Invoke-Compose $upArgs) -ne 0) {
    Say ''
    Say 'Docker could not start Immich (see the messages above). If a download failed, check the'
    Say 'internet connection and run this script again. It keeps the files it already wrote.'
    exit 1
}

# [I6] GET /api/server/ping answers {"res":"pong"} once the server is ready. A proxy must not handle
# this local address, so the requests below go direct.
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy
Say 'Waiting for Immich to answer at http://localhost:2283 (the first start can take a few minutes)...'
$watch = [System.Diagnostics.Stopwatch]::StartNew()
$lastShown = 0
while ($true) {
    $pong = $false
    try { $pong = ((Invoke-RestMethod -UseBasicParsing -Uri 'http://127.0.0.1:2283/api/server/ping' -TimeoutSec 5).res -eq 'pong') } catch { $pong = $false }
    if ($pong) { break }
    $waited = [int]$watch.Elapsed.TotalSeconds
    if ($waited -ge $TimeoutSeconds) {
        Say "Immich did not answer within $TimeoutSeconds seconds. Container status and the last server log lines:"
        [void](Invoke-Compose @('ps'))
        [void](Invoke-Compose @('logs', '--tail', '50', 'immich-server'))
        Say 'Run this script again to keep waiting, or report the lines above.'
        exit 1
    }
    if ($waited - $lastShown -ge 30) { $lastShown = $waited; Say "  still starting ($waited s)..." }
    Start-Sleep -Seconds 5
}
$version = ''
try {
    $v = Invoke-RestMethod -UseBasicParsing -Uri 'http://127.0.0.1:2283/api/server/version' -TimeoutSec 5
    $version = "v$($v.major).$($v.minor).$($v.patch)"
} catch { $version = '' }
Say ''
Say "Immich $version is running: http://localhost:2283"

# ---------------------------------------------------------------- after restarts

Say ''
Say 'After a restart of the PC:'
$containers = @('immich_server', 'immich_redis', 'immich_postgres')
if ((Get-MlState) -eq 'on') { $containers += 'immich_machine_learning' }
foreach ($c in $containers) {
    # [D3] restart policy "always": Docker starts the container again whenever the daemon starts.
    $r = Invoke-Native $Docker @('inspect', '-f', '{{.HostConfig.RestartPolicy.Name}}', $c)
    if ($r.Code -eq 0) { Say "  Container ${c}: restart policy ""$($r.Output.Trim())""" } else { Say "  Container ${c}: not found" }
}
# [D2] "Start Docker Desktop when you sign in to your computer" is off by default. Docker documents where
# its settings file is, but not this key, so the script only reads it and you check the switch.
$autostart = $false
$settings = Join-Path $env:APPDATA 'Docker\settings-store.json'
try {
    if ((Get-Content -LiteralPath $settings -Raw) -match '"AutoStart"\s*:\s*true') { $autostart = $true }
} catch { $autostart = $false }
if ($autostart) {
    Say '  Docker Desktop starts when you sign in (its settings file says so).'
} else {
    Say '  Docker Desktop does not seem to start when you sign in. Turn it on: Docker Desktop >'
    Say '  Settings > General > "Start Docker Desktop when you sign in to your computer".'
}
Say '  Docker Desktop starts only after someone signs in to Windows. After a restart, for example'
Say '  for a Windows update, Immich stays down until you sign in.'

# ---------------------------------------------------------------- addresses

Say ''
Say 'Addresses:'
Say '  On this PC:      http://localhost:2283'
try {
    $lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object {
        $_.IPAddress -notmatch '^(127\.|169\.254\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)' -and
        $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Tailscale'
    } | Select-Object -First 1
    if ($lan) { Say "  On your network: http://$($lan.IPAddress):2283" }
} catch { }
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
$tsExe = $null
if ($ts) { $tsExe = $ts.Source } elseif ($env:ProgramFiles -and (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'))) { $tsExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe' }
if ($tsExe) {
    $r = Invoke-Native $tsExe @('ip', '-4')   # Tailscale CLI: tailscale ip -4
    $tsIp = ($r.Output -split "`n" | Select-Object -First 1).Trim()
    if ($r.Code -eq 0 -and $tsIp) { Say "  Through Tailscale: http://${tsIp}:2283 (for the Immich phone app, see scripts\tailscale.md)" }
    else { Say '  Through Tailscale: Tailscale is installed but not connected. See scripts\tailscale.md.' }
} else {
    Say '  Through Tailscale: set it up with scripts\tailscale.md.'
}
Say "  Free space for the library ($UploadLocation): $(Get-FreeGB $UploadLocation) GB"
Say '  If Windows asks whether Docker Desktop may use the network, allow it on private networks.'

# ---------------------------------------------------------------- next steps

Say ''
Say 'Next steps (scripts\setup.ps1 does step 1 for you):'
$mlFlag = ''
if ((Get-MlState) -ne 'on') { $mlFlag = ' --ml off' }
Say '  1. Create your Immich account and the API key for the import:'
Say "       py scripts\immich\bootstrap.py --url http://localhost:2283$mlFlag"
Say '     It asks for the email and password. The key is saved for the import and never shown.'
$step = 2
if (-not $autostart) {
    Say "  $step. In Docker Desktop > Settings > General, turn on ""Start Docker Desktop when you sign in to your computer""."
    $step++
}
Say "  $step. Check the import plan: py scripts\immich\import_library.py check --dir DIR"
Say ''
Say 'Updating Immich later: Immich''s upgrade guide says "docker compose pull && docker compose up -d".'
Say 'Keep the pgdata line in .env and the pgdata volume in docker-compose.yml when you replace the files.'
