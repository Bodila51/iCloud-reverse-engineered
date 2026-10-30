<#
Homecloud: set up everything on a Windows PC with one command.

Run it from the kit folder in PowerShell:
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1        checks the PC, shows the plan and asks first
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Yes   for when you already agreed to the plan

Options
  -Yes             go ahead without asking. Pass it after the person said yes to the plan.
  -Ml auto|on|off  Immich machine learning, passed to scripts\immich\setup_windows.ps1 (default auto:
                   off when Docker has less than about 6 GB of memory)
  -AppDir DIR      folder for Immich's files and photo library (default %USERPROFILE%\immich-app)
  -NoTailscale     skip Tailscale and the phone page, for example in Offline vault mode

What it does, in this order. A second run skips each step that is already done.
  1. Checks the PC and changes nothing: Windows version, 64-bit, memory, free space,
     virtualization, WSL, Docker Desktop, Python and winget. Then it shows the plan.
  2. Python 3.12 with winget, for your user account, if py finds no Python 3.10 or newer.
  3. WSL 2 with "wsl --install --no-distribution", if Docker Desktop needs it. When Windows then
     needs a restart, the script stops with exit code 3. Run it again after the restart, and it
     goes on from there.
  4. Docker Desktop with winget, for your user account, with Docker's license accepted.
  5. Starts Docker Desktop and waits until "docker info" answers.
  6. scripts\immich\setup_windows.ps1 -Yes: Immich in Docker Desktop.
  7. scripts\immich\bootstrap.py: the Immich account and an API key. A Windows window asks for
     the email and the password. The password goes to bootstrap.py on standard input only.
  8. scripts\tailscale_setup.ps1 -Yes: Tailscale. You sign in in the browser window it opens.
  9. scripts\phone_card.py --open: a page with QR codes and the steps for the iPhone.
It never reads or changes iCloud, the Photos app, the iCloud Photos folder or your download folder.

Exit codes
  0  done; the last line is "Result: OK"
  1  a step failed; the last line says what to do
  2  nothing more was changed: no -Yes yet, or you chose No or Cancel
  3  Windows needs a restart; run the same command again after it

Sources
  [W1] winget install options (--exact, --id, --scope, --silent, --custom, --override,
       --accept-package-agreements, --accept-source-agreements):
       https://learn.microsoft.com/windows/package-manager/winget/install
  [W2] winget manifests Python.Python.3.12 (3.12.10) and Docker.DockerDesktop (4.97.0):
       https://github.com/microsoft/winget-pkgs/tree/master/manifests
  [W3] winget comes with App Installer: https://learn.microsoft.com/windows/package-manager/winget/
  [P1] Python installer options, InstallLauncherAllUsers (default 1 = needs admin):
       https://docs.python.org/3.12/using/windows.html#installing-without-ui
  [D1] Docker Desktop on Windows: requirements, per-user install (--user, no admin), installer
       flags --quiet, --accept-license, --backend=wsl-2, WSL 2.1.5 or later, "wsl --version":
       https://docs.docker.com/desktop/setup/install/windows-install/
  [L1] WSL basic commands, "wsl --install --no-distribution", "wsl --update", "wsl --version":
       https://learn.microsoft.com/windows/wsl/basic-commands
  [L2] .wslconfig, memory: "50% of total memory on Windows": https://learn.microsoft.com/windows/wsl/wsl-config
  [L3] WSL source, github.com/microsoft/WSL: src/windows/common/WslClient.cpp (wsl --install prints
       the restart message and exits 0) and wslutil.cpp (WSL 2 needs the services HNS and vmcompute,
       and vfpext from build 22621 on).
  [M1] PowerShell: Get-Credential, Start-Process -Verb RunAs, $OutputEncoding, Get-CimInstance
       (learn.microsoft.com/powershell)
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [ValidateSet('auto', 'on', 'off')][string]$Ml = 'auto',
    [string]$AppDir = '',
    [switch]$NoTailscale
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ImmichUrl = 'http://localhost:2283'
$MinFreeBytes = 10GB               # Docker Desktop, WSL and Immich's programs
$DockerWaitSeconds = 600
$MinPython = [version]'3.10'       # Path A needs 3.10; the other scripts run on 3.9
$MinWsl = [version]'2.1.5'         # [D1]
$MinPasswordLength = 8             # bootstrap.py MIN_PASSWORD
$DockerLicenseUrl = 'https://www.docker.com/legal/docker-subscription-service-agreement'
$RestartSentence = 'Restart your PC to finish the WSL install, then say "continue" to Claude Code.'

# ---------------------------------------------------------------- small helpers

function Say([string]$Text = '') { Write-Host $Text }

function Stop-Setup([string]$Text, [int]$Code = 1) {
    Write-Host $Text
    exit $Code
}

function Format-GB([double]$Bytes) { return ('{0:N1}' -f ($Bytes / 1GB)) }

function Get-AbsolutePath([string]$Path) {
    if ($Path -eq '~') { $Path = $env:USERPROFILE }
    elseif ($Path.StartsWith('~\') -or $Path.StartsWith('~/')) { $Path = Join-Path $env:USERPROFILE $Path.Substring(2) }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Invoke-Native {
    # Runs a program and returns @{ Code; Output }. Windows PowerShell 5.1 turns lines on stderr
    # into errors, so this runs with ErrorActionPreference Continue. wsl.exe writes UTF-16, which
    # arrives with NUL characters between the letters; they are removed.
    param([string]$Exe, [string[]]$Arguments = @())
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = @()
    $code = 1
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } catch {
        $out = @("$_")
        $code = 1
    } finally {
        $ErrorActionPreference = $old
    }
    if ($null -eq $code) { $code = 0 }
    return @{ Code = $code; Output = ((@($out) -join "`n") -replace "`0", '') }
}

function Invoke-Tee {
    # Runs a program, shows its output as it comes (unless -Hide) and returns @{ Code; Lines }.
    # -InputText goes to the program's standard input, -NoInput gives it an empty one.
    param([string]$Exe, [string[]]$Arguments = @(), [string]$InputText = $null, [switch]$NoInput, [switch]$Hide)
    $lines = New-Object System.Collections.Generic.List[string]
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $code = 1
    try {
        if ($null -ne $InputText -and $InputText -ne '') {
            # [M1] Windows PowerShell 5.1 sends piped text as ASCII by default, which turns
            # accented letters into "?". bootstrap.py reads one UTF-8 line.
            $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
            $InputText | & $Exe @Arguments 2>&1 | ForEach-Object { $l = "$_"; $lines.Add($l); if (-not $Hide) { Write-Host $l } }
        } elseif ($NoInput) {
            @() | & $Exe @Arguments 2>&1 | ForEach-Object { $l = "$_"; $lines.Add($l); if (-not $Hide) { Write-Host $l } }
        } else {
            & $Exe @Arguments 2>&1 | ForEach-Object { $l = "$_"; $lines.Add($l); if (-not $Hide) { Write-Host $l } }
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($null -eq $code) { $code = 0 }
    return @{ Code = $code; Lines = $lines.ToArray() }
}

function Get-VersionFromText([string]$Text) {
    # The first number like 2.6.1 in the text, or $null.
    $clean = $Text -replace "`0", ''
    if ($clean -match '(\d+)\.(\d+)\.(\d+)') { return [version]('{0}.{1}.{2}' -f $Matches[1], $Matches[2], $Matches[3]) }
    if ($clean -match '(\d+)\.(\d+)') { return [version]('{0}.{1}' -f $Matches[1], $Matches[2]) }
    return $null
}

function Get-WindowsProblem([int]$Build) {
    # [D1] Windows 10 22H2 (build 19045) or Windows 11 23H2 (build 22631) or newer.
    if ($Build -ge 22631) { return '' }
    if ($Build -ge 22000) {
        return "This PC runs Windows 11 build $Build, and Docker Desktop needs version 23H2 (build 22631) or newer, so install the updates in Settings > Windows Update and run this again."
    }
    if ($Build -ge 19045) { return '' }
    return "This PC runs Windows build $Build, and Docker Desktop needs Windows 10 version 22H2 (build 19045) or Windows 11 version 23H2 or newer, so update Windows in Settings > Windows Update and run this again."
}

function Test-EmailText([string]$Text) {
    return ($Text -match '^[^@\s]+@[^@\s.][^@\s]*$')
}

function Find-LastMatch([string[]]$Lines, [string]$Pattern) {
    # The first group of the last line that matches, or ''.
    $found = ''
    foreach ($l in $Lines) { if ($l -match $Pattern) { $found = $Matches[1] } }
    return $found
}

function Get-ComposeMl([string]$ComposeFile) {
    # setup_windows.ps1 marks a commented-out machine learning service with "#ML-OFF# ".
    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) { return 'unknown' }
    $text = [System.IO.File]::ReadAllText($ComposeFile)
    if ($text -match '(?m)^#ML-OFF# ') { return 'off' }
    if ($text -match '(?m)^  immich-machine-learning:') { return 'on' }
    return 'unknown'
}

function Get-MlFlag([string]$ComposeState, [string]$Asked) {
    # The --ml value for bootstrap.py: match Immich's setting to the compose file.
    if ($ComposeState -eq 'off') { return 'off' }
    if ($ComposeState -eq 'on' -and $Asked -eq 'on') { return 'on' }
    return ''
}

function Read-State([string]$Path) {
    $state = @{}
    try {
        foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
            if ($line -match '^([a-z_]+)=(.*)$') { $state[$Matches[1]] = $Matches[2] }
        }
    } catch { }
    return $state
}

function Write-State([string]$Path, [hashtable]$State) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $lines = @('# Homecloud setup.ps1: where the WSL install stands. Safe to delete.')
    foreach ($k in ($State.Keys | Sort-Object)) { $lines += "$k=$($State[$k])" }
    [System.IO.File]::WriteAllLines($Path, [string[]]$lines)
}

function Test-SameBoot([string]$Stored, [datetime]$Current) {
    # LastBootUpTime can move by a second or so between queries.
    if (-not $Stored) { return $false }
    $t = [datetime]::MinValue
    if (-not [datetime]::TryParse($Stored, [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind, [ref]$t)) { return $false }
    return ([math]::Abs(($t.ToUniversalTime() - $Current.ToUniversalTime()).TotalMinutes) -lt 2)
}

# ---------------------------------------------------------------- system checks

function Get-Cim([string]$Class) {
    try { return @(Get-CimInstance -ClassName $Class -ErrorAction Stop)[0] } catch { return $null }
}

function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Test-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Test-ServicePresent([string]$Name) {
    # Like WSL's own check [L3]: the service (or driver) exists.
    try {
        Add-Type -AssemblyName System.ServiceProcess -ErrorAction Stop
        $sc = New-Object System.ServiceProcess.ServiceController($Name)
        $null = $sc.Status
        return $true
    } catch { return $false }
}

function Get-WslExe {
    $sys = $env:WINDIR
    if (-not $sys) { return $null }
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        $p = Join-Path $sys 'Sysnative\wsl.exe'   # 32-bit PowerShell on 64-bit Windows
    } else {
        $p = Join-Path $sys 'System32\wsl.exe'
    }
    if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    $cmd = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-WslState([int]$Build) {
    # Returns @{ State; Version }. State: ready, old (older than 2.1.5), restart (installed, but the
    # Virtual Machine Platform is not active yet) or missing.
    $result = @{ State = 'missing'; Version = $null }
    $wsl = Get-WslExe
    if (-not $wsl) { return $result }
    $env:WSL_UTF8 = '1'
    $r = Invoke-Native $wsl @('--version')   # [D1] "If version details do not appear", it is the old inbox WSL
    if ($r.Code -ne 0) { return $result }
    $v = Get-VersionFromText $r.Output
    $result.Version = $v
    $vmp = (Test-ServicePresent 'HNS') -and (Test-ServicePresent 'vmcompute')
    if ($Build -ge 22621) { $vmp = $vmp -and (Test-ServicePresent 'vfpext') }
    if ($Build -lt 22000) { $vmp = $vmp -and (Test-ServicePresent 'LxssManager') }   # Windows 10 also needs the WSL component
    if (-not $vmp) { $result.State = 'restart'; return $result }
    if ($null -eq $v -or $v -lt $MinWsl) { $result.State = 'old'; return $result }
    $result.State = 'ready'
    return $result
}

function Update-ProcessPath {
    # Installers add folders to PATH in the registry; add the new ones to this window too.
    $have = @($env:PATH -split ';' | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\').ToLowerInvariant() })
    $add = @()
    foreach ($scope in @('User', 'Machine')) {
        $p = [Environment]::GetEnvironmentVariable('Path', $scope)
        if (-not $p) { continue }
        foreach ($entry in ($p -split ';')) {
            if (-not $entry) { continue }
            $expanded = [Environment]::ExpandEnvironmentVariables($entry)
            if ($have -notcontains $expanded.TrimEnd('\').ToLowerInvariant()) {
                $add += $expanded
                $have += $expanded.TrimEnd('\').ToLowerInvariant()
            }
        }
    }
    if ($add.Count -gt 0) { $env:PATH = ($env:PATH.TrimEnd(';') + ';' + ($add -join ';')) }
}

function Find-Python {
    # The py launcher with a Python 3.10 or newer: @{ Exe; Version }, or $null. The kit's other
    # steps type "py", so only py counts.
    $candidates = @()
    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe') }
    if ($env:WINDIR) { $candidates += (Join-Path $env:WINDIR 'py.exe') }
    foreach ($exe in $candidates) {
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
        # No quotes inside the code: Windows PowerShell 5.1 does not escape them for native programs.
        $r = Invoke-Native $exe @('-3', '-c', 'import sys; print(sys.version.split()[0])')
        if ($r.Code -ne 0) { continue }
        $v = Get-VersionFromText $r.Output
        if ($v) { return @{ Exe = $exe; Version = $v } }
    }
    return $null
}

function Get-DockerRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    try {
        $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
        # <install folder>\resources\bin\docker.exe
        if ($cmd) { $roots.Add((Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $cmd.Source)))) }
    } catch { }
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop',
                       'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop')) {
        try {
            $loc = (Get-ItemProperty -LiteralPath $key -ErrorAction Stop).InstallLocation
            if ($loc) { $roots.Add(([string]$loc).TrimEnd('\')) }
        } catch { }
    }
    # [D1] Per-user: %LOCALAPPDATA%\Programs\DockerDesktop. All users: C:\Program Files\Docker\Docker.
    if ($env:LOCALAPPDATA) { $roots.Add((Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop')) }
    if ($env:ProgramFiles) { $roots.Add((Join-Path $env:ProgramFiles 'Docker\Docker')) }
    return $roots.ToArray()
}

function Find-DockerDesktop {
    # @{ Cli; App } or $null. The per-user layout is assumed to match the all-users one.
    foreach ($root in (Get-DockerRoots)) {
        if (-not $root) { continue }
        $cli = Join-Path $root 'resources\bin\docker.exe'
        $app = Join-Path $root 'Docker Desktop.exe'
        if ((Test-Path -LiteralPath $cli -PathType Leaf) -and (Test-Path -LiteralPath $app -PathType Leaf)) {
            return @{ Cli = $cli; App = $app }
        }
    }
    return $null
}

function Find-Winget {
    $cmd = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($env:LOCALAPPDATA) {
        $p = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Get-FreeBytes([string]$Path) {
    try {
        $drive = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($Path))
        return [double]$drive.AvailableFreeSpace
    } catch { return -1 }
}

function Get-LanAddress {
    try {
        $lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object {
            $_.IPAddress -notmatch '^(127\.|169\.254\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)' -and
            $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Tailscale'
        } | Select-Object -First 1
        if ($lan) { return $lan.IPAddress }
    } catch { }
    return ''
}

# ---------------------------------------------------------------- installs

function Show-UacNote {
    if ($script:UacNoted) { return }
    $script:UacNoted = $true
    Say 'Windows may now show a User Account Control window that asks whether an app may make changes.'
    Say 'Click Yes. If you see no window, look for a flashing shield icon in the taskbar.'
}

function Invoke-Winget([string]$Winget, [string[]]$Arguments) {
    # [W1] Shows winget's output. Returns its exit code.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Winget @Arguments 2>&1 | ForEach-Object { $l = "$_"; if ($l.Trim()) { Write-Host $l } }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($null -eq $code) { $code = 0 }
    return $code
}

function Invoke-Elevated([string]$Exe, [string[]]$Arguments) {
    # Runs a program as administrator: directly when this window already is, else through a User
    # Account Control prompt [M1]. Returns the exit code, or -1 when it is not known.
    if (Test-Admin) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $Exe @Arguments 2>&1 | ForEach-Object { $l = ("$_" -replace "`0", ''); if ($l.Trim()) { Write-Host $l } }
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = $old }
        if ($null -eq $code) { $code = -1 }
        return $code
    }
    Show-UacNote
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
    } catch {
        Stop-Setup 'You chose No in the Windows permission window, so WSL was not installed; run this again and choose Yes.' 2
    }
    if ($null -eq $p -or $null -eq $p.ExitCode) { return -1 }
    return [int]$p.ExitCode
}

function Install-Python([string]$Winget) {
    Say ''
    Say 'Installing Python 3.12 for your user account with winget...'
    Show-UacNote
    # [W2] Python.Python.3.12, user scope. [P1] InstallLauncherAllUsers=0 keeps the py launcher in
    # your user folder (%LOCALAPPDATA%\Programs\Python\Launcher), so it needs no admin rights.
    $code = Invoke-Winget $Winget @('install', '--exact', '--id', 'Python.Python.3.12', '--source', 'winget',
        '--scope', 'user', '--silent', '--accept-package-agreements', '--accept-source-agreements',
        '--custom', 'InstallLauncherAllUsers=0')
    Update-ProcessPath
    $py = Find-Python
    if ($py -and $py.Version -ge $MinPython) {
        Say "Python $($py.Version) is ready."
        return $py
    }
    Stop-Setup "Python did not install (winget exit code $code); install Python 3.12 from https://www.python.org/downloads/windows/ and run this again." 1
}

function Install-Wsl([int]$Build, [string]$StateFile) {
    $wsl = Get-WslExe
    if (-not $wsl) {
        Stop-Setup 'This Windows has no wsl.exe, so install the Windows updates in Settings > Windows Update and run this again.' 1
    }
    $state = Read-State $StateFile
    $boot = $null
    $os = Get-Cim 'Win32_OperatingSystem'
    $bootValue = Get-Prop $os 'LastBootUpTime'
    if ($bootValue -is [datetime]) { $boot = $bootValue }
    $tries = 0
    [void][int]::TryParse([string]$state['wsl_install_count'], [ref]$tries)

    if ($null -ne $boot -and (Test-SameBoot $state['wsl_install_boot'] $boot)) {
        # WSL was installed earlier, and Windows has not restarted since.
        Stop-Setup $RestartSentence 3
    }
    if ($tries -ge 2) {
        Stop-Setup 'WSL 2 is still not ready after two installs and restarts, so open PowerShell as administrator, run "wsl --install --no-distribution", restart, and run this again.' 1
    }

    $current = Get-WslState $Build
    Say ''
    if ($current.State -eq 'old') {
        Say "Updating WSL from version $($current.Version) (Docker Desktop needs $MinWsl or newer)..."
        $code = Invoke-Elevated $wsl @('--update')   # [L1] [D1]
    } else {
        Say 'Installing WSL 2 with: wsl --install --no-distribution'
        $code = Invoke-Elevated $wsl @('--install', '--no-distribution')   # [L1]
        if ($code -ne 0 -and $code -ne -1 -and $code -ne 3010) {
            # [D1] The old inbox WSL knows only "wsl --update".
            Say "wsl --install ended with code $code. Trying: wsl --update"
            $code = Invoke-Elevated $wsl @('--update')
        }
    }
    $state['wsl_install_count'] = [string]($tries + 1)
    if ($null -ne $boot) { $state['wsl_install_boot'] = $boot.ToUniversalTime().ToString('o') }
    try { Write-State $StateFile $state } catch { }

    $after = Get-WslState $Build
    if ($after.State -eq 'ready') {
        Say "WSL 2 is ready (version $($after.Version))."
        return
    }
    if ($after.State -eq 'old') {
        Stop-Setup "WSL is at version $($after.Version), and Docker Desktop needs $MinWsl or newer, so open PowerShell as administrator, run ""wsl --update"", and run this again." 1
    }
    # [L3] wsl --install exits with 0 even when Windows must restart first.
    Stop-Setup $RestartSentence 3
}

function Install-DockerDesktop([string]$Winget) {
    Say ''
    Say 'Installing Docker Desktop for your user account with winget (about 600 MB)...'
    # [W2] Docker.DockerDesktop. [W1] --override hands this text to Docker's installer as it is.
    # [D1] "install --user": per-user, no admin rights. "--accept-license" accepts the Docker
    # Subscription Service Agreement now instead of at the first start. "--backend=wsl-2" is the default.
    $code = Invoke-Winget $Winget @('install', '--exact', '--id', 'Docker.DockerDesktop', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements',
        '--override', 'install --user --quiet --accept-license --backend=wsl-2')
    Update-ProcessPath
    $found = Find-DockerDesktop
    if ($found) {
        Say 'Docker Desktop is installed.'
        return $found
    }
    Stop-Setup "Docker Desktop did not install (winget exit code $code); install it from https://docs.docker.com/desktop/setup/install/windows-install/ and run this again." 1
}

function Test-DockerUp([string]$Cli) {
    return ((Invoke-Native $Cli @('info', '--format', '{{.ServerVersion}}')).Code -eq 0)
}

function Start-DockerDesktop($Docker, [int]$Seconds) {
    if (Test-DockerUp $Docker.Cli) { Say 'Docker Desktop is running.'; return }
    Say ''
    Say 'Starting Docker Desktop. The first start can take a few minutes...'
    try { Start-Process -FilePath $Docker.App | Out-Null } catch {
        Stop-Setup "Docker Desktop did not open: $($_.Exception.Message). Open it from the Start menu, then run this again." 1
    }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $shown = 0
    while (-not (Test-DockerUp $Docker.Cli)) {
        $waited = [int]$watch.Elapsed.TotalSeconds
        if ($waited -ge $Seconds) {
            Stop-Setup ("Docker Desktop did not start within {0} minutes, so open Docker Desktop from the Start menu, follow what its window shows, and run this again." -f [int]($Seconds / 60)) 1
        }
        if ($waited - $shown -ge 30) { $shown = $waited; Say "  still starting ($waited s)..." }
        Start-Sleep -Seconds 5
    }
    Say 'Docker Desktop is running.'
}

# ---------------------------------------------------------------- Immich account

function Get-WebArgs {
    # Immich runs on this PC: no proxy.
    $a = @{ UseBasicParsing = $true; TimeoutSec = 10 }
    if ($PSVersionTable.PSVersion.Major -ge 6) { $a['NoProxy'] = $true }
    else { [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy }
    return $a
}

function Get-ImmichInitialized([string]$Url) {
    # bootstrap.py's source: GET /server/config, ServerConfigDto.isInitialized (no sign-in needed).
    try {
        $wa = Get-WebArgs
        $c = Invoke-RestMethod -Uri ($Url + '/api/server/config') @wa
        $v = Get-Prop $c 'isInitialized'
        if ($v -is [bool]) { return $v }
    } catch { }
    return $null
}

function Show-CredentialDialog([string]$Message, [string]$UserName) {
    # [M1] Windows PowerShell shows this as a Windows window with a hidden password field.
    try {
        if ($UserName) { return (Get-Credential -UserName $UserName -Message $Message) }
        return (Get-Credential -Message $Message)
    } catch { return $null }
}

function Read-ImmichLogin([bool]$IsNew, [string]$DefaultEmail) {
    # Returns @{ Email; Password }. The password stays in memory.
    $email = $DefaultEmail
    $note = ''
    for ($try = 1; $try -le 3; $try++) {
        if ($IsNew) {
            $msg = $note + "Homecloud: pick the email and password for your new Immich account. You type them again on your iPhone. Use at least $MinPasswordLength characters."
        } else {
            $msg = $note + 'Homecloud: type the email and password of your Immich admin account.'
        }
        $cred = Show-CredentialDialog $msg $email
        if ($null -eq $cred) {
            Stop-Setup 'You closed the Immich password window, so the setup stopped; run this again when you are ready.' 2
        }
        # Windows PowerShell 5.1 can put a backslash in front of a name without a domain.
        $email = ([string]$cred.UserName).TrimStart('\').Trim()
        $password = $cred.GetNetworkCredential().Password
        if (-not (Test-EmailText $email)) { $note = 'That email address does not look right. '; continue }
        if ($IsNew) {
            if ($password.Length -lt $MinPasswordLength) { $note = 'That password is too short. '; continue }
            $again = Show-CredentialDialog 'Homecloud: type the same password again.' $email
            if ($null -eq $again) {
                Stop-Setup 'You closed the Immich password window, so the setup stopped; run this again when you are ready.' 2
            }
            if ($again.GetNetworkCredential().Password -cne $password) { $note = 'The two passwords were different. '; continue }
        } elseif (-not $password) {
            $note = 'The password was empty. '
            continue
        }
        return @{ Email = $email; Password = $password }
    }
    Stop-Setup 'No usable email and password after 3 tries, so run this again and type them in the Homecloud window.' 2
}

function Invoke-Bootstrap([string]$Py, [string]$Script, [string]$MlFlag, [string]$KeyFile) {
    # Returns the email of the Immich account.
    $base = @('-3', $Script, '--url', $ImmichUrl)
    if ($MlFlag) { $base += @('--ml', $MlFlag) }

    if (Test-Path -LiteralPath $KeyFile -PathType Leaf) {
        # A saved key that still works needs no password. bootstrap.py checks it; with an empty
        # standard input it cannot wait for an answer. Its messages show only when it is done.
        $r = Invoke-Tee $Py $base -NoInput -Hide
        if ($r.Code -eq 0) {
            $r.Lines | ForEach-Object { Write-Host $_ }
            $email = Find-LastMatch $r.Lines '^\s*Email:\s*(\S+@\S+)\s*$'
            if ($email) { return $email }
        } elseif ($r.Code -ne 2) {
            $r.Lines | ForEach-Object { Write-Host $_ }
            Stop-Setup 'The Immich account step failed (see the message above), so make sure Docker Desktop runs and run this again.' 1
        }
    }

    $init = Get-ImmichInitialized $ImmichUrl
    $isNew = ($init -ne $true)
    Say ''
    if ($isNew) {
        Say 'Next, a Windows window named "Windows PowerShell credential request" asks you to pick the'
        Say 'email and password for your Immich account. Then it asks for the password once more.'
    } else {
        Say 'Next, a Windows window named "Windows PowerShell credential request" asks for the email and'
        Say 'password of your Immich admin account.'
    }
    Say 'The password goes only to Immich on this PC. If you see no window, look in the taskbar.'
    $login = Read-ImmichLogin $isNew ''
    $typedEmail = $login.Email
    $r = Invoke-Tee $Py ($base + @('--email', $typedEmail, '--password-stdin')) -InputText $login.Password
    $login.Password = $null
    $login = $null
    if ($r.Code -eq 2) { Stop-Setup 'The Immich account step stopped before it finished, so run this again.' 2 }
    if ($r.Code -ne 0) {
        if ($isNew) { Stop-Setup 'The Immich account step failed (see the message above), so run this again.' 1 }
        Stop-Setup 'The Immich account step failed (see the message above), so run this again and type the password of your Immich admin account.' 1
    }
    $email = Find-LastMatch $r.Lines '^\s*Email:\s*(\S+@\S+)\s*$'
    if (-not $email) { $email = $typedEmail }
    return $email
}

# ---------------------------------------------------------------- main

function Main {
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        Stop-Setup 'This script is for Windows; on a Mac, run: bash scripts/setup.sh' 1
    }
    if ($PSVersionTable.PSEdition -eq 'Core' -and $env:WINDIR) {
        # PowerShell 7 asks Get-Credential in the console, which has no window when Claude Code runs
        # this. Windows PowerShell 5.1 shows a Windows window, so run there.
        $winPs = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if (Test-Path -LiteralPath $winPs -PathType Leaf) {
            $again = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Ml', $Ml)
            if ($Yes) { $again += '-Yes' }
            if ($AppDir) { $again += @('-AppDir', $AppDir) }
            if ($NoTailscale) { $again += '-NoTailscale' }
            & $winPs @again
            exit $LASTEXITCODE
        }
    }

    $psExe = (Get-Process -Id $PID).Path
    $immichSetup = Join-Path $PSScriptRoot 'immich\setup_windows.ps1'
    $bootstrap = Join-Path $PSScriptRoot 'immich\bootstrap.py'
    $tailscaleSetup = Join-Path $PSScriptRoot 'tailscale_setup.ps1'
    $phoneCard = Join-Path $PSScriptRoot 'phone_card.py'
    $homeDir = Join-Path $env:USERPROFILE '.homecloud'
    $stateFile = Join-Path $homeDir 'setup-windows-state.txt'
    $keyFile = Join-Path $homeDir 'immich-api-key'   # bootstrap.py and import_library.py DEFAULT_KEY_FILE

    $appDirGiven = [bool]$AppDir
    if (-not $AppDir) { $AppDir = Join-Path $env:USERPROFILE 'immich-app' }   # setup_windows.ps1 default
    $AppDir = Get-AbsolutePath $AppDir
    $composeFile = Join-Path $AppDir 'docker-compose.yml'

    $needed = @($immichSetup, $bootstrap)
    if (-not $NoTailscale) { $needed += @($tailscaleSetup, $phoneCard) }
    foreach ($f in $needed) {
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) {
            Stop-Setup "The kit is missing $f, so download the kit again and run this from its folder." 1
        }
    }

    $env:PYTHONIOENCODING = 'utf-8'
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

    # ------------------------------------------------------------ 1. checks (read-only)
    Say 'Homecloud setup for Windows. Checking this PC (this changes nothing)...'
    $os = Get-Cim 'Win32_OperatingSystem'
    $cs = Get-Cim 'Win32_ComputerSystem'
    $cpu = Get-Cim 'Win32_Processor'
    $build = 0
    [void][int]::TryParse([string](Get-Prop $os 'BuildNumber'), [ref]$build)
    if ($build -eq 0) { $build = [Environment]::OSVersion.Version.Build }
    $caption = [string](Get-Prop $os 'Caption')
    if (-not $caption) { $caption = 'Windows' }
    $displayVersion = ''
    try { $displayVersion = [string](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop).DisplayVersion } catch { }
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $ram = [double](Get-Prop $cs 'TotalPhysicalMemory')
    $versionText = $caption.Trim()
    if ($displayVersion) { $versionText += ", version $displayVersion" }
    Say "  Windows: $versionText (build $build), $arch"

    if (-not [Environment]::Is64BitOperatingSystem) {
        Stop-Setup 'This Windows is 32-bit, and Docker Desktop needs 64-bit Windows, so use another PC.' 1
    }
    $problem = Get-WindowsProblem $build
    if ($problem) { Stop-Setup $problem 1 }

    if ($ram -gt 0) {
        Say ("  Memory: {0} GB" -f (Format-GB $ram))
        if ($ram -lt 3.5GB) {
            Stop-Setup ("This PC has {0} GB of memory, too little for Docker Desktop and Immich, so use a PC with at least 8 GB." -f (Format-GB $ram)) 1
        }
    }
    $lowMemory = ($ram -gt 0 -and $ram -lt 7.5GB)

    $freeSystem = Get-FreeBytes ($env:SystemDrive + '\')
    $freeApp = Get-FreeBytes $AppDir
    $systemRoot = [System.IO.Path]::GetPathRoot($env:SystemDrive + '\')
    $appRoot = [System.IO.Path]::GetPathRoot($AppDir)
    if ($freeSystem -ge 0) { Say ("  Free space on drive {0} {1} GB" -f $systemRoot.TrimEnd('\'), (Format-GB $freeSystem)) }
    if ($appRoot -ne $systemRoot -and $freeApp -ge 0) { Say ("  Free space on drive {0} (Immich's folder): {1} GB" -f $appRoot.TrimEnd('\'), (Format-GB $freeApp)) }

    $hypervisor = Get-Prop $cs 'HypervisorPresent'
    $firmwareVt = Get-Prop $cpu 'VirtualizationFirmwareEnabled'
    if ($hypervisor -eq $true) { Say '  Virtualization: on (a hypervisor runs)' }
    elseif ($firmwareVt -eq $true) { Say '  Virtualization: on in the firmware' }
    elseif ($firmwareVt -eq $false -and $arch -eq 'AMD64') {
        Stop-Setup 'Virtualization is off in this PC''s firmware, and WSL 2 needs it, so turn on Intel VT-x or AMD-V (SVM) in the UEFI or BIOS settings and run this again (Docker''s guide: https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/topics/#virtualization).' 1
    } else { Say '  Virtualization: unknown' }

    try {
        $lanman = Get-Service -Name LanmanServer -ErrorAction Stop
        if ([string]$lanman.StartType -ne 'Automatic') {
            Say '  Note: Docker Desktop asks that the Windows service "Server" (LanmanServer) starts automatically.'
        }
    } catch { }

    $wslState = Get-WslState $build
    switch ($wslState.State) {
        'ready' { Say "  WSL 2: ready (version $($wslState.Version))" }
        'old' { Say "  WSL 2: version $($wslState.Version), needs an update to $MinWsl or newer" }
        'restart' { Say '  WSL 2: installed, but Windows needs a restart before it works' }
        default { Say '  WSL 2: not installed' }
    }

    $docker = Find-DockerDesktop
    $dockerUp = $false
    if ($docker) {
        $env:PATH = (Split-Path -Parent $docker.Cli) + ';' + $env:PATH
        $dockerUp = Test-DockerUp $docker.Cli
        if ($dockerUp) { Say '  Docker Desktop: installed and running' } else { Say '  Docker Desktop: installed, not running' }
    } else {
        Say '  Docker Desktop: not installed'
    }

    $py = Find-Python
    $needPython = ($null -eq $py -or $py.Version -lt $MinPython)
    if ($null -eq $py) { Say '  Python: not found (py)' }
    elseif ($needPython) { Say "  Python: $($py.Version), older than $MinPython" }
    else { Say "  Python: $($py.Version)" }

    $winget = Find-Winget
    if ($winget) { Say '  winget: found' } else { Say '  winget: not found' }

    $immichThere = Test-Path -LiteralPath $composeFile -PathType Leaf
    $needWsl = ($wslState.State -ne 'ready')
    $needDocker = ($null -eq $docker)
    if (($needPython -or $needDocker) -and -not $winget) {
        Stop-Setup 'winget is missing, so install App Installer from the Microsoft Store (https://apps.microsoft.com/detail/9nblggh4nns1) and run this again.' 1
    }
    if (($needDocker -or -not $immichThere) -and $freeSystem -ge 0 -and $freeSystem -lt $MinFreeBytes) {
        Stop-Setup ("Drive {0} has {1} GB free, and Docker Desktop, WSL and Immich's programs need about {2} GB, so free up space and run this again." -f $systemRoot.TrimEnd('\'), (Format-GB $freeSystem), (Format-GB $MinFreeBytes)) 1
    }

    # ------------------------------------------------------------ plan
    Say ''
    Say 'Plan:'
    $n = 1
    if ($needPython) { Say "  $n. Install Python 3.12 for your user account with winget (Python.Python.3.12)."; $n++ }
    if ($needWsl) {
        if ($wslState.State -eq 'old') { Say "  $n. Update WSL with: wsl --update (Windows asks for permission)." }
        elseif ($wslState.State -eq 'restart') { Say "  $n. Finish the WSL 2 install. Windows needs a restart for it; then you run this again." }
        else { Say "  $n. Install WSL 2 with: wsl --install --no-distribution (Windows asks for permission). Windows then usually needs a restart, and you run this again after it." }
        $n++
    }
    if ($needDocker) {
        Say "  $n. Install Docker Desktop for your user account with winget (Docker.DockerDesktop). This accepts"
        Say "     the Docker Subscription Service Agreement for you: $DockerLicenseUrl"
        Say '     Docker Desktop is free for personal use.'
        $n++
    }
    if (-not $dockerUp) { Say "  $n. Start Docker Desktop and wait until it runs."; $n++ }
    if ($immichThere) { Say "  $n. Start Immich from $AppDir (scripts\immich\setup_windows.ps1 -Yes -Ml $Ml)." }
    else {
        Say "  $n. Install Immich in $AppDir and start it (scripts\immich\setup_windows.ps1 -Yes -Ml $Ml)."
        Say '     It downloads Immich''s files from GitHub and about 1 GB of programs. Immich keeps its own copy'
        Say "     of every photo in $AppDir\library, so that drive needs room for your whole library."
    }
    $n++
    Say "  $n. Create your Immich account and an API key (scripts\immich\bootstrap.py). A Windows window asks"
    Say '     you for an email and a password. A saved key that still works needs no window.'
    $n++
    if (-not $NoTailscale) {
        Say "  $n. Set up Tailscale (scripts\tailscale_setup.ps1 -Yes). You sign in in the browser window it opens."
        $n++
        Say "  $n. Open a page with QR codes and the steps for your iPhone (scripts\phone_card.py)."
        $n++
    }
    if ($lowMemory) {
        Say ''
        Say ("Memory: this PC has {0} GB. Docker Desktop with WSL 2 gets half of it by default, so Immich" -f (Format-GB $ram))
        Say 'will likely run with machine learning off. A "memory=" line in %UserProfile%\.wslconfig can give'
        Say 'WSL more, but Windows then has less.'
    }
    Say ''

    if (-not $Yes) {
        $interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
        if ($interactive) {
            $answer = Read-Host 'Continue? Type y and press Enter'
            if ($answer -notmatch '^(y|yes)$') { Stop-Setup 'Nothing was changed.' 2 }
        } else {
            Stop-Setup 'Nothing was changed. Run again with -Yes after the person agrees to the plan above.' 2
        }
    }

    # ------------------------------------------------------------ 2-5. installs and Docker
    if ($needPython) { $py = Install-Python $winget }
    if ($needWsl) { Install-Wsl $build $stateFile }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue }
    if ($needDocker) {
        $docker = Install-DockerDesktop $winget
        $env:PATH = (Split-Path -Parent $docker.Cli) + ';' + $env:PATH
    }
    Start-DockerDesktop $docker $DockerWaitSeconds

    # ------------------------------------------------------------ 6. Immich
    Say ''
    Say 'Setting up Immich (scripts\immich\setup_windows.ps1)...'
    $immichArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $immichSetup, '-Yes', '-Ml', $Ml)
    if ($appDirGiven) { $immichArgs += @('-AppDir', $AppDir) }
    & $psExe @immichArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Stop-Setup "The Immich step stopped (exit code $code), so do what its message above says and run this again." 1
    }

    # ------------------------------------------------------------ 7. Immich account
    Say ''
    Say 'Setting up your Immich account (scripts\immich\bootstrap.py)...'
    $mlFlag = Get-MlFlag (Get-ComposeMl $composeFile) $Ml
    $email = Invoke-Bootstrap $py.Exe $bootstrap $mlFlag $keyFile

    # ------------------------------------------------------------ 8-9. Tailscale and the phone page
    if ($NoTailscale) {
        Say ''
        Say 'Skipped Tailscale and the phone page (-NoTailscale).'
        $lan = Get-LanAddress
        Say "Immich: $ImmichUrl on this PC."
        if ($lan) { Say "On your home network: http://${lan}:2283" }
        Say "Immich account: $email"
        Say 'Result: OK'
        exit 0
    }

    Say ''
    Say 'Setting up Tailscale (scripts\tailscale_setup.ps1)...'
    Show-UacNote
    $r = Invoke-Tee $psExe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tailscaleSetup, '-Yes')
    if ($r.Code -ne 0) {
        Stop-Setup "The Tailscale step stopped (exit code $($r.Code)), so do what its message above says and run this again." 1
    }
    $address = Find-LastMatch $r.Lines '^\s*Address:\s*(https?://\S+)\s*$'
    if (-not $address) {
        Stop-Setup 'The Tailscale step printed no address, so check that Tailscale shows Connected and run this again.' 1
    }

    Say ''
    Say 'Making the page for your iPhone (scripts\phone_card.py)...'
    & $py.Exe @('-3', $phoneCard, '--url', $address, '--email', $email, '--open')
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Stop-Setup "The phone page step failed (exit code $code), so run this again." 1
    }

    Say ''
    Say "Immich on this PC: $ImmichUrl"
    Say "Immich on your iPhone: $address"
    Say "Immich account: $email"
    Say 'Result: OK'
    exit 0
}

$script:UacNoted = $false
if ($MyInvocation.InvocationName -ne '.') { Main }
