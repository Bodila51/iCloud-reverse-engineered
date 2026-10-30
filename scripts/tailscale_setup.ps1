<#
Homecloud: set up Tailscale on this Windows computer and print the address for the Immich phone app.

Run it from the repository folder in PowerShell:
  powershell -ExecutionPolicy Bypass -File scripts\tailscale_setup.ps1        shows the plan and asks first
  powershell -ExecutionPolicy Bypass -File scripts\tailscale_setup.ps1 -Yes   for when you already agreed

Options
  -Port N               Immich port in the printed address (default 2283)
  -TimeoutSeconds N     how long to wait for Tailscale to start and for your sign-in (default 300)

What it does
  1. If Tailscale is missing, it installs it with winget (package Tailscale.Tailscale) and checks that
     Tailscale signed tailscale.exe. Windows asks for permission, and maybe your password.
  2. It starts Tailscale. Its icon appears in the notification area of the taskbar.
  3. If this computer is not signed in, it starts the sign-in and opens it in your browser. Sign in
     there, or create a free account.
  4. It waits until Tailscale is connected, then prints two lines:
       Address: http://100.x.y.z:2283
       Name: computer-name.tailnet-name.ts.net
If Tailscale is already connected, it only prints these lines. It never uses or prints an auth key.

Sources
  [T1] Tailscale, install on Windows (CLI at C:\Program Files\Tailscale\tailscale.exe):
       https://tailscale.com/docs/install/windows
  [T2] winget package Tailscale.Tailscale (publisher Tailscale Inc.): github.com/microsoft/winget-pkgs,
       manifests/t/Tailscale/Tailscale; https://winstall.app/apps/Tailscale.Tailscale
  [T3] tailscale status --json (BackendState NeedsLogin/NeedsMachineAuth/Stopped/Starting/Running/
       InUseOtherUser, AuthURL, Self.DNSName, Self.KeyExpiry, CurrentTailnet.MagicDNSEnabled),
       tailscale ip -4, and tailscale up (a bare "up" turns on a stopped, signed-in device; otherwise
       it prints "To authenticate, visit:" and a URL): cmd/tailscale/cli/status.go, ip.go, up.go and
       ipn/ipnstate/ipnstate.go in github.com/tailscale/tailscale
  [T4] Tailscale, key expiry (180 days, Disable key expiry): https://tailscale.com/kb/1028/key-expiry
  [W1] winget install options (--id, --exact, --source, --silent, --accept-package-agreements,
       --accept-source-agreements): https://learn.microsoft.com/windows/package-manager/winget/install
  [P1] PowerShell: Get-AuthenticodeSignature, Start-Process, ConvertFrom-Json (learn.microsoft.com/powershell)
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [ValidateRange(1, 65535)][int]$Port = 2283,
    [ValidateRange(0, 86400)][int]$TimeoutSeconds = 300
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$WingetId = 'Tailscale.Tailscale'   # [T2]
$AdminUrl = 'https://login.tailscale.com/admin/machines'
$PollSeconds = 3

function Say([string]$Text = '') { Write-Host $Text }
function Stop-Script([string]$Text, [int]$Code = 1) { Write-Host $Text; exit $Code }

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

function Get-Prop($Object, [string]$Name) {
    # Reads a property that may be missing, which strict mode does not allow with $Object.Name.
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($p) { return $p.Value }
    return $null
}

function Find-Tailscale {
    # [T1] The install folder first: right after winget, this window's PATH does not have it yet.
    $candidates = @()
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe') }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($pf86) { $candidates += (Join-Path $pf86 'Tailscale IPN\tailscale.exe') }
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    $cmd = Get-Command tailscale -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

function Read-Status {
    # [T3] Returns the status object, or $null while the Tailscale service does not answer.
    $r = Invoke-Native $script:Ts @('status', '--json', '--peers=false')
    if ($r.Code -ne 0 -or -not $r.Output) { return $null }
    try { $st = $r.Output | ConvertFrom-Json } catch { return $null }
    if (-not (Get-Prop $st 'BackendState')) { return $null }
    return $st
}

function Write-Address($Status) {
    # Prints the Address and Name lines. Returns $false while Tailscale has no address.
    $r = Invoke-Native $script:Ts @('ip', '-4')   # [T3] tailscale ip -4
    $ip = ''
    if ($r.Code -eq 0 -and $r.Output) { $ip = ($r.Output -split "`n")[0].Trim() }
    if ($ip -notmatch '^\d+\.\d+\.\d+\.\d+$') { return $false }
    Say "Address: http://${ip}:$Port"
    $name = [string](Get-Prop (Get-Prop $Status 'Self') 'DNSName')
    $name = $name.TrimEnd('.')
    if ($name) {
        Say "Name: $name"
        if ((Get-Prop (Get-Prop $Status 'CurrentTailnet') 'MagicDNSEnabled') -eq $false) {
            Say 'MagicDNS is off in your Tailscale account, so the phone cannot use the name. Use the address.'
        }
    }
    return $true
}

function Write-KeyExpiryHint($Status) {
    # [T4] A computer that serves photos should not be signed out after 180 days.
    if (Get-Prop (Get-Prop $Status 'Self') 'KeyExpiry') {
        Say "Tip: Tailscale signs this computer out after 180 days unless you turn that off. Open $AdminUrl,"
        Say 'click the menu (...) next to this computer and choose Disable key expiry.'
    }
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

function Get-Minutes([int]$Seconds) {
    if ($Seconds -ge 60) { return ('{0} minutes' -f [math]::Ceiling($Seconds / 60)) }
    return ('{0} seconds' -f $Seconds)
}

function Open-Url([string]$Url) {
    if ($Url -match '^https://\S+$') {
        try { Start-Process $Url } catch { Write-Verbose 'The browser did not open.' }
    }
}

function Install-Tailscale {
    # [T2] [W1] winget installs Tailscale's own installer; Windows asks for permission (UAC).
    $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) {
        Stop-Script 'winget is missing. Install or update App Installer from the Microsoft Store, then run this script again.' 1
    }
    Say "Installing Tailscale with winget ($WingetId). When Windows asks for permission, click Yes."
    $code = Invoke-Shown $winget.Source @('install', '--id', $WingetId, '--exact', '--source', 'winget',
        '--silent', '--accept-package-agreements', '--accept-source-agreements')
    $script:Ts = Find-Tailscale
    if (-not $script:Ts) {
        Stop-Script "Tailscale was not installed (winget exit code $code). Run this script again and click Yes when Windows asks." 1
    }
    # Check that Tailscale signed the program before this script runs it.
    $sig = Get-AuthenticodeSignature -FilePath $script:Ts
    $subject = ''
    if ($sig.SignerCertificate) { $subject = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne 'Valid' -or $subject -notmatch 'Tailscale') {
        Stop-Script "$($script:Ts) is not signed by Tailscale, so this script stops here. Uninstall Tailscale in Settings > Apps and run this script again." 1
    }
}

# ---------------------------------------------------------------- already connected?

$script:Ts = Find-Tailscale
$status = $null
if ($script:Ts) { $status = Read-Status }
if ($status -and (Get-Prop $status 'BackendState') -eq 'Running') {
    if (Write-Address $status) { exit 0 }
}

# ---------------------------------------------------------------- plan

Say 'Tailscale lets the Immich app on your phone reach this computer, at home and away, without opening'
Say 'anything to the internet. The Personal plan is free.'
Say ''
Say 'Plan:'
$n = 1
if (-not $script:Ts) {
    Say "  $n. Install Tailscale with winget ($WingetId) and check that Tailscale signed it. Windows asks"
    Say '     for permission, and maybe your password.'
    $n++
}
Say "  $n. Start Tailscale. Its icon appears in the notification area of the taskbar."
$n++
Say "  $n. Connect Tailscale. If this computer is not signed in, a browser window opens: sign in there,"
Say '     or create a free account.'
$n++
Say "  $n. Wait up to $(Get-Minutes $TimeoutSeconds) for the connection, then print the address for the Immich app."
Say ''
Confirm-Plan

# ---------------------------------------------------------------- install and start

if (-not $script:Ts) { Install-Tailscale }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ('homecloud-tailscale-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -Force | Out-Null
$upProc = $null
try {
    $gui = Join-Path (Split-Path -Parent $script:Ts) 'tailscale-ipn.exe'
    if ((Test-Path -LiteralPath $gui) -and -not (Get-Process -Name 'tailscale-ipn' -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $gui
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $told = $false
    while (-not ($status = Read-Status)) {
        if (-not $told) { Say 'Waiting for the Tailscale service to start.'; $told = $true }
        if ((Get-Date) -ge $deadline) {
            Stop-Script "Tailscale did not start within $(Get-Minutes $TimeoutSeconds). Restart the computer, then run this script again." 1
        }
        Start-Sleep -Seconds $PollSeconds
    }

    # ------------------------------------------------------------ sign in and wait

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $state = [string](Get-Prop $status 'BackendState')
    if ($state -ne 'Running' -and $state -ne 'Starting') {
        # [T3] A bare "tailscale up" turns Tailscale on, and starts a sign-in when one is needed.
        $upProc = Start-Process -FilePath $script:Ts -ArgumentList 'up' -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $work 'up.out') -RedirectStandardError (Join-Path $work 'up.err')
    }

    $opened = ''
    $toldLogin = $false
    $toldMenu = $false
    $toldApprove = $false
    $toldOther = $false
    while ($true) {
        $status = Read-Status
        $state = ''
        if ($status) {
            $state = [string](Get-Prop $status 'BackendState')
            if ($state -eq 'Running') {
                if (Write-Address $status) { Write-KeyExpiryHint $status; exit 0 }
            } elseif ($state -eq 'NeedsLogin') {
                $url = [string](Get-Prop $status 'AuthURL')
                if (-not $url) {
                    foreach ($f in @('up.err', 'up.out')) {
                        $p = Join-Path $work $f
                        if (Test-Path -LiteralPath $p) {
                            $m = Select-String -LiteralPath $p -Pattern 'https://\S+' | Select-Object -First 1
                            if ($m) { $url = $m.Matches[0].Value; break }
                        }
                    }
                }
                if ($url -and $url -ne $opened) {
                    Say 'Opening the Tailscale sign-in page in your browser. Sign in there, or create a free account.'
                    Say "If no browser window opens, open this link: $url"
                    Open-Url $url
                    $opened = $url
                    $toldLogin = $true
                }
            } elseif ($state -eq 'NeedsMachineAuth' -and -not $toldApprove) {
                Say "Your Tailscale account asks for new devices to be approved. Approve this computer at $AdminUrl."
                $toldApprove = $true
            } elseif ($state -eq 'InUseOtherUser' -and -not $toldOther) {
                Say 'Tailscale on this computer is signed in by another Windows user. Sign in to Windows as that user, or log out of Tailscale there.'
                $toldOther = $true
            }
        }
        if ($upProc -and $upProc.HasExited -and -not $toldLogin -and -not $toldMenu -and
            $state -ne 'Running' -and $state -ne 'Starting') {
            # "tailscale up" stopped without a sign-in link, for example because this computer has
            # settings that a bare "up" does not keep. The Tailscale icon can still sign in.
            Say 'Click the Tailscale icon in the notification area of the taskbar and choose Log in, or Connect.'
            $toldMenu = $true
        }
        if ((Get-Date) -ge $deadline) {
            switch ($state) {
                'NeedsLogin' { Stop-Script 'You have not finished signing in to Tailscale. Sign in in the browser window, then run this script again.' 1 }
                'NeedsMachineAuth' { Stop-Script "This computer waits for approval. Approve it at $AdminUrl, then run this script again." 1 }
                '' { Stop-Script 'Tailscale stopped answering. Restart the computer, then run this script again.' 1 }
                default { Stop-Script "Tailscale is not connected after $(Get-Minutes $TimeoutSeconds) (state: $state). Connect it from the Tailscale icon in the taskbar, then run this script again." 1 }
            }
        }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if ($upProc -and -not $upProc.HasExited) {
        try { $upProc.Kill() } catch { Write-Verbose 'tailscale up had already stopped.' }
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
