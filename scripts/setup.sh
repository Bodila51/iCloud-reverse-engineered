#!/bin/bash
# Homecloud: set up everything on a Mac with one command.
#
#   bash scripts/setup.sh          checks this Mac, shows the plan and asks once
#   bash scripts/setup.sh --yes    for when you already agreed to the plan
#
# Options
#   --ml auto|on|off   machine learning in Immich, passed on to scripts/immich/setup_mac.sh.
#                      auto (the default) turns it off when Docker has less than about 6 GB.
#   --app-dir DIR      folder for Immich's files (default ~/immich-app)
#   --no-tailscale     skip Tailscale. The phone card then uses this Mac's Wi-Fi address.
#
# Steps (a second run skips what is done)
#   1. Check this Mac: macOS, chip, memory, free disk, Python. Read-only.
#   2. Docker Desktop: install it from the command line if it is missing, give it enough
#      memory for Immich, start it and wait until it runs.
#   3. Immich: bash scripts/immich/setup_mac.sh --yes
#   4. Immich account and API key: python3 scripts/immich/bootstrap.py --url http://localhost:2283
#   5. Tailscale: bash scripts/tailscale_setup.sh --yes
#   6. Phone card: python3 scripts/phone_card.py --url ADDRESS --email EMAIL --open
# The last line is "Result: OK" when everything worked. Otherwise the script exits with a
# non-zero code and its last line says what to do.
#
# Sources
#   [D1] Docker, Install Docker Desktop on Mac, "Install from the command line" and "Installer
#        flags" (--accept-license, --user=<username>), download links for Apple silicon and Intel:
#        https://docs.docker.com/desktop/setup/install/mac-install/
#   [D2] Docker, Change your Docker Desktop settings: settings-store.json is in
#        ~/Library/Group Containers/group.com.docker/, and the memory limit "Defaults to 50% of
#        your host's memory". Docker does not document the file's keys. MemoryMiB is the key
#        Docker Desktop writes for Settings > Resources > Memory limit.
#        https://docs.docker.com/desktop/settings-and-maintenance/settings/
#   [D3] Docker, Mac permission requirements, "Installing from the command line" (--user):
#        https://docs.docker.com/desktop/setup/install/mac-permission-requirements/
#   [I1] Immich requirements, RAM: https://docs.immich.app/install/requirements
#   [M1] macOS man pages: sw_vers(1), sysctl(8), df(1), hdiutil(1), open(1), osascript(1),
#        sudo(8), xcode-select(1), ipconfig(8), pgrep(1)

set -euo pipefail

YES=0
ML=auto
APP_DIR_ARG=""
TAILSCALE=1
IMMICH_URL="http://localhost:2283"
PING_URL="http://127.0.0.1:2283/api/server/ping"
SETTINGS="$HOME/Library/Group Containers/group.com.docker/settings-store.json"  # [D2]
SETTINGS_BACKUP="$SETTINGS.homecloud-backup"
KEY_FILE="$HOME/.homecloud/immich-api-key"  # DEFAULT_KEY_FILE in scripts/immich/import_library.py
MIN_RAM_MIB=8192
MIN_FREE_GB=10
MIN_MACOS=14
# Waiting times in seconds. The environment can change them, for example for tests.
DOCKER_WAIT=${HOMECLOUD_DOCKER_WAIT:-300}
QUIT_WAIT=${HOMECLOUD_QUIT_WAIT:-90}
POLL=${HOMECLOUD_POLL:-3}

say() { printf '%s\n' "$*"; }
usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }
bad_usage() { printf '%s\n' "$1" >&2; exit 2; }
fail() {  # fail "one plain sentence for the person" [exit code]
  say ""
  say "Result: failed"
  say "$1"
  exit "${2:-1}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --ml) [ $# -ge 2 ] || bad_usage "--ml needs auto, on or off."; ML=$2; shift ;;
    --ml=*) ML=${1#*=} ;;
    --app-dir) [ $# -ge 2 ] || bad_usage "--app-dir needs a folder."; APP_DIR_ARG=$2; shift ;;
    --app-dir=*) APP_DIR_ARG=${1#*=} ;;
    --no-tailscale) TAILSCALE=0 ;;
    -h|--help) usage; exit 0 ;;
    *) bad_usage "Unknown option: $1 (see --help)" ;;
  esac
  shift
done
case "$ML" in auto|on|off) ;; *) bad_usage "--ml must be auto, on or off." ;; esac

[ "$(uname -s)" = Darwin ] || fail "This setup is for macOS, so on Windows run scripts\setup.ps1 instead." 2
[ "$(id -u)" != 0 ] || fail "Run this setup as yourself, without sudo, and it asks for your password only where it needs it." 2
ME=$(id -un)

absolute_path() {  # expands ~ and makes a path absolute, like scripts/immich/setup_mac.sh
  local p=$1
  # shellcheck disable=SC2088  # matches a literal ~ that the shell did not expand
  case "$p" in "~") p=$HOME ;; "~/"*) p="$HOME/${p#\~/}" ;; esac
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p=${p%/}; done
  printf '%s\n' "$p"
}
if [ -n "$APP_DIR_ARG" ]; then
  APP_DIR_ARG=$(absolute_path "$APP_DIR_ARG")
  APP_DIR=$APP_DIR_ARG
else
  APP_DIR="$HOME/immich-app"  # the default of scripts/immich/setup_mac.sh
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
SETUP_IMMICH=scripts/immich/setup_mac.sh
BOOTSTRAP=scripts/immich/bootstrap.py
TS_SETUP=scripts/tailscale_setup.sh
PHONE_CARD=scripts/phone_card.py

TMP=""
cleanup() { if [ -n "$TMP" ]; then rm -rf "$TMP"; fi; }
trap cleanup EXIT
TMP=$(mktemp -d "${TMPDIR:-/tmp}/homecloud-setup.XXXXXX")

# ---------------------------------------------------------------- helpers

gib() { awk -v m="$1" 'BEGIN { printf "%.1f", m / 1024 }'; }  # MiB to GB, as macOS shows memory

python_ok() {  # python3 3.9 or newer works
  # Until Apple's Command Line Tools are installed, /usr/bin/python3 only opens an install window.
  if [ "$(command -v python3 2>/dev/null)" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
    return 1
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_docker_app() {
  local a
  for a in /Applications/Docker.app "$HOME/Applications/Docker.app"; do
    if [ -d "$a" ]; then printf '%s\n' "$a"; return 0; fi
  done
  return 1
}

find_docker() {  # the docker command, like scripts/immich/setup_mac.sh finds it [D3]
  if command -v docker >/dev/null 2>&1; then command -v docker; return 0; fi
  local c
  for c in "$HOME/.docker/bin/docker" /usr/local/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker \
    "$HOME/Applications/Docker.app/Contents/Resources/bin/docker"; do
    if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

docker_running() { local d; d=$(find_docker) || return 1; "$d" info >/dev/null 2>&1; }

docker_mem_mib() {  # memory of Docker's Linux VM in MiB, 0 if unknown
  local d v
  d=$(find_docker) || { echo 0; return 0; }
  v=$("$d" info --format '{{.MemTotal}}' 2>/dev/null || true)
  case "$v" in ''|*[!0-9]*) v=0 ;; esac
  echo $((v / 1048576))
}

docker_mem_ok() {  # docker_mem_ok MIB: the VM reports a bit less than its setting
  [ "$1" -ge $((NEED_MIB * 85 / 100)) ]
}

configured_mib() {  # MemoryMiB from Docker Desktop's settings file, empty when it has none
  [ -f "$SETTINGS" ] || return 0
  python3 -I - "$SETTINGS" 2>/dev/null <<'PY' || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        value = json.load(f).get("MemoryMiB")
except Exception:
    sys.exit(0)
if isinstance(value, int) and not isinstance(value, bool):
    print(value)
PY
}

set_memory_mib() {  # set_memory_mib MIB: changes only MemoryMiB and keeps a copy of the file
  local mib=$1 dir
  dir=$(dirname "$SETTINGS")
  if [ ! -f "$SETTINGS" ] || [ ! -w "$SETTINGS" ] || [ ! -w "$dir" ]; then
    say "Note: Docker's settings file is missing or read-only, so its memory stays as it is."
    return 1
  fi
  if [ ! -e "$SETTINGS_BACKUP" ]; then cp -p "$SETTINGS" "$SETTINGS_BACKUP"; fi
  if ! python3 -I - "$SETTINGS" "$mib" <<'PY'
import json, os, sys, tempfile
path, mib = sys.argv[1], int(sys.argv[2])
with open(path, encoding="utf-8") as f:
    data = json.load(f)
if not isinstance(data, dict):
    sys.exit("Docker's settings file does not hold a JSON object.")
if data.get("MemoryMiB") == mib:
    sys.exit(0)
data["MemoryMiB"] = mib
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings-store.", suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    with open(tmp, encoding="utf-8") as f:
        json.load(f)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)
except BaseException:
    os.unlink(tmp)
    raise
PY
  then
    say "Note: Docker's settings file could not be changed, so its memory stays as it is."
    return 1
  fi
  say "Docker Desktop memory set to $(gib "$mib") GB (MemoryMiB $mib). The old file is ~${SETTINGS_BACKUP#"$HOME"}"
}

docker_processes() {  # true while any part of Docker Desktop still runs
  pgrep -f "$DOCKER_APP/Contents/MacOS/" >/dev/null 2>&1
}

quit_docker() {
  say "Quitting Docker Desktop to change its memory..."
  osascript -e 'tell application id "com.docker.docker" to quit' >/dev/null 2>&1 || true
  local start=$SECONDS
  while docker_processes; do
    if [ $((SECONDS - start)) -ge "$QUIT_WAIT" ]; then
      fail "Docker Desktop did not quit, so quit it from the whale icon in the menu bar and run this setup again."
    fi
    sleep "$POLL"
  done
}

start_docker() {
  say "Starting Docker Desktop..."
  open -a "$DOCKER_APP" >/dev/null 2>&1 || open -a Docker >/dev/null 2>&1 \
    || fail "Docker Desktop did not open, so open Docker from Applications and run this setup again."
  local start=$SECONDS last=0 waited
  while ! docker_running; do
    waited=$((SECONDS - start))
    if [ "$waited" -ge "$DOCKER_WAIT" ]; then
      fail "Docker Desktop did not start within $(((DOCKER_WAIT + 59) / 60)) minutes, so open Docker from Applications, wait until it shows that the engine is running, and run this setup again."
    fi
    if [ $((waited - last)) -ge 30 ]; then last=$waited; say "  still starting ($waited s)..."; fi
    sleep "$POLL"
  done
  say "Docker Desktop is running."
}

immich_up() { curl -fsS --noproxy '*' --max-time 5 "$PING_URL" 2>/dev/null | grep -q pong; }

ml_state() {  # on, off or none, from docker-compose.yml (setup_mac.sh comments the service out)
  local f="$APP_DIR/docker-compose.yml"
  if [ ! -f "$f" ]; then echo none
  elif grep -q '^  immich-machine-learning:' "$f"; then echo on
  else echo off
  fi
}

immich_email() {  # prints the account email when the saved API key works; fails otherwise
  # The key stays inside Python: it is never printed and never on a command line.
  python3 -I - "$IMMICH_URL" "$KEY_FILE" 2>/dev/null <<'PY'
import json, sys, urllib.request
url, path = sys.argv[1].rstrip("/"), sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        key = f.read().strip()
except OSError:
    sys.exit(1)
if not key:
    sys.exit(1)
req = urllib.request.Request(url + "/api/users/me", headers={"x-api-key": key, "Accept": "application/json"})
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(req, timeout=10) as resp:
        me = json.loads(resp.read().decode("utf-8"))
except Exception:
    sys.exit(1)
print(me.get("email") or "" if isinstance(me, dict) else "")
PY
}

# [D1] "sudo hdiutil attach Docker.dmg", "sudo /Volumes/Docker/Docker.app/Contents/MacOS/install"
# and "sudo hdiutil detach /Volumes/Docker", with the installer flags --accept-license and
# --user=<username>. It runs as one root shell, so macOS asks for the password once. The mount
# point comes from hdiutil's output, in case another volume named Docker is already mounted.
# System folders come first on PATH, so nothing else can stand in for hdiutil or awk.
INSTALL_SNIPPET=$(cat <<'SH'
PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
dmg=$1 user=$2 HOME=$3
export PATH HOME
out=$(hdiutil attach -nobrowse "$dmg") || exit 3
mnt=$(printf '%s\n' "$out" | awk -F '\t' 'NF >= 3 && $NF ~ /^\// { v = $NF } END { sub(/[ \t]+$/, "", v); print v }')
if [ -z "$mnt" ] || [ ! -x "$mnt/Docker.app/Contents/MacOS/install" ]; then
  [ -z "$mnt" ] || hdiutil detach "$mnt" -quiet
  exit 4
fi
"$mnt/Docker.app/Contents/MacOS/install" --accept-license --user="$user"
status=$?
hdiutil detach "$mnt" -quiet || hdiutil detach -force "$mnt" -quiet || true
exit "$status"
SH
)

install_docker() {
  local url="https://desktop.docker.com/mac/main/$DMG_ARCH/Docker.dmg" dmg="$TMP/Docker.dmg" err="$TMP/install.err" status=0
  say "Downloading Docker Desktop for $CHIP from docker.com. It is a large file and can take a few minutes..."
  curl -fsSL --retry 3 -o "$dmg" "$url" \
    || fail "The Docker Desktop download did not finish, so check the internet connection and run this setup again."
  say "macOS now asks for your Mac password once, to install Docker Desktop in Applications."
  if [ -t 0 ] || sudo -n true 2>/dev/null; then
    sudo /bin/sh -c "$INSTALL_SNIPPET" homecloud "$dmg" "$ME" "$HOME" 2>"$err" || status=$?
  else
    # No terminal for sudo to ask in (for example inside Claude Code): macOS shows its own
    # password window instead. The password goes to macOS, never to this script.
    osascript \
      -e 'on run argv' \
      -e 'set cmd to "/bin/sh -c " & quoted form of (item 1 of argv) & " homecloud"' \
      -e 'repeat with i from 2 to count of argv' \
      -e 'set cmd to cmd & " " & quoted form of (item i of argv)' \
      -e 'end repeat' \
      -e 'do shell script cmd with prompt "Homecloud is installing Docker Desktop." with administrator privileges' \
      -e 'end run' \
      "$INSTALL_SNIPPET" "$dmg" "$ME" "$HOME" >/dev/null 2>"$err" || status=$?
  fi
  if [ "$status" != 0 ]; then
    cat "$err" >&2 || true
    if grep -q -- '-128' "$err" 2>/dev/null; then
      fail "You closed the password window, so run this setup again and type your Mac password when macOS asks."
    fi
    if grep -qi 'not allowed\|not in the sudoers\|incorrect password' "$err" 2>/dev/null; then
      fail "Installing Docker Desktop needs the password of an administrator account on this Mac, so run this setup again from an administrator account."
    fi
    fail "Docker Desktop did not install, so check that macOS is up to date and run this setup again."
  fi
  rm -f "$dmg"
  DOCKER_APP=$(find_docker_app) || fail "Docker Desktop did not install, so check that macOS is up to date and run this setup again."
  say "Docker Desktop is installed in $DOCKER_APP."
}

# ---------------------------------------------------------------- 1. check this Mac (read-only)

say "Step 1 of 6: checking this Mac (nothing changes)"

macos=$(sw_vers -productVersion 2>/dev/null || true)
macos_major=${macos%%.*}
case "$macos_major" in ''|*[!0-9]*) macos_major=0 ;; esac
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
  CHIP="Apple silicon"; DMG_ARCH=arm64  # also right when this shell runs under Rosetta
else
  CHIP="Intel"; DMG_ARCH=amd64
fi
ram=$(sysctl -n hw.memsize 2>/dev/null || true)
case "$ram" in ''|*[!0-9]*) ram=0 ;; esac
RAM_MIB=$((ram / 1048576))
free_kb=$(df -Pk "$HOME" 2>/dev/null | awk 'NR == 2 { print $4 }')
case "$free_kb" in ''|*[!0-9]*) free_kb=0 ;; esac
FREE_GB=$((free_kb / 1000000))

# [I1] Immich asks for 6 GB, or less with machine learning off. On an 8 GB Mac, Docker gets 5 GB
# and macOS keeps 3 GB. With 16 GB or more, Docker's default of half the memory is enough.
NEED_MIB=5120; SET_MIB=5120
if [ "$RAM_MIB" -ge 16384 ] && [ "$ML" != off ]; then NEED_MIB=6144; SET_MIB=8192; fi

PY_OK=0; PY_VERSION=""
if python_ok; then
  PY_OK=1
  PY_VERSION=$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || true)
fi

DOCKER_APP=$(find_docker_app || true)
if docker_running; then DSTATE=running
elif [ -n "$DOCKER_APP" ]; then DSTATE=stopped
else DSTATE=missing
fi

MEM_NOW=0       # MiB: what the running VM reports, or what the settings file says
MEM_CHANGE=0    # 1 when Docker's memory gets raised
if [ "$DSTATE" = running ]; then
  MEM_NOW=$(docker_mem_mib)
  if [ "$MEM_NOW" -gt 0 ] && ! docker_mem_ok "$MEM_NOW" && [ -n "$DOCKER_APP" ]; then MEM_CHANGE=1; fi
else
  MEM_NOW=""
  if [ "$PY_OK" = 1 ]; then MEM_NOW=$(configured_mib); fi  # Apple's python3 stub would open a window
  [ -n "$MEM_NOW" ] || MEM_NOW=$((RAM_MIB / 2))  # [D2] the default is half of the Mac's memory
  if [ "$MEM_NOW" -lt "$NEED_MIB" ]; then MEM_CHANGE=1; fi
fi

# A rerun without --app-dir finds the folder of the Immich that already runs.
if [ -z "$APP_DIR_ARG" ] && [ "$DSTATE" = running ]; then
  wd=$("$(find_docker)" inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' immich_server 2>/dev/null || true)
  if [ -n "$wd" ] && [ "$wd" != "$APP_DIR" ] && [ -f "$wd/docker-compose.yml" ]; then
    APP_DIR=$wd; APP_DIR_ARG=$wd
  fi
fi

IMMICH=none
if [ -f "$APP_DIR/docker-compose.yml" ] && [ -f "$APP_DIR/.env" ]; then IMMICH=stopped; fi
if [ "$DSTATE" = running ] && immich_up; then IMMICH=running; fi
SKIP_IMMICH=0
if [ "$IMMICH" = running ] && [ -f "$APP_DIR/docker-compose.yml" ]; then
  if [ "$ML" = auto ] || [ "$ML" = "$(ml_state)" ]; then SKIP_IMMICH=1; fi
fi
EMAIL=""; SKIP_BOOTSTRAP=0
if [ "$IMMICH" = running ] && [ "$PY_OK" = 1 ] && EMAIL=$(immich_email); then SKIP_BOOTSTRAP=1; fi

say "  macOS ${macos:-unknown} on a Mac with $CHIP"
say "  Memory: $(gib "$RAM_MIB") GB"
say "  Free space on the disk with your home folder: $FREE_GB GB"
if [ "$PY_OK" = 1 ]; then say "  Python: $PY_VERSION"; else say "  Python: missing (python3 3.9 or newer is needed)"; fi
case "$DSTATE" in
  running) if [ "$MEM_NOW" -gt 0 ]; then say "  Docker: running, with $(gib "$MEM_NOW") GB of memory"; else say "  Docker: running"; fi ;;
  stopped) say "  Docker Desktop: installed, not running (memory setting: $(gib "$MEM_NOW") GB)" ;;
  missing) say "  Docker Desktop: not installed" ;;
esac
case "$IMMICH" in
  running) say "  Immich: running at $IMMICH_URL (files in $APP_DIR)" ;;
  stopped) say "  Immich: set up in $APP_DIR, not running" ;;
  none) say "  Immich: not set up" ;;
esac
if [ "$SKIP_BOOTSTRAP" = 1 ]; then say "  Immich account: ${EMAIL:-found}, and the saved API key works"; fi

missing=""
set -- "$SETUP_IMMICH" "$BOOTSTRAP" "$PHONE_CARD"
if [ "$TAILSCALE" = 1 ]; then set -- "$@" "$TS_SETUP"; fi
for s in "$@"; do [ -f "$s" ] || missing="${missing:+$missing, }$s"; done
if [ -n "$missing" ]; then
  say "  Missing in $ROOT: $missing"
  fail "Part of Homecloud is missing ($missing), so download the whole Homecloud folder again and run this setup again."
fi
if [ "$RAM_MIB" -gt 0 ] && [ "$RAM_MIB" -lt "$MIN_RAM_MIB" ]; then
  fail "This Mac has $(gib "$RAM_MIB") GB of memory and Immich needs a Mac with at least 8 GB, so use another computer for Homecloud."
fi
if [ "$DSTATE" = missing ] && [ "$macos_major" -gt 0 ] && [ "$macos_major" -lt "$MIN_MACOS" ]; then
  fail "Docker Desktop needs a newer macOS than $macos, so update macOS in System Settings > General > Software Update and run this setup again."
fi
if { [ "$DSTATE" = missing ] || [ "$IMMICH" = none ]; } && [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
  fail "Docker and Immich need about $MIN_FREE_GB GB of free disk space and this Mac has $FREE_GB GB, so free some space and run this setup again."
fi

# ---------------------------------------------------------------- plan and the one question

say ""
say "Plan:"
if [ "$PY_OK" = 0 ]; then
  say "  - Install Apple's Command Line Tools, which bring python3. A window opens: click Install."
fi
case "$DSTATE" in
  missing)
    say "  - Install Docker Desktop for $CHIP the way Docker's install guide describes it: download"
    say "    https://desktop.docker.com/mac/main/$DMG_ARCH/Docker.dmg, then run its installer with sudo and"
    say "    --accept-license --user=$ME. macOS asks for your Mac password once."
    say "    --accept-license accepts the Docker Subscription Service Agreement for you. Docker Desktop is"
    say "    free for personal use and for small businesses."
    ;;
  *) say "  - Docker Desktop is installed. Nothing to install." ;;
esac
if [ "$MEM_CHANGE" = 1 ]; then
  if [ "$DSTATE" = missing ]; then
    say "  - Give Docker Desktop $(gib "$SET_MIB") GB of memory. By default it takes half of this Mac's memory,"
    say "    $(gib "$MEM_NOW") GB, and Immich needs about $(gib "$NEED_MIB") GB."
  else
    say "  - Give Docker Desktop $(gib "$SET_MIB") GB of memory (now $(gib "$MEM_NOW") GB). Immich needs about $(gib "$NEED_MIB") GB."
  fi
  say "    Only MemoryMiB changes in ~${SETTINGS#"$HOME"},"
  say "    and a copy of the file stays next to it."
  if [ "$DSTATE" = running ]; then say "    Docker Desktop quits and starts again for this."; fi
fi
if [ "$DSTATE" != running ]; then say "  - Start Docker Desktop and wait until it runs."; fi
if [ "$SKIP_IMMICH" = 1 ]; then
  say "  - Immich already runs. Nothing to do."
else
  say "  - Install or start Immich: bash $SETUP_IMMICH --yes --ml $ML${APP_DIR_ARG:+ --app-dir $APP_DIR_ARG}"
fi
if [ "$SKIP_BOOTSTRAP" = 1 ]; then
  say "  - Check your Immich account and API key: python3 $BOOTSTRAP. The saved key works, so no window opens."
else
  say "  - Create your Immich account and an API key for Homecloud: python3 $BOOTSTRAP"
  say "    A window asks for an Immich password. Type it only in that window, never in the chat."
fi
if [ "$TAILSCALE" = 1 ]; then
  say "  - Connect this Mac to Tailscale: bash $TS_SETUP --yes"
  say "    It installs Tailscale if it is missing. A browser window opens: sign in to Tailscale there."
else
  say "  - Tailscale: skipped (--no-tailscale). Your iPhone reaches Immich only on the same Wi-Fi."
fi
say "  - Make the phone card with two QR codes and open it in your browser: python3 $PHONE_CARD"
say "It never changes iCloud, the Photos library or your photos."
say ""

if [ "$YES" != 1 ]; then
  if [ -t 0 ]; then
    printf 'Continue? Type y and press Return: '
    answer=""
    read -r answer || true
    case "$answer" in y|Y|yes|Yes|YES) ;; *) say "Nothing was changed."; exit 2 ;; esac
  else
    say "Nothing was changed. After you agree to the plan above, run: bash scripts/setup.sh --yes"
    exit 2
  fi
fi

# ---------------------------------------------------------------- 2. Python and Docker Desktop

if [ "$PY_OK" = 0 ]; then
  say ""
  say "Installing Apple's Command Line Tools. In the window that opens, click Install, then Agree."
  xcode-select --install >/dev/null 2>&1 || true
  start=$SECONDS
  until python_ok; do
    if [ $((SECONDS - start)) -ge 1800 ]; then
      fail "The Command Line Tools did not finish in 30 minutes, so finish their install in its window and run this setup again."
    fi
    sleep 10
  done
  say "python3 works now."
fi

say ""
say "Step 2 of 6: Docker Desktop"
if [ "$DSTATE" = missing ]; then install_docker; fi

if [ "$DSTATE" != running ]; then
  # Before the first start or while Docker is stopped, the settings file can change directly.
  # Right after an install the file may not exist yet; then the check after the start does it.
  if [ -f "$SETTINGS" ]; then
    cur=$(configured_mib)
    [ -n "$cur" ] || cur=$((RAM_MIB / 2))
    if [ "$cur" -lt "$NEED_MIB" ]; then set_memory_mib "$SET_MIB" || true; fi
  fi
  start_docker
else
  say "Docker is running."
fi

mem=$(docker_mem_mib)
if [ "$mem" -gt 0 ] && ! docker_mem_ok "$mem"; then
  if [ -n "$DOCKER_APP" ] && [ -f "$SETTINGS" ]; then
    say "Docker Desktop has $(gib "$mem") GB of memory, and Immich needs about $(gib "$NEED_MIB") GB."
    quit_docker
    set_memory_mib "$SET_MIB" || true
    start_docker
    mem=$(docker_mem_mib)
  fi
  if ! docker_mem_ok "$mem"; then
    say "Note: Docker has $(gib "$mem") GB of memory. Immich runs with machine learning off."
  fi
fi
if [ "$mem" -gt 0 ]; then say "Memory for Docker: $(gib "$mem") GB."; fi

# ---------------------------------------------------------------- 3. Immich

say ""
say "Step 3 of 6: Immich"
if [ "$SKIP_IMMICH" = 1 ]; then
  say "Immich already runs at $IMMICH_URL. Skipped."
else
  set -- --yes --ml "$ML"
  if [ -n "$APP_DIR_ARG" ]; then set -- "$@" --app-dir "$APP_DIR_ARG"; fi
  bash "$SETUP_IMMICH" "$@" \
    || fail "Immich did not start, so read the message above, fix what it names and run this setup again."
fi
immich_up || fail "Immich does not answer at $IMMICH_URL, so restart Docker Desktop and run this setup again."

# ---------------------------------------------------------------- 4. Immich account and API key

say ""
say "Step 4 of 6: Immich account and API key"
# bootstrap.py finishes quietly when the saved key still works, and it also applies --ml off on
# a rerun, so it always runs. Its last lines are "Email: <email>" and "Result: OK".
set -- --url "$IMMICH_URL"
if [ "$(ml_state)" != on ]; then set -- "$@" --ml off; fi
bs_log="$TMP/bootstrap.out"
set +e
python3 -u "$BOOTSTRAP" "$@" 2>&1 | tee "$bs_log"
status=${PIPESTATUS[0]}
set -e
case "$status" in
  0) ;;
  2) fail "The Immich password window was closed, so run this setup again and type an Immich password when the window asks." ;;
  *) fail "The Immich account step did not finish, so read the message above and run this setup again." ;;
esac
EMAIL=$(sed -n 's/^[[:space:]]*Email:[[:space:]]*//p' "$bs_log" | tail -n 1 | tr -d '\r' | awk '{ print $1 }')
case "$EMAIL" in *?@?*) ;; *) EMAIL=$(immich_email || true) ;; esac

# ---------------------------------------------------------------- 5. Tailscale

say ""
say "Step 5 of 6: Tailscale"
ADDRESS=""
if [ "$TAILSCALE" = 1 ]; then
  ts_log="$TMP/tailscale.out"
  set +e
  bash "$TS_SETUP" --yes 2>&1 | tee "$ts_log"
  status=${PIPESTATUS[0]}
  set -e
  if [ "$status" != 0 ]; then
    fail "Tailscale is not connected yet, so finish the Tailscale sign-in in the browser and run this setup again."
  fi
  ADDRESS=$(sed -n 's/^[[:space:]]*Address:[[:space:]]*//p' "$ts_log" | tail -n 1 | tr -d '\r' | awk '{ print $1 }')
  case "$ADDRESS" in
    http://*|https://*) ;;
    *) fail "The Tailscale step did not print this Mac's address, so run this setup again." ;;
  esac
else
  for ifc in en0 en1; do
    ip=$(ipconfig getifaddr "$ifc" 2>/dev/null || true)
    if [ -n "$ip" ]; then ADDRESS="http://$ip:2283"; break; fi
  done
  [ -n "$ADDRESS" ] || fail "This Mac has no Wi-Fi address, so connect it to your Wi-Fi and run this setup again."
  say "Skipped (--no-tailscale). Wi-Fi address: $ADDRESS"
fi

# ---------------------------------------------------------------- 6. phone card

say ""
say "Step 6 of 6: phone card"
set -- --url "$ADDRESS"
if [ -n "$EMAIL" ]; then set -- "$@" --email "$EMAIL"; fi
python3 "$PHONE_CARD" "$@" --open \
  || fail "The phone card was not made, so run this setup again."

say ""
say "Homecloud is set up on this Mac."
say "  Immich on this Mac:      $IMMICH_URL"
say "  Immich for your iPhone:  $ADDRESS"
if [ -n "$EMAIL" ]; then say "  Immich account:          $EMAIL"; fi
say "  Phone card:              $HOME/.homecloud/phone.html"
say "Result: OK"
