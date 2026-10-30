#!/bin/bash
# Homecloud: keep a Mac laptop on the charger awake, so Immich stays reachable.
#
#   bash scripts/always_on/mac.sh              read-only: shows the settings now and what --apply changes
#   bash scripts/always_on/mac.sh --apply      lid open, screen off: the Mac does not sleep on the charger
#   bash scripts/always_on/mac.sh --lid-closed also stays awake with the lid closed (opt-in, see below)
#   bash scripts/always_on/mac.sh --undo       puts back the settings this script saved
# Add --yes to skip the "Continue?" question after you agreed to the plan.
# --apply, --lid-closed and --undo use sudo: run them in your own Terminal window, which asks
# for your Mac password.
#
# What happens with the lid closed (Apple silicon, no external display): the Mac goes to sleep,
# and Immich stops answering. Apple supports closed-lid use only with an external display, power
# and an external keyboard or mouse. No setting in "man pmset" changes that. So the default here
# is: lid open, screen off. The screen turns off after its usual time, and the Mac keeps running
# while it is on the charger.
# --lid-closed adds "pmset -a disablesleep 1". Apple's open-source pmset code has this switch,
# but "man pmset" does not describe it, so a macOS update may change it. It also keeps the Mac
# awake on battery, even in a bag. Use it only if the lid must stay closed. --undo removes it.

# Sources
#   [P1] man pmset (Apple open source, PowerManagement/pmset/pmset.1): -c is "charger (wall power)",
#        "sleep - system sleep timer (value in minutes, or 0 to disable)", -g custom, restoredefaults.
#        https://github.com/apple-oss-distributions/PowerManagement/blob/main/pmset/pmset.1
#   [P2] Apple open source pmset.m, same repository: the disablesleep argument ("Any non-zero value
#        ... means DISABLE sleep") and the SleepDisabled line of "pmset -g". Not in the man page.
#        https://github.com/apple-oss-distributions/PowerManagement/blob/main/pmset/pmset.m
#   [A1] Apple, closed-lid use with an external display: https://support.apple.com/en-us/117373
#   [A2] Apple, sleep and wake settings, "Prevent automatic sleeping on power adapter when the
#        display is off" under Battery > Options:
#        https://support.apple.com/guide/mac-help/set-sleep-and-wake-settings-mchle41a6ccd/mac
#   [A3] Apple, Optimized Battery Charging and Charge Limit (Apple silicon, macOS 26.4 or later):
#        https://support.apple.com/en-us/102338
#   [A4] Apple, automatic login is not available while FileVault is on:
#        https://support.apple.com/en-us/102316
#   [D1] Docker Desktop settings, "Start Docker Desktop when you sign in to your computer" (off by
#        default) and the settings-store.json location:
#        https://docs.docker.com/desktop/settings-and-maintenance/settings/
#   [M1] man fdesetup ("fdesetup status"), man system_profiler (SPPowerDataType)

set -euo pipefail

MODE=show
YES=0
STATE_DIR="$HOME/.homecloud"
STATE="$STATE_DIR/always_on_mac.before"
SELF="$0"

say() { printf '%s\n' "$*"; }
minutes() { if [ "$1" = 1 ]; then printf '1 minute'; else printf '%s minutes' "$1"; fi; }
die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }

for arg in "$@"; do
  case "$arg" in
    --apply) MODE=apply ;;
    --lid-closed) MODE=lid ;;
    --undo) MODE=undo ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $arg (see --help)" 2 ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || die "This script is for macOS. On Windows, use scripts/always_on/windows.ps1." 2
command -v pmset >/dev/null 2>&1 || die "pmset is missing. It comes with macOS." 2

ac_value() {  # ac_value KEY: the value for the charger, from "pmset -g custom" [P1]
  pmset -g custom 2>/dev/null | awk -v key="$1" '
    /^AC Power:/ { ac = 1; next }
    /^[A-Za-z].*:[[:space:]]*$/ { ac = 0 }
    ac && $1 == key { print $2; exit }'
}

sleep_disabled() {  # 1 or 0, from the "SleepDisabled" line of "pmset -g" [P2]
  local v
  v=$(pmset -g 2>/dev/null | awk '$1 == "SleepDisabled" { print $2; exit }')
  printf '%s\n' "${v:-0}"
}

show_status() {
  local s d
  s=$(ac_value sleep)
  d=$(ac_value displaysleep)
  say "Now, on the charger:"
  case "$s" in
    0) say "  System sleep: never (sleep 0). The Mac keeps running while the lid is open." ;;
    "") say "  System sleep: unknown. pmset -g custom shows no charger settings." ;;
    *) say "  System sleep: after $(minutes "$s") without use (sleep $s). Immich stops answering then." ;;
  esac
  case "$d" in
    0) say "  Screen: stays on (displaysleep 0)." ;;
    "") ;;
    *) say "  Screen: turns off after $(minutes "$d") (displaysleep $d). That is fine, and it saves power." ;;
  esac
  if [ "$(sleep_disabled)" = 1 ]; then
    say "  Lid closed: the Mac stays awake, also on battery (SleepDisabled 1, from pmset disablesleep)."
  else
    say "  Lid closed: the Mac sleeps. Only an external display, keyboard or mouse and the charger"
    say "  together keep it awake (Apple's closed-lid mode)."
  fi
}

show_notes() {
  local fv settings
  say ""
  say "After a restart or a power cut:"
  fv=$(fdesetup status 2>/dev/null || true)  # [M1] works without sudo
  case "$fv" in
    *"FileVault is On"*)
      say "  FileVault is on, so the Mac waits at the password screen after a restart, a power cut or"
      say "  a macOS update. Docker Desktop and Immich stay down until someone signs in." ;;
    *)
      say "  Docker Desktop starts only after someone signs in, so Immich stays down until then." ;;
  esac
  # [D1] The switch is off by default. Docker documents the file but not this key, so only read it.
  settings="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
  if grep -Eqi '"autostart"[[:space:]]*:[[:space:]]*true' "$settings" 2>/dev/null; then
    say "  Docker Desktop starts when you sign in (its settings file says so). Immich starts with it."
  else
    say "  Turn on Docker Desktop > Settings > General > \"Start Docker Desktop when you sign in to"
    say "  your computer\". It is off by default. Immich's containers start when Docker starts."
  fi
  say ""
  say "Battery: on the charger all day, the battery stays near full, which wears it faster."
  if [ "$(uname -m)" = arm64 ]; then
    say "  On macOS 26.4 or later, set a Charge Limit: System Settings > Battery, the info button next"
    say "  to Charging, then 80%. Without it, Optimized Battery Charging holds the charge at 80% when"
    say "  macOS expects a long time on the charger. Apple: https://support.apple.com/en-us/102338"
  else
    say "  Optimized Battery Charging holds the charge at 80% when macOS expects a long time on the"
    say "  charger. Apple: https://support.apple.com/en-us/102338"
  fi
  say "  Keep the Mac on a hard, open surface with the vents clear, not on a bed or in a bag."
  if command -v system_profiler >/dev/null 2>&1; then  # [M1]
    system_profiler SPPowerDataType 2>/dev/null \
      | grep -E "Cycle Count|Condition|Maximum Capacity" | sed 's/^ */  Battery now: /' || true
  fi
}

confirm() {  # confirm FLAG
  if [ "$YES" = 1 ]; then return 0; fi
  if [ -t 0 ]; then
    printf 'Continue? Type y and press Return: '
    local answer=""
    read -r answer || true
    case "$answer" in y|Y|yes|Yes|YES) return 0 ;; esac
    say "Nothing was changed."
    exit 2
  fi
  say "Nothing was changed. Run this in your own Terminal window (it asks for your Mac password):"
  say "  bash $SELF $1"
  exit 2
}

need_sudo() {  # need_sudo FLAG
  if [ ! -t 0 ] && ! sudo -n true 2>/dev/null; then
    say "Nothing was changed. This step needs your Mac password for sudo. Run it in your own"
    say "Terminal window:"
    say "  bash $SELF $1"
    exit 2
  fi
}

save_before() {  # keeps the values from before the first change, so --undo can put them back
  if [ -f "$STATE" ]; then return 0; fi
  mkdir -p "$STATE_DIR"
  {
    printf 'ac_sleep=%s\n' "$(ac_value sleep)"
    printf 'sleep_disabled=%s\n' "$(sleep_disabled)"
  } > "$STATE"
  say "Saved the old values in $STATE"
}

case "$MODE" in
  show)
    show_status
    show_notes
    say ""
    say "--apply runs:      sudo pmset -c sleep 0"
    say "                   The Mac does not sleep on the charger. Keep the lid open. [man pmset]"
    say "--lid-closed adds: sudo pmset -a disablesleep 1"
    say "                   Awake with the lid closed, also on battery. Not described in man pmset."
    say "System Settings has the same kind of switch for the lid-open case: Battery > Options >"
    say "\"Prevent automatic sleeping on power adapter when the display is off\"."
    say "Nothing was changed."
    ;;
  apply|lid)
    FLAG=--apply
    if [ "$MODE" = lid ]; then FLAG=--lid-closed; fi
    show_status
    say ""
    say "Plan:"
    say "  - sudo pmset -c sleep 0: no system sleep while on the charger. Battery settings stay as they"
    say "    are. The screen still turns off after its usual time. Keep the lid open. [man pmset]"
    if [ "$MODE" = lid ]; then
      say "  - sudo pmset -a disablesleep 1: the Mac stays awake with the lid closed, and on battery too."
      say "    Apple's pmset source code has this switch, but Apple does not document it in man pmset,"
      say "    so a macOS update may change it. A closed Mac cools less well: keep it on a hard, open"
      say "    surface, and never put it in a bag while this is on."
    fi
    say "  - The old values are saved first. To undo: bash $SELF --undo"
    say ""
    confirm "$FLAG"
    need_sudo "$FLAG"
    save_before
    sudo pmset -c sleep 0  # [P1]
    if [ "$MODE" = lid ]; then
      sudo pmset -a disablesleep 1  # [P2]
    fi
    say ""
    show_status
    show_notes
    say ""
    say "To undo: bash $SELF --undo"
    ;;
  undo)
    if [ ! -f "$STATE" ]; then
      say "No saved values in $STATE, so there is nothing to put back."
      if [ "$(sleep_disabled)" = 1 ]; then
        say "SleepDisabled is 1. To let the Mac sleep with the lid closed again, run:"
        say "  sudo pmset -a disablesleep 0"
      fi
      say "To reset every power setting to Apple's defaults instead: sudo pmset -a restoredefaults"
      exit 0
    fi
    old_sleep=$(sed -n 's/^ac_sleep=//p' "$STATE")
    old_disabled=$(sed -n 's/^sleep_disabled=//p' "$STATE")
    case "$old_disabled" in 0|1) ;; *) old_disabled=0 ;; esac
    restore_sleep=1
    case "$old_sleep" in ''|*[!0-9]*) restore_sleep=0 ;; esac
    now_disabled=$(sleep_disabled)
    say "Plan:"
    if [ "$restore_sleep" = 1 ]; then
      say "  - sudo pmset -c sleep $old_sleep (system sleep on the charger, as before)"
    else
      say "  - The saved sleep value is missing. Set sleep in System Settings > Battery yourself."
    fi
    if [ "$now_disabled" != "$old_disabled" ]; then
      say "  - sudo pmset -a disablesleep $old_disabled (closing the lid puts the Mac to sleep again)"
    fi
    say ""
    confirm --undo
    need_sudo --undo
    if [ "$restore_sleep" = 1 ]; then
      sudo pmset -c sleep "$old_sleep"  # [P1]
    fi
    if [ "$now_disabled" != "$old_disabled" ]; then
      sudo pmset -a disablesleep "$old_disabled"  # [P2]
    fi
    rm -f "$STATE"
    say ""
    show_status
    ;;
esac
