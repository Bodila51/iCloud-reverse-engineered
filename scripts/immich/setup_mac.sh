#!/bin/bash
# Homecloud: install Immich on a Mac with Docker Desktop, the way the Immich docs describe it.
#
# Run it from the repository folder:
#   bash scripts/immich/setup_mac.sh          shows the plan and asks before it changes anything
#   bash scripts/immich/setup_mac.sh --yes    for when you already agreed to the plan
#
# Options
#   --app-dir DIR          folder for docker-compose.yml, .env and the database (default ~/immich-app)
#   --upload-location DIR  folder where Immich keeps its copy of every photo (default APP_DIR/library)
#   --ml auto|on|off       machine learning. auto (the default) turns it off when Docker Desktop has
#                          less than the 6 GB of memory that Immich asks for, as on an 8 GB Mac.
#   --timeout SECONDS      how long to wait for Immich to answer after the start (default 900)
#
# What it does
#   1. Checks that Docker Desktop is installed and running. If not, it prints Docker's download
#      links and stops.
#   2. New install: downloads docker-compose.yml and example.env from the latest Immich release,
#      writes .env (folders, a random database password, your time zone) and, with machine
#      learning off, comments out the machine learning service.
#      Existing install: changes no file, unless you pass --ml on or --ml off.
#   3. Starts Immich with "docker compose up -d", waits until it answers, and prints the address.
# It never reads or changes iCloud, the Photos library or your download folder.
#
# Sources (each command below names its source)
#   [I1] Immich, Docker Compose install: https://docs.immich.app/install/docker-compose
#        (text: docs/docs/partials/_docker-compose-install-steps.mdx in github.com/immich-app/immich)
#   [I2] Immich requirements: https://docs.immich.app/install/requirements
#   [I3] Immich FAQ, "How can I disable machine learning?": https://docs.immich.app/FAQ
#   [I4] Immich environment variables (TZ): https://docs.immich.app/install/environment-variables
#   [I5] Immich post-install steps: https://docs.immich.app/install/post-install
#   [I6] Immich API, GET /api/server/ping and /api/server/version: open-api/immich-openapi-specs.json
#        in github.com/immich-app/immich
#   [D1] Docker Desktop on Mac: https://docs.docker.com/desktop/setup/install/mac-install/
#   [D2] Docker Desktop settings: https://docs.docker.com/desktop/settings-and-maintenance/settings/
#   [D3] Restart policies: https://docs.docker.com/engine/containers/start-containers-automatically/
#   [D4] .env syntax: https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/
#   [D5] docker compose, docker info, docker inspect: https://docs.docker.com/reference/cli/docker/
#   [M1] macOS man pages: sysctl(8), ipconfig(8), df(1), readlink(1), fdesetup(8), curl(1), od(1)

set -euo pipefail

APP_DIR="$HOME/immich-app"
UPLOAD_LOCATION=""
ML="auto"
TIMEOUT=900
YES=0
# [I1] "Download docker-compose.yml and example.env" from the latest release.
RELEASE_URL="https://github.com/immich-app/immich/releases/latest/download"
# [I2] "RAM: Minimum 6GB". Docker reports a 6 GB VM as a bit less than 6,000,000,000 bytes.
MIN_DOCKER_MEM=5500000000
ML_MARK="#ML-OFF# "
ML_NOTE="#ML-OFF-NOTE#"

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --app-dir) [ $# -ge 2 ] || die "--app-dir needs a folder." 2; APP_DIR="$2"; shift ;;
    --app-dir=*) APP_DIR="${1#*=}" ;;
    --upload-location) [ $# -ge 2 ] || die "--upload-location needs a folder." 2; UPLOAD_LOCATION="$2"; shift ;;
    --upload-location=*) UPLOAD_LOCATION="${1#*=}" ;;
    --ml) [ $# -ge 2 ] || die "--ml needs auto, on or off." 2; ML="$2"; shift ;;
    --ml=*) ML="${1#*=}" ;;
    --timeout) [ $# -ge 2 ] || die "--timeout needs a number of seconds." 2; TIMEOUT="$2"; shift ;;
    --timeout=*) TIMEOUT="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" 2 ;;
  esac
  shift
done

case "$ML" in auto|on|off) ;; *) die "--ml must be auto, on or off." 2 ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) die "--timeout must be a number of seconds." 2 ;; esac

absolute_path() {  # expands ~ and makes a path absolute, without resolving links
  local p=$1
  # shellcheck disable=SC2088  # matches a literal ~ that the shell did not expand
  case "$p" in "~") p=$HOME ;; "~/"*) p="$HOME/${p#\~/}" ;; esac
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p=${p%/}; done
  printf '%s\n' "$p"
}

under() {  # under PATH PARENT: true if PATH is PARENT or inside it
  case "$1/" in "$2"/*) return 0 ;; esac
  return 1
}

check_location() {  # check_location PATH WHAT
  local p=$1 what=$2
  if under "$p" "$HOME/Desktop" || under "$p" "$HOME/Documents" || under "$p" "$HOME/Library"; then
    die "Pick another folder for $what than $p. Desktop and Documents can sync to iCloud Drive, and
~/Library holds app data. Keep Immich's files out of folders that sync to a cloud service." 2
  fi
  case "$p" in
    *.photoslibrary|*.photoslibrary/*) die "$p is inside a Photos library. Pick another folder for $what." 2 ;;
  esac
}

APP_DIR=$(absolute_path "$APP_DIR")
[ -n "$UPLOAD_LOCATION" ] || UPLOAD_LOCATION="$APP_DIR/library"
UPLOAD_LOCATION=$(absolute_path "$UPLOAD_LOCATION")
DB_DATA_LOCATION="$APP_DIR/postgres"

if [ "$(uname -s)" != "Darwin" ] && [ "${HOMECLOUD_ALLOW_NON_MAC:-}" != "1" ]; then
  die "This script is for macOS. On Windows, run scripts/immich/setup_windows.ps1." 2
fi

# [I2] The database "should ideally use local SSD storage, and never a network share".
# An external drive can be unplugged while the database writes, so keep it on the internal disk.
if under "$APP_DIR" "/Volumes"; then
  die "Keep --app-dir on the Mac's internal disk: it holds Immich's database, and Immich's
requirements say the database belongs on a local SSD. Put the photos on an external drive
with --upload-location /Volumes/<drive>/immich-library instead." 2
fi
check_location "$APP_DIR" "Immich (--app-dir)"
check_location "$UPLOAD_LOCATION" "the photo library (--upload-location)"
if under "$UPLOAD_LOCATION" "/Volumes"; then
  vol=$(printf '%s\n' "$UPLOAD_LOCATION" | cut -d/ -f1-3)
  [ -d "$vol" ] || die "The drive $vol is not connected. Connect it and run this again." 2
fi

# ---------------------------------------------------------------- Docker Desktop

find_docker() {
  # [D1] Docker Desktop puts its command line tools in $HOME/.docker/bin and adds that folder
  # to PATH; older versions used /usr/local/bin. The app also carries them inside its bundle.
  if command -v docker >/dev/null 2>&1; then command -v docker; return 0; fi
  local c
  for c in "$HOME/.docker/bin/docker" /usr/local/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
    if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

if ! DOCKER=$(find_docker); then
  say "Docker Desktop is not installed. Immich runs inside it."
  say ""
  say "Download it from Docker's install page: https://docs.docker.com/desktop/setup/install/mac-install/"
  say "  Mac with Apple silicon (M1, M2, M3, M4 ...): https://desktop.docker.com/mac/main/arm64/Docker.dmg"
  say "  Mac with an Intel processor:                https://desktop.docker.com/mac/main/amd64/Docker.dmg"
  say "  This Mac reports: $(uname -m) (arm64 means Apple silicon). Apple menu > About This Mac shows the chip."
  say ""
  say "Open Docker.dmg, drag Docker to Applications, open Docker from Applications and accept the terms."
  say "When Docker Desktop shows that the engine is running, run this script again."
  exit 1
fi
# The Docker credential helper lives next to the docker command, so keep that folder on PATH.
PATH="$(dirname "$DOCKER"):$PATH"
export PATH

if ! "$DOCKER" info >/dev/null 2>&1; then  # [D5] docker info fails while the engine is stopped
  say "Docker Desktop is installed but not running."
  say "Open Docker from Applications (or run: open -a Docker). Wait until it shows that the engine"
  say "is running, then run this script again."
  exit 1
fi
if ! "$DOCKER" compose version >/dev/null 2>&1; then  # [I2] Immich needs "docker compose"
  die "Docker Compose is missing. Update Docker Desktop (Docker menu > Check for Updates), then run this again."
fi
os_type=$("$DOCKER" info --format '{{.OSType}}' 2>/dev/null || true)
[ "$os_type" = "linux" ] || die "Docker runs '$os_type' containers. Immich needs Linux containers."

docker_mem=$("$DOCKER" info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
case "$docker_mem" in ''|*[!0-9]*) docker_mem=0 ;; esac
host_mem=0
if command -v sysctl >/dev/null 2>&1; then
  host_mem=$(sysctl -n hw.memsize 2>/dev/null || echo 0)  # [M1] sysctl(8): hw.memsize
  case "$host_mem" in ''|*[!0-9]*) host_mem=0 ;; esac
fi
gb() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1000000000 }'; }

# ---------------------------------------------------------------- helpers

compose() { (cd "$APP_DIR" && "$DOCKER" compose "$@"); }  # [D5] run where docker-compose.yml is

env_quote() {  # prints a .env value; quoted when needed [D4]
  local v=$1
  case "$v" in
    *"'"*|*\\*|*"
"*) die "This path cannot go into .env: $v (it contains a quote, a backslash or a line break)." 2 ;;
  esac
  if printf '%s' "$v" | grep -Eq '^[A-Za-z0-9_./:@%+,=-]+$'; then
    printf '%s\n' "$v"
  else
    printf "'%s'\n" "$v"  # [D4] "Single-quoted (') values are used literally."
  fi
}

set_env_value() {  # set_env_value FILE KEY VALUE: replaces "KEY=" or "# KEY=", else appends
  local file=$1 key=$2 value=$3
  KEY="$key" VAL="$value" awk '
    BEGIN { done = 0; key = ENVIRON["KEY"]; val = ENVIRON["VAL"] }
    !done && $0 ~ ("^[#[:space:]]*" key "=") { print key "=" val; done = 1; next }
    { print }
    END { if (!done) print key "=" val }
  ' "$file" > "$file.tmp"
  mv "$file.tmp" "$file"
}

ml_state() {  # on, off (turned off by this script) or unknown
  local f="$APP_DIR/docker-compose.yml"
  if grep -q "^$ML_MARK" "$f"; then echo off
  elif grep -q '^  immich-machine-learning:' "$f"; then echo on
  else echo unknown
  fi
}

ml_comment_out() {  # [I3] "comment out the immich-machine-learning section of the docker-compose.yml"
  local f=$1
  MARK="$ML_MARK" NOTE="$ML_NOTE" awk '
    BEGIN { inml = 0; mark = ENVIRON["MARK"]; note = ENVIRON["NOTE"] }
    {
      if ($0 ~ /^  immich-machine-learning:[[:space:]]*$/) {
        inml = 1
        print note " Homecloud turned machine learning off (Immich FAQ: How can I disable machine learning?)."
        print note " To turn it back on: bash scripts/immich/setup_mac.sh --ml on"
        print mark $0
        next
      }
      if (inml) {
        if ($0 ~ /^  [^ #]/ || $0 ~ /^[^ #]/) { inml = 0 }
        else if ($0 ~ /^[[:space:]]*$/) { print; next }
        else { print mark $0; next }
      }
      print
    }
  ' "$f" > "$f.tmp"
  mv "$f.tmp" "$f"
}

ml_restore() {
  local f=$1
  MARK="$ML_MARK" NOTE="$ML_NOTE" awk '
    BEGIN { mark = ENVIRON["MARK"]; note = ENVIRON["NOTE"] }
    index($0, note) == 1 { next }
    index($0, mark) == 1 { print substr($0, length(mark) + 1); next }
    { print }
  ' "$f" > "$f.tmp"
  mv "$f.tmp" "$f"
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

free_gb() {  # free space, in GB, on the disk that holds $1 (or its nearest existing parent)
  local p=$1
  while [ ! -e "$p" ] && [ "$p" != "/" ]; do p=$(dirname "$p"); done
  df -Pk "$p" | awk 'NR == 2 { printf "%.1f", $4 * 1024 / 1000000000 }'  # [M1] df(1)
}

explain_ml_off() {
  if [ "$docker_mem" -lt "$MIN_DOCKER_MEM" ]; then
    say "  Machine learning will be off. Docker Desktop has $(gb "$docker_mem") GB of memory and Immich asks"
    say "  for at least 6 GB, or runs with machine learning off on less (Immich requirements)."
  else
    say "  Machine learning will be off, as you asked with --ml off."
  fi
  say "  You lose: search by what is in a photo (for example \"dog\" or \"beach\"), People (faces),"
  say "  search for text in photos, and duplicate detection. The Explore page shows little (Immich FAQ)."
  say "  You keep: the timeline, albums, favorites, the map, search by date, place, camera or file name,"
  say "  sharing, and backup from the Immich iPhone app."
}

# ---------------------------------------------------------------- plan

# Both paths go into .env; stop now if one cannot (an assignment stops the script under set -e).
Q_UPLOAD_LOCATION=$(env_quote "$UPLOAD_LOCATION")
Q_DB_DATA_LOCATION=$(env_quote "$DB_DATA_LOCATION")

EXISTING=0
if [ -f "$APP_DIR/docker-compose.yml" ] && [ -f "$APP_DIR/.env" ]; then
  EXISTING=1
elif [ -e "$APP_DIR/docker-compose.yml" ] || [ -e "$APP_DIR/.env" ]; then
  die "$APP_DIR has only one of docker-compose.yml and .env. Fix it by hand, or pick another --app-dir." 2
elif [ -d "$APP_DIR" ] && [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
  die "$APP_DIR exists and is not empty. Pick another folder with --app-dir." 2
fi

if [ "$ML" = "auto" ]; then
  if [ "$docker_mem" -lt "$MIN_DOCKER_MEM" ]; then ML_WANT=off; else ML_WANT=on; fi
else
  ML_WANT=$ML
fi

if [ "$host_mem" -gt 0 ]; then
  say "Docker Desktop is running. Memory for Docker: $(gb "$docker_mem") GB. Memory in this Mac: $(gb "$host_mem") GB."
else
  say "Docker Desktop is running. Memory for Docker: $(gb "$docker_mem") GB."
fi
if [ "$ML_WANT" = "on" ] && [ "$docker_mem" -lt "$MIN_DOCKER_MEM" ]; then
  say "Warning: Immich asks for at least 6 GB of memory with machine learning on (Immich requirements)."
  say "Raise it in Docker Desktop > Settings > Resources > Memory limit, or use --ml off."
fi

ML_CHANGE=""
if [ "$EXISTING" = 1 ]; then
  current=$(ml_state)
  say "Immich is already set up in $APP_DIR. Machine learning: $current."
  if [ "$ML" = "off" ] && [ "$current" = "on" ]; then ML_CHANGE=off
  elif [ "$ML" = "on" ] && [ "$current" = "off" ]; then ML_CHANGE=on
  elif [ "$ML" = "auto" ] && [ "$current" != "$ML_WANT" ] && [ "$current" != "unknown" ]; then
    say "Note: with this much memory the recommended setting is --ml $ML_WANT. Nothing changes unless you pass it."
  fi
  say ""
  say "Plan:"
  if [ "$ML_CHANGE" = "off" ]; then
    say "  - Comment out the machine learning service in $APP_DIR/docker-compose.yml."
    explain_ml_off
  elif [ "$ML_CHANGE" = "on" ]; then
    say "  - Turn the machine learning service back on in $APP_DIR/docker-compose.yml."
    say "    Then turn it on in Immich too: Administration > Settings > Machine Learning Settings."
  else
    say "  - Change no file."
  fi
  say "  - Start Immich with: docker compose up -d (in $APP_DIR)."
else
  say ""
  say "Plan:"
  say "  - Create $APP_DIR and download into it, from the latest Immich release on GitHub:"
  say "      $RELEASE_URL/docker-compose.yml"
  say "      $RELEASE_URL/example.env (saved as .env)"
  say "  - Set in .env: UPLOAD_LOCATION=$UPLOAD_LOCATION"
  say "                 DB_DATA_LOCATION=$DB_DATA_LOCATION (the database, on this Mac's disk)"
  say "                 DB_PASSWORD=<random letters and digits>, and TZ=<this Mac's time zone>"
  if [ "$ML_WANT" = "off" ]; then
    say "  - Comment out the machine learning service in docker-compose.yml (Immich FAQ)."
    explain_ml_off
  else
    say "  - Keep machine learning on."
  fi
  say "  - Start Immich with: docker compose up -d. The first start downloads about 1 GB of images."
  say "    Free space on this disk now: $(free_gb "$APP_DIR") GB. Immich keeps its own copy of every"
  say "    photo, so the library needs room for your photos plus 10 to 20% (Immich requirements)."
fi
say "  The containers have the restart policy \"always\" from Immich's compose file, so Docker"
say "  starts them again whenever Docker Desktop starts."
say ""
confirm

# ---------------------------------------------------------------- install or update

if [ "$EXISTING" = 0 ]; then
  created_app_dir=0
  [ -d "$APP_DIR" ] || created_app_dir=1
  mkdir -p "$APP_DIR"
  tmp_compose="$APP_DIR/.docker-compose.yml.download"
  tmp_env="$APP_DIR/.example.env.download"
  cleanup() {
    rm -f "$tmp_compose" "$tmp_env" "$tmp_compose.tmp" "$tmp_env.tmp"
    if [ "$created_app_dir" = 1 ] && [ ! -f "$APP_DIR/.env" ]; then rmdir "$APP_DIR" 2>/dev/null || true; fi
  }
  trap cleanup EXIT
  # [I1] The docs use wget; macOS has curl, which downloads the same files. -L follows the
  # release redirect, -f fails on HTTP errors.
  say "Downloading docker-compose.yml and example.env..."
  curl -fsSL --retry 3 -o "$tmp_compose" "$RELEASE_URL/docker-compose.yml" \
    || die "Could not download docker-compose.yml. Check the internet connection and run this again."
  curl -fsSL --retry 3 -o "$tmp_env" "$RELEASE_URL/example.env" \
    || die "Could not download example.env. Check the internet connection and run this again."
  if ! grep -q '^  immich-server:' "$tmp_compose" || ! grep -q 'UPLOAD_LOCATION' "$tmp_compose"; then
    die "The downloaded docker-compose.yml does not look like Immich's. Nothing was started."
  fi
  for key in UPLOAD_LOCATION DB_DATA_LOCATION DB_PASSWORD; do
    grep -q "^$key=" "$tmp_env" || die "The downloaded example.env has no $key line. Nothing was started."
  done

  # [I1] Step 2: "Populate UPLOAD_LOCATION", "Consider changing DB_PASSWORD ... use only the
  # characters A-Za-z0-9", "Set your timezone by uncommenting the TZ= line."
  set_env_value "$tmp_env" UPLOAD_LOCATION "$Q_UPLOAD_LOCATION"
  set_env_value "$tmp_env" DB_DATA_LOCATION "$Q_DB_DATA_LOCATION"
  password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')  # [M1] od(1): 48 hex digits, a-f and 0-9
  [ "${#password}" -eq 48 ] || die "Could not make a random password."
  set_env_value "$tmp_env" DB_PASSWORD "$password"
  # [I4] TZ takes an identifier like Europe/Prague. macOS links /etc/localtime into zoneinfo.
  tz=$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*/zoneinfo/||p' || true)
  if printf '%s' "$tz" | grep -Eq '^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$'; then
    set_env_value "$tmp_env" TZ "$tz"
  else
    say "Could not read this Mac's time zone, so TZ stays unset (Immich then uses UTC as a fallback)."
  fi
  if [ "$ML_WANT" = "off" ]; then
    ml_comment_out "$tmp_compose"
  fi
  mv "$tmp_compose" "$APP_DIR/docker-compose.yml"
  mv "$tmp_env" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"  # it holds the database password
  mkdir -p "$UPLOAD_LOCATION" "$DB_DATA_LOCATION"
  # [D5] "docker compose config --quiet" only validates the files.
  if ! compose config --quiet; then
    die "Docker Compose does not accept the new files in $APP_DIR. Nothing was started.
Remove $APP_DIR and run this again, or report the message above."
  fi
  say "Wrote $APP_DIR/docker-compose.yml and $APP_DIR/.env (readable only by you)."
elif [ "$ML_CHANGE" = "off" ]; then
  cp "$APP_DIR/docker-compose.yml" "$APP_DIR/docker-compose.yml.before-ml-off"
  ml_comment_out "$APP_DIR/docker-compose.yml"
  compose config --quiet || die "Docker Compose does not accept the change. The old file is in docker-compose.yml.before-ml-off."
elif [ "$ML_CHANGE" = "on" ]; then
  ml_restore "$APP_DIR/docker-compose.yml"
  compose config --quiet || die "Docker Compose does not accept the change. Check $APP_DIR/docker-compose.yml."
fi

say "Starting Immich..."
# [I1] Step 3: "docker compose up -d". --remove-orphans removes the machine learning container
# after its service was commented out ([D5] docker compose up).
if [ "$ML_CHANGE" = "off" ]; then set -- --remove-orphans; else set --; fi
if ! compose up -d "$@"; then
  say ""
  say "Docker could not start Immich (see the messages above). If a download failed, check the"
  say "internet connection and run this script again. It keeps the files it already wrote."
  exit 1
fi

# [I6] GET /api/server/ping answers {"res":"pong"} once the server is ready.
say "Waiting for Immich to answer at http://localhost:2283 (the first start can take a few minutes)..."
start=$SECONDS
last=0
while :; do
  answer=$(curl -fsS --noproxy '*' --max-time 5 "http://127.0.0.1:2283/api/server/ping" 2>/dev/null || true)
  case "$answer" in *pong*) break ;; esac
  waited=$((SECONDS - start))
  if [ "$waited" -ge "$TIMEOUT" ]; then
    say "Immich did not answer within $TIMEOUT seconds. Container status and the last server log lines:"
    compose ps || true
    compose logs --tail 50 immich-server || true
    say "Run this script again to keep waiting, or report the lines above."
    exit 1
  fi
  if [ $((waited - last)) -ge 30 ]; then
    last=$waited
    say "  still starting ($waited s)..."
  fi
  sleep 5
done

version=$(curl -fsS --noproxy '*' --max-time 5 "http://127.0.0.1:2283/api/server/version" 2>/dev/null \
  | sed -nE 's/.*"major":[[:space:]]*([0-9]+).*"minor":[[:space:]]*([0-9]+).*"patch":[[:space:]]*([0-9]+).*/v\1.\2.\3/p' || true)
say ""
say "Immich ${version:-} is running: http://localhost:2283"

# ---------------------------------------------------------------- after restarts

say ""
say "After a restart of the Mac:"
containers="immich_server immich_redis immich_postgres"
if [ "$(ml_state)" = "on" ]; then containers="$containers immich_machine_learning"; fi
# [D3] restart policy "always": Docker starts the container again whenever the daemon starts.
for c in $containers; do
  if policy=$("$DOCKER" inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$c" 2>/dev/null); then
    say "  Container $c: restart policy \"$policy\""
  else
    say "  Container $c: not found"
  fi
done
# [D2] "Start Docker Desktop when you sign in to your computer" is off by default. Docker documents
# where its settings file is, but not this key, so the script only reads it and you check the switch.
settings="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
autostart=$(grep -o '"AutoStart"[[:space:]]*:[[:space:]]*[a-z]*' "$settings" 2>/dev/null | sed 's/.*:[[:space:]]*//' || true)
case "$autostart" in
  true) say "  Docker Desktop starts when you sign in (its settings file says so)." ;;
  *) say "  Docker Desktop does not seem to start when you sign in. Turn it on: Docker Desktop >"
     say "  Settings > General > \"Start Docker Desktop when you sign in to your computer\"." ;;
esac
if command -v fdesetup >/dev/null 2>&1 && fdesetup status 2>/dev/null | grep -q "On"; then
  say "  FileVault is on, so after a restart or a power cut the Mac waits at the password screen."
  say "  Docker Desktop and Immich stay down until someone signs in."
else
  say "  Docker Desktop starts only after someone signs in to the Mac."
fi

# ---------------------------------------------------------------- addresses

say ""
say "Addresses:"
say "  On this Mac:     http://localhost:2283"
if command -v ipconfig >/dev/null 2>&1; then
  for ifc in en0 en1; do
    ip=$(ipconfig getifaddr "$ifc" 2>/dev/null || true)  # [M1] ipconfig(8) getifaddr
    if [ -n "$ip" ]; then say "  On your Wi-Fi:   http://$ip:2283"; break; fi
  done
fi
ts=""
if command -v tailscale >/dev/null 2>&1; then ts=tailscale
elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then ts=/Applications/Tailscale.app/Contents/MacOS/Tailscale
fi
if [ -n "$ts" ]; then
  tsip=$("$ts" ip -4 2>/dev/null | head -n 1 || true)  # Tailscale CLI: tailscale ip -4
  if [ -n "$tsip" ]; then
    say "  Through Tailscale: http://$tsip:2283 (for the Immich iPhone app, see scripts/tailscale.md)"
  else
    say "  Through Tailscale: Tailscale is installed but not connected. See scripts/tailscale.md."
  fi
else
  say "  Through Tailscale: set it up with scripts/tailscale.md."
fi
say "  Free space for the library ($UPLOAD_LOCATION): $(free_gb "$UPLOAD_LOCATION") GB"

# ---------------------------------------------------------------- next steps

say ""
say "Next steps:"
say "  1. Open http://localhost:2283, click Getting Started and create your admin account (Immich post-install)."
step=2
if [ "$(ml_state)" != "on" ]; then
  say "  $step. In Immich, open Administration > Settings > Machine Learning Settings and turn machine learning off."
  say "     Its container is not running, so its jobs would fail for every photo (Immich FAQ)."
  step=$((step + 1))
fi
if [ "$autostart" != "true" ]; then
  say "  $step. In Docker Desktop > Settings > General, turn on \"Start Docker Desktop when you sign in to your computer\"."
  step=$((step + 1))
fi
say "  $step. In Immich, click your initials (top right) > Account Settings > API Keys > New API Key."
say "     Tick Select all, click Create and copy the key. Immich shows it only once."
step=$((step + 1))
say "  $step. In your own Terminal window, run this and paste the key when it asks (it stays hidden):"
say "       python3 scripts/immich/import_library.py save-key"
step=$((step + 1))
say "  $step. Check the import plan: python3 scripts/immich/import_library.py check --dir DIR"
