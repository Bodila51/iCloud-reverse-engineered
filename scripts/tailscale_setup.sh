#!/bin/bash
# Homecloud: set up Tailscale on this Mac and print the address for the Immich phone app.
#
# Run it from the repository folder:
#   bash scripts/tailscale_setup.sh          shows the plan and asks before it changes anything
#   bash scripts/tailscale_setup.sh --yes    for when you already agreed to the plan
#
# Options
#   --port N             Immich port in the printed address (default 2283)
#   --timeout SECONDS    how long to wait for Tailscale to start and for your sign-in (default 300)
#
# What it does
#   1. If Tailscale is missing, it downloads Tailscale's standalone app for macOS, the variant
#      Tailscale recommends, checks that Tailscale signed the package, and installs it with
#      "sudo installer". macOS asks for your Mac password.
#   2. It opens Tailscale. The first time, macOS asks you to allow Tailscale's system extension
#      and its VPN configuration. Allow both.
#   3. If this Mac is not signed in, it starts the sign-in and opens it in your browser. Sign in
#      there, or create a free account.
#   4. It waits until Tailscale is connected, then prints two lines:
#        Address: http://100.x.y.z:2283
#        Name: computer-name.tailnet-name.ts.net
# If Tailscale is already connected, it only prints these lines. It never uses or prints an auth key.
#
# Sources
#   [T1] Tailscale, three ways to run Tailscale on macOS: the standalone variant is recommended,
#        its package is https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg, never install
#        it next to the Mac App Store variant, macOS 13 or later.
#        https://tailscale.com/docs/concepts/macos-variants
#   [T2] Tailscale CLI on macOS: the CLI is the app's own binary,
#        /Applications/Tailscale.app/Contents/MacOS/Tailscale; scripts set TAILSCALE_BE_CLI=1 so it
#        acts as the CLI and does not open the app. https://tailscale.com/docs/reference/tailscale-cli
#   [T3] tailscale status --json (BackendState NeedsLogin/NeedsMachineAuth/Stopped/Starting/Running,
#        AuthURL, Self.DNSName, Self.KeyExpiry, CurrentTailnet.MagicDNSEnabled), tailscale ip -4, and
#        tailscale up (a bare "up" turns on a stopped, signed-in device; otherwise it prints
#        "To authenticate, visit:" and a URL): cmd/tailscale/cli/status.go, ip.go, up.go and
#        ipn/ipnstate/ipnstate.go in github.com/tailscale/tailscale
#   [T4] Tailscale, key expiry (180 days, Disable key expiry): https://tailscale.com/kb/1028/key-expiry
#   [M1] macOS man pages: installer(8), pkgutil(1), open(1), osascript(1), curl(1)

set -euo pipefail

YES=0
PORT=2283
TIMEOUT=300
PKG_URL="https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg"   # [T1]
ADMIN_URL="https://login.tailscale.com/admin/machines"
# The tests point these at stand-ins. Leave them unset.
APP="${HOMECLOUD_TS_APP:-/Applications/Tailscale.app}"
POLL="${HOMECLOUD_TS_POLL:-3}"

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }

usage() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --port) [ $# -ge 2 ] || die "--port needs a number." 2; PORT="$2"; shift ;;
    --port=*) PORT="${1#*=}" ;;
    --timeout) [ $# -ge 2 ] || die "--timeout needs a number of seconds." 2; TIMEOUT="$2"; shift ;;
    --timeout=*) TIMEOUT="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" 2 ;;
  esac
  shift
done

case "$PORT" in ''|*[!0-9]*) die "--port must be a number." 2 ;; esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then die "--port must be between 1 and 65535." 2; fi
case "$TIMEOUT" in ''|*[!0-9]*) die "--timeout must be a number of seconds." 2 ;; esac

if [ "$(uname -s)" != "Darwin" ] && [ "${HOMECLOUD_ALLOW_NON_MAC:-}" != "1" ]; then
  die "This script is for macOS. On Windows, run scripts/tailscale_setup.ps1." 2
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/homecloud-tailscale.XXXXXX")
UP_PID=""
cleanup() {
  if [ -n "$UP_PID" ]; then kill "$UP_PID" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# ---------------------------------------------------------------- helpers

TS=""
find_cli() {  # [T2] the app's own binary first: a tailscale command from elsewhere talks to another service
  if [ -x "$APP/Contents/MacOS/Tailscale" ]; then TS="$APP/Contents/MacOS/Tailscale"
  elif command -v tailscale >/dev/null 2>&1; then TS=$(command -v tailscale)
  else TS=""
  fi
}

ts() { TAILSCALE_BE_CLI=1 "$TS" "$@"; }  # [T2]

STATUS=""
read_status() {  # [T3] true when Tailscale answers; keeps its status JSON in STATUS
  STATUS=$(ts status --json --peers=false 2>/dev/null) || STATUS=""
  [ -n "$(field BackendState)" ]
}

field() {  # field KEY: the first value of KEY in STATUS, a string or true/false, without quotes
  # shellcheck disable=SC2020  # each of , { } becomes a line break, so every key starts a line
  printf '%s\n' "$STATUS" | tr ',{}' '\n\n\n' |
    sed -n -e 's/\\u0026/\&/g' \
      -e 's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' |
    sed -n 1p
}

print_address() {  # prints the Address and Name lines; false while Tailscale has no address
  local ip name
  ip=$(ts ip -4 2>/dev/null | sed -n 1p) || ip=""  # [T3] tailscale ip -4
  case "$ip" in [0-9]*.[0-9]*.[0-9]*.[0-9]*) ;; *) return 1 ;; esac
  name=$(field DNSName)
  name=${name%.}
  say "Address: http://$ip:$PORT"
  if [ -n "$name" ]; then
    say "Name: $name"
    if [ "$(field MagicDNSEnabled)" = "false" ]; then
      say "MagicDNS is off in your Tailscale account, so the phone cannot use the name. Use the address."
    fi
  fi
  return 0
}

confirm() {
  if [ "$YES" = 1 ]; then return 0; fi
  if [ -t 0 ]; then
    printf 'Continue? Type y and press Return: '
    local answer=""
    read -r answer || true
    case "$answer" in y|Y|yes|Yes|YES) return 0 ;; esac
    say "Nothing was changed."
    exit 2
  fi
  say "Nothing was changed. Run again with --yes after the user agrees to the plan above."
  exit 2
}

run_as_admin() {  # runs a command as administrator; macOS asks for the Mac password
  if [ "$(id -u)" = 0 ]; then "$@"; return; fi
  if [ -t 0 ] || sudo -n true 2>/dev/null; then sudo "$@"; return; fi
  # No terminal for sudo to ask in, as when Claude Code runs this script. macOS shows its own
  # password dialog instead. The password goes to macOS only, never through this script.
  cat > "$WORK/admin.applescript" <<'APPLESCRIPT'
on run argv
  set cmd to ""
  repeat with a in argv
    set cmd to cmd & quoted form of (a as text) & " "
  end repeat
  do shell script cmd with prompt "Homecloud is installing Tailscale." with administrator privileges
end run
APPLESCRIPT
  osascript "$WORK/admin.applescript" "$@" >/dev/null
}

install_tailscale() {  # [T1] the standalone package, checked, then installed with installer(8)
  local pkg="$WORK/Tailscale.pkg" sig
  say "Downloading Tailscale from $PKG_URL"
  curl -fL --retry 3 --silent --show-error -o "$pkg" "$PKG_URL" ||
    die "The download from pkgs.tailscale.com did not work. Check the internet connection and run this script again."
  sig=$(pkgutil --check-signature "$pkg" 2>&1) || sig=""  # [M1] pkgutil(1)
  case "$sig" in
    *"Developer ID Installer: Tailscale"*) ;;
    *) die "The downloaded package is not signed by Tailscale, so nothing was installed. Run this script again later." ;;
  esac
  say "Installing Tailscale. When macOS asks for your Mac password, type it there."
  run_as_admin installer -pkg "$pkg" -target / ||
    die "Tailscale was not installed. Run this script again and type your Mac password when macOS asks."
  find_cli
  [ -n "$TS" ] || die "Tailscale was installed, but it is not in $APP. Open Tailscale from Applications and run this script again."
}

open_url() {  # opens an https link in the default browser
  case "$1" in https://*) open "$1" >/dev/null 2>&1 || true ;; esac
}

up_log_url() {  # the sign-in link that "tailscale up" printed, if any
  [ -f "$WORK/up.log" ] || return 0
  sed -n 's/.*\(https:\/\/[^[:space:]]*\).*/\1/p' "$WORK/up.log" | sed -n 1p
}

up_running() { [ -n "$UP_PID" ] && kill -0 "$UP_PID" 2>/dev/null; }

minutes() { if [ "$1" -ge 60 ]; then say "$(( ($1 + 59) / 60 )) minutes"; else say "$1 seconds"; fi; }

key_expiry_hint() {  # [T4] a computer that serves photos should not be signed out after 180 days
  if [ -n "$(field KeyExpiry)" ]; then
    say "Tip: Tailscale signs this Mac out after 180 days unless you turn that off. Open $ADMIN_URL,"
    say "click the menu (...) next to this Mac and choose Disable key expiry."
  fi
}

# ---------------------------------------------------------------- already connected?

find_cli
STATE=""
if [ -n "$TS" ] && read_status; then STATE=$(field BackendState); fi
if [ "$STATE" = "Running" ] && print_address; then exit 0; fi

# ---------------------------------------------------------------- plan

say "Tailscale lets the Immich app on your phone reach this Mac, at home and away, without opening"
say "anything to the internet. The Personal plan is free."
say ""
say "Plan:"
n=1
if [ -z "$TS" ]; then
  say "  $n. Download Tailscale's standalone app for macOS from pkgs.tailscale.com, check that Tailscale"
  say "     signed it, and install it. macOS asks for your Mac password."
  n=$((n + 1))
fi
say "  $n. Open Tailscale. The first time, macOS asks you to allow its system extension and its VPN"
say "     configuration. Allow both."
n=$((n + 1))
say "  $n. Connect Tailscale. If this Mac is not signed in, a browser window opens: sign in there,"
say "     or create a free account."
n=$((n + 1))
say "  $n. Wait up to $(minutes "$TIMEOUT") for the connection, then print the address for the Immich app."
say ""
confirm

# ---------------------------------------------------------------- install and start

if [ -z "$TS" ]; then install_tailscale; fi

DEADLINE=$(( $(date +%s) + TIMEOUT ))
if [ -d "$APP" ]; then open "$APP" >/dev/null 2>&1 || true; fi

told_start=0
until read_status; do
  if [ "$told_start" = 0 ]; then
    say "Waiting for Tailscale to start. If a Tailscale window or a macOS prompt asks you to allow"
    say "the system extension or the VPN configuration, follow it and click Allow."
    told_start=1
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    die "Tailscale did not start within $(minutes "$TIMEOUT"). Open Tailscale from Applications, allow its system extension and VPN configuration when macOS asks, then run this script again."
  fi
  sleep "$POLL"
done

# ---------------------------------------------------------------- sign in and wait

DEADLINE=$(( $(date +%s) + TIMEOUT ))  # a fresh wait for the sign-in
STATE=$(field BackendState)
if [ "$STATE" != "Running" ] && [ "$STATE" != "Starting" ]; then
  # [T3] A bare "tailscale up" turns Tailscale on, and starts a sign-in when one is needed.
  : > "$WORK/up.log"
  TAILSCALE_BE_CLI=1 "$TS" up > "$WORK/up.log" 2>&1 &
  UP_PID=$!
fi

opened=""
told_login=0
told_menu=0
told_approve=0
while :; do
  if read_status; then
    STATE=$(field BackendState)
    case "$STATE" in
      Running)
        if print_address; then
          key_expiry_hint
          exit 0
        fi
        ;;
      NeedsLogin)
        url=$(field AuthURL)
        [ -n "$url" ] || url=$(up_log_url)
        if [ -n "$url" ] && [ "$url" != "$opened" ]; then
          say "Opening the Tailscale sign-in page in your browser. Sign in there, or create a free account."
          say "If no browser window opens, open this link: $url"
          open_url "$url"
          opened=$url
          told_login=1
        fi
        ;;
      NeedsMachineAuth)
        if [ "$told_approve" = 0 ]; then
          say "Your Tailscale account asks for new devices to be approved. Approve this Mac at $ADMIN_URL."
          told_approve=1
        fi
        ;;
    esac
  else
    STATE=""
  fi
  if [ -n "$UP_PID" ] && ! up_running && [ "$told_login" = 0 ] && [ "$told_menu" = 0 ] &&
     [ "$STATE" != "Running" ] && [ "$STATE" != "Starting" ]; then
    # "tailscale up" stopped without a sign-in link, for example because this Mac has settings
    # that a bare "up" does not keep. The Tailscale menu can still sign in.
    say "Click the Tailscale icon in the menu bar and choose Log in, or turn Tailscale on there."
    told_menu=1
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    case "$STATE" in
      NeedsLogin) die "You have not finished signing in to Tailscale. Sign in in the browser window, then run this script again." ;;
      NeedsMachineAuth) die "This Mac waits for approval. Approve it at $ADMIN_URL, then run this script again." ;;
      "") die "Tailscale stopped answering. Open Tailscale from Applications, then run this script again." ;;
      *) die "Tailscale is not connected after $(minutes "$TIMEOUT") (state: $STATE). Turn it on from the Tailscale icon in the menu bar, then run this script again." ;;
    esac
  fi
  sleep "$POLL"
done
