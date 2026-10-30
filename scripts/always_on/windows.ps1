<#
Homecloud: keep a Windows laptop on the charger awake, so Immich stays reachable.

Run it from the repository folder in PowerShell:
  powershell -ExecutionPolicy Bypass -File scripts\always_on\windows.ps1          read-only: the settings now and the plan
  powershell -ExecutionPolicy Bypass -File scripts\always_on\windows.ps1 -Apply   changes the "plugged in" settings
  powershell -ExecutionPolicy Bypass -File scripts\always_on\windows.ps1 -Undo    puts back the values this script saved
Add -Yes to skip the "Continue?" question after you agreed to the plan.
Add -KeepLid to -Apply to leave the lid setting as it is.

What -Apply changes, in the active power plan and only for "plugged in":
  - Sleep after: Never
  - Hibernate after: Never
  - Closing the lid: Do nothing (unless -KeepLid)
On battery, the laptop sleeps as before. The screen still turns off after its usual time.
The same settings are in Control Panel > Power Options: "Change plan settings" for sleep, and
"Choose what closing the lid does" for the lid.

Sources (each command below names its source)
  [W1] Microsoft, powercfg command-line options (/getactivescheme, /query, /setacvalueindex,
       /setactive): https://learn.microsoft.com/windows-hardware/design/device-experiences/powercfg-command-line-options
  [W2] Microsoft, Sleep idle timeout: subgroup SUB_SLEEP 238c9fa8-0aad-41ed-83f4-97be242c8f20,
       setting STANDBYIDLE 29f6c1db-86da-48c5-9fdb-f2b67b1f44da, value in seconds, 0 = never:
       https://learn.microsoft.com/windows-hardware/customize/power-settings/sleep-settings-sleep-idle-timeout
  [W3] Microsoft, Hibernate idle timeout: HIBERNATEIDLE 9d7815a6-7ee4-497e-8888-515a05f02364, seconds:
       https://learn.microsoft.com/windows-hardware/customize/power-settings/sleep-settings-hibernate-idle-timeout
  [W4] Microsoft, Lid switch close action: subgroup SUB_BUTTONS 4f971e89-eebd-4455-a8de-9e59040e7347,
       setting LIDACTION 5ca83367-6e45-459f-a27b-476b1d01c936, 0 Do nothing, 1 Sleep, 2 Hibernate,
       3 Shut down: https://learn.microsoft.com/windows-hardware/customize/power-settings/power-button-and-lid-settings-lid-switch-close-action
  [D1] Docker Desktop settings, "Start Docker Desktop when you sign in to your computer" (off by
       default), settings-store.json in %APPDATA%\Docker:
       https://docs.docker.com/desktop/settings-and-maintenance/settings/
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Undo,
    [switch]$KeepLid,
    [switch]$Yes
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$StateDir = Join-Path $env:USERPROFILE '.homecloud'
$StateFile = Join-Path $StateDir 'always_on_windows.before.json'
$Self = $PSCommandPath
if (-not $Self) { $Self = 'scripts\always_on\windows.ps1' }

$SubSleep = '238c9fa8-0aad-41ed-83f4-97be242c8f20'     # [W2] SUB_SLEEP
$SubButtons = '4f971e89-eebd-4455-a8de-9e59040e7347'   # [W4] SUB_BUTTONS
$Settings = @(
    @{ Key = 'sleep'; Label = 'Sleep after'; Sub = $SubSleep; Guid = '29f6c1db-86da-48c5-9fdb-f2b67b1f44da'; Kind = 'seconds' },     # [W2] STANDBYIDLE
    @{ Key = 'hibernate'; Label = 'Hibernate after'; Sub = $SubSleep; Guid = '9d7815a6-7ee4-497e-8888-515a05f02364'; Kind = 'seconds' }, # [W3] HIBERNATEIDLE
    @{ Key = 'lid'; Label = 'Closing the lid'; Sub = $SubButtons; Guid = '5ca83367-6e45-459f-a27b-476b1d01c936'; Kind = 'lid' }          # [W4] LIDACTION
)

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

function Get-ActiveScheme {
    # [W1] powercfg /getactivescheme prints the GUID and the name of the active power plan.
    $r = Invoke-Native 'powercfg.exe' @('/getactivescheme')
    $m = [regex]::Match($r.Output, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
    if ($r.Code -ne 0 -or -not $m.Success) { Stop-Script "powercfg /getactivescheme did not work: $($r.Output)" 1 }
    $name = ''
    $n = [regex]::Match($r.Output, '\(([^)]*)\)\s*$')
    if ($n.Success) { $name = $n.Groups[1].Value }
    return @{ Guid = $m.Value.ToLower(); Name = $name }
}

function Get-PlugValue([string]$Scheme, [hashtable]$Setting) {
    # [W1] powercfg /query SCHEME SUBGROUP SETTING ends with the current "AC" (plugged in) and "DC"
    # (battery) values, in that order, as hex numbers. The labels are translated on non-English
    # Windows, so this reads the last two hex numbers. Returns $null when the setting is not there.
    $r = Invoke-Native 'powercfg.exe' @('/query', $Scheme, $Setting.Sub, $Setting.Guid)
    if ($r.Code -ne 0) { return $null }
    $hex = [regex]::Matches($r.Output, '0x[0-9a-fA-F]+')
    if ($hex.Count -lt 2) { return $null }
    return [Convert]::ToInt64($hex[$hex.Count - 2].Value.Substring(2), 16)
}

function Set-PlugValue([string]$Scheme, [hashtable]$Setting, [long]$Value) {
    # [W1] powercfg /setacvalueindex SCHEME SUBGROUP SETTING VALUE: the value while plugged in.
    $r = Invoke-Native 'powercfg.exe' @('/setacvalueindex', $Scheme, $Setting.Sub, $Setting.Guid, "$Value")
    if ($r.Code -ne 0) {
        Say "powercfg /setacvalueindex for ""$($Setting.Label)"" did not work: $($r.Output)"
        Stop-Script 'If it says access is denied, open PowerShell with "Run as administrator" and run the same command.' 1
    }
}

function Set-ActivePlan([string]$Scheme) {
    # [W1] powercfg /setactive SCHEME applies the changed values of the active plan.
    $r = Invoke-Native 'powercfg.exe' @('/setactive', $Scheme)
    if ($r.Code -ne 0) { Stop-Script "powercfg /setactive did not work: $($r.Output)" 1 }
}

function Format-Value([hashtable]$Setting, $Value) {
    if ($null -eq $Value) { return 'not on this PC' }
    if ($Setting.Kind -eq 'lid') {
        switch ([int]$Value) {
            0 { return 'Do nothing' }
            1 { return 'Sleep' }
            2 { return 'Hibernate' }
            3 { return 'Shut down' }
            default { return "value $Value" }
        }
    }
    if ($Value -eq 0) { return 'Never' }
    $minutes = [math]::Round($Value / 60)
    if ($minutes -eq 1) { return '1 minute' }
    return "$minutes minutes"
}

function Show-Values([string]$Scheme, [string]$Title) {
    Say $Title
    $values = @{}
    foreach ($s in $Settings) {
        $v = Get-PlugValue $Scheme $s
        $values[$s.Key] = $v
        Say ('  {0,-16} {1}' -f ($s.Label + ':'), (Format-Value $s $v))
    }
    return $values
}

function Confirm-Plan([string]$Flag) {
    if ($Yes) { return }
    $interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
    if ($interactive) {
        $answer = Read-Host 'Continue? Type y and press Enter'
        if ($answer -match '^(y|yes)$') { return }
        Stop-Script 'Nothing was changed.' 2
    }
    Say 'Nothing was changed. After the user agrees to the plan above, run:'
    Stop-Script "  powershell -ExecutionPolicy Bypass -File ""$Self"" $Flag -Yes" 2
}

function Show-Notes([bool]$LidChanged) {
    Say ''
    Say 'After a restart:'
    Say '  Docker Desktop starts only after someone signs in to Windows. After a restart, for example'
    Say '  for a Windows update or after a power cut, Immich stays down until you sign in.'
    # [D1] The switch is off by default. Docker documents where the file is, but not this key,
    # so the script only reads it.
    $autostart = $false
    $file = Join-Path $env:APPDATA 'Docker\settings-store.json'
    try {
        if ((Get-Content -LiteralPath $file -Raw) -match '"AutoStart"\s*:\s*true') { $autostart = $true }
    } catch { $autostart = $false }
    if ($autostart) {
        Say '  Docker Desktop starts when you sign in (its settings file says so). Immich starts with it.'
    } else {
        Say '  Turn on Docker Desktop > Settings > General > "Start Docker Desktop when you sign in to'
        Say '  your computer". It is off by default. Immich''s containers start when Docker starts.'
    }
    Say ''
    Say 'Battery: on the charger all day, the battery stays near full, which wears it faster.'
    Say '  Windows has no charge limit that works on every laptop. Your laptop maker''s app may have'
    Say '  one, for example Conservation Mode in Lenovo Vantage, Primarily AC Use in Dell Power'
    Say '  Manager, Battery Health Charging in MyASUS, or Smart charging in the Surface app.'
    Say '  Keep the laptop on a hard, open surface with the vents clear.'
    if ($LidChanged) {
        Say '  With the lid closed, the laptop cools less well. Before you put it in a bag, open the lid'
        Say '  and shut it down or put it to sleep from the Start menu.'
    }
}

if ($Apply -and $Undo) { Stop-Script 'Use -Apply or -Undo, not both.' 2 }
if ($env:OS -ne 'Windows_NT') { Stop-Script 'This script is for Windows. On a Mac, use scripts/always_on/mac.sh.' 2 }

$plan = Get-ActiveScheme
$scheme = $plan.Guid
Say "Power plan: $($plan.Name) ($scheme)"

if ($Undo) {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        Say "No saved values in $StateFile, so there is nothing to put back."
        Say 'You can set the values by hand in Control Panel > Power Options.'
        exit 0
    }
    $saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    $names = @($saved.PSObject.Properties | ForEach-Object { $_.Name })
    if ($names -notcontains 'scheme') { Stop-Script "The file $StateFile looks wrong. Set the values in Control Panel > Power Options instead." 1 }
    $target = "$($saved.scheme)"
    $null = Show-Values $target 'Now, while plugged in:'
    Say ''
    Say 'Plan:'
    foreach ($s in $Settings) {
        if ($names -contains $s.Key) {
            Say ('  - {0}: {1} (as before)' -f $s.Label, (Format-Value $s ([long]$saved.($s.Key))))
        }
    }
    if ($target -ne $scheme) {
        Say "  The values go back into the plan that was active then ($target)."
    }
    Say ''
    Confirm-Plan '-Undo'
    foreach ($s in $Settings) {
        if ($names -contains $s.Key) { Set-PlugValue $target $s ([long]$saved.($s.Key)) }
    }
    if ($target -eq $scheme) { Set-ActivePlan $scheme }
    Remove-Item -LiteralPath $StateFile -Force
    Say ''
    $null = Show-Values $target 'Now, while plugged in:'
    exit 0
}

$before = Show-Values $scheme 'Now, while plugged in:'
$targets = @($Settings | Where-Object { -not ($KeepLid -and $_.Key -eq 'lid') -and $null -ne $before[$_.Key] })
$lidChanged = $false
foreach ($s in $targets) { if ($s.Key -eq 'lid') { $lidChanged = $true } }

Say ''
if (-not $Apply) {
    Say '-Apply would set, only while plugged in:'
} else {
    Say 'Plan, only while plugged in:'
}
foreach ($s in $targets) {
    Say ('  - {0}: {1}' -f $s.Label, (Format-Value $s 0))
}
Say '  On battery, nothing changes. The screen still turns off after its usual time.'
if ($lidChanged) {
    Say '  With the lid set to Do nothing, the laptop keeps running with the lid closed while plugged in.'
}
Say "  The old values are saved first. To undo: powershell -ExecutionPolicy Bypass -File ""$Self"" -Undo"

if (-not $Apply) {
    Show-Notes $lidChanged
    Say ''
    Say 'Nothing was changed.'
    exit 0
}

Say ''
$flag = '-Apply'
if ($KeepLid) { $flag = '-Apply -KeepLid' }
Confirm-Plan $flag

if (-not (Test-Path -LiteralPath $StateFile)) {
    $state = [ordered]@{ scheme = $scheme }
    foreach ($s in $Settings) {
        if ($null -ne $before[$s.Key]) { $state[$s.Key] = $before[$s.Key] }
    }
    if (-not (Test-Path -LiteralPath $StateDir)) { $null = New-Item -ItemType Directory -Path $StateDir }
    [System.IO.File]::WriteAllText($StateFile, ($state | ConvertTo-Json))
    Say "Saved the old values in $StateFile"
}
foreach ($s in $targets) { Set-PlugValue $scheme $s 0 }
Set-ActivePlan $scheme
Say ''
$null = Show-Values $scheme 'Now, while plugged in:'
Show-Notes $lidChanged
Say ''
Say "To undo: powershell -ExecutionPolicy Bypass -File ""$Self"" -Undo"
exit 0
