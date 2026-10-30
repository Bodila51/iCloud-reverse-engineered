#!/bin/bash
# Homecloud: remove the Immich test install from a Mac, after you type DELETE.
#
# Run it yourself in Terminal, from the repository folder:
#   bash scripts/immich/undo_mac.sh                  Immich's containers, images and folder
#   bash scripts/immich/undo_mac.sh --dir DIR        also the download folder DIR (the test folder)
# Options
#   --app-dir DIR    Immich's folder from the setup (default ~/immich-app)
#   --dir DIR        a download folder to delete too. It must hold .homecloud/manifest.jsonl.
#   --key-file FILE  the saved Immich API key (default ~/.homecloud/immich-api-key)
#
# It first lists everything it would remove, with sizes, and removes nothing until you type DELETE.
# It never touches iCloud, your iPhone, the Photos app or its library. It does not uninstall
# Docker Desktop or Tailscale; at the end it prints how to do that by hand.
#
# Sources
#   [D1] docker compose down (--volumes, --rmi all, --remove-orphans):
#        https://docs.docker.com/reference/cli/docker/compose/down/
#   [D2] docker image ls --filter reference, docker image rm:
#        https://docs.docker.com/reference/cli/docker/image/ls/ and .../image/rm/
#   [D3] Uninstall Docker Desktop: https://docs.docker.com/desktop/uninstall/
#   [I1] Immich backups live in UPLOAD_LOCATION/backups: https://docs.immich.app/administration/backup-and-restore

set -euo pipefail

APP_DIR="$HOME/immich-app"
DIR=""
KEY_FILE="$HOME/.homecloud/immich-api-key"

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --app-dir) [ $# -ge 2 ] || die "--app-dir needs a folder." 2; APP_DIR="$2"; shift ;;
    --dir) [ $# -ge 2 ] || die "--dir needs a folder." 2; DIR="$2"; shift ;;
    --key-file) [ $# -ge 2 ] || die "--key-file needs a file." 2; KEY_FILE="$2"; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" 2 ;;
  esac
  shift
done

absolute_path() {
  local p=$1
  # shellcheck disable=SC2088  # matches a literal ~ that the shell did not expand
  case "$p" in "~") p=$HOME ;; "~/"*) p="$HOME/${p#\~/}" ;; esac
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p=${p%/}; done
  printf '%s\n' "$p"
}

under() { case "$1/" in "$2"/*) return 0 ;; esac; return 1; }

size_of() {  # "1.2 GB in 345 files"
  local kb files
  kb=$(du -sk "$1" 2>/dev/null | awk '{print $1}' || echo 0)
  files=$(find "$1" -type f 2>/dev/null | wc -l | tr -d ' ' || echo "?")
  awk -v kb="${kb:-0}" -v f="$files" 'BEGIN {
    b = kb * 1024
    if (b >= 1000000000) printf "%.1f GB in %s files", b / 1000000000, f
    else printf "%.1f MB in %s files", b / 1000000, f
  }'
}

refuse_path() {  # refuse_path PATH WHAT: paths this script never deletes
  local p=$1 what=$2
  while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p=${p%/}; done
  case "$p" in
    /|/Users|/Volumes|/Applications|/System|/Library|"$HOME") die "Refusing to delete $p as $what." 2 ;;
    *.photoslibrary|*.photoslibrary/*) die "Refusing to delete $p: it is a Photos library." 2 ;;
  esac
  case "$p" in
    /Volumes/*/*|/Users/*/*) ;;
    /Volumes/*|/Users/*) die "Refusing to delete $p as $what: it is a whole drive or a whole home folder." 2 ;;
  esac
  if under "$p" "$HOME/Library" || under "$p" "$HOME/Pictures/Photos Library.photoslibrary"; then
    die "Refusing to delete $p as $what: it is inside ~/Library or the Photos library." 2
  fi
  if [ -d "$p" ] && find "$p" -maxdepth 3 -name '*.photoslibrary' -print -quit 2>/dev/null | grep -q .; then
    die "Refusing to delete $p as $what: it contains a Photos library." 2
  fi
}

env_value() {  # env_value KEY: the value from APP_DIR/.env, without quotes
  local line
  line=$(grep -E "^$1=" "$APP_DIR/.env" 2>/dev/null | tail -n 1 || true)
  line=${line#*=}
  case "$line" in
    \'*\') line=${line#\'}; line=${line%\'} ;;
    \"*\") line=${line#\"}; line=${line%\"} ;;
    *) line=${line%% #*} ;;
  esac
  printf '%s\n' "$line"
}

APP_DIR=$(absolute_path "$APP_DIR")
KEY_FILE=$(absolute_path "$KEY_FILE")
refuse_path "$APP_DIR" "Immich's folder"
[ -z "$DIR" ] || DIR=$(absolute_path "$DIR")

# ---------------------------------------------------------------- what is there

COMPOSE=0
[ -f "$APP_DIR/docker-compose.yml" ] && COMPOSE=1
UPLOAD=""
DBDATA=""
if [ -f "$APP_DIR/.env" ]; then
  UPLOAD=$(env_value UPLOAD_LOCATION)
  DBDATA=$(env_value DB_DATA_LOCATION)
  case "$UPLOAD" in ""|/*) ;; *) UPLOAD="$APP_DIR/${UPLOAD#./}" ;; esac
  case "$DBDATA" in ""|/*) ;; *) DBDATA="$APP_DIR/${DBDATA#./}" ;; esac
fi
[ -z "$UPLOAD" ] || refuse_path "$UPLOAD" "Immich's library"
[ -z "$DBDATA" ] || refuse_path "$DBDATA" "Immich's database"
if [ -n "$DIR" ]; then
  refuse_path "$DIR" "the download folder"
  [ -f "$DIR/.homecloud/manifest.jsonl" ] || die "$DIR has no .homecloud/manifest.jsonl, so it is not a Homecloud download folder. Nothing was removed." 2
fi

DOCKER=""
if command -v docker >/dev/null 2>&1; then DOCKER=$(command -v docker)
else
  for c in "$HOME/.docker/bin/docker" /usr/local/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
    if [ -x "$c" ]; then DOCKER=$c; break; fi
  done
fi
if [ -n "$DOCKER" ]; then PATH="$(dirname "$DOCKER"):$PATH"; export PATH; fi
DOCKER_RUNNING=0
if [ -n "$DOCKER" ] && "$DOCKER" info >/dev/null 2>&1; then DOCKER_RUNNING=1; fi
if [ "$COMPOSE" = 1 ] && [ -n "$DOCKER" ] && [ "$DOCKER_RUNNING" = 0 ]; then
  die "Docker Desktop is installed but not running, so Immich's containers cannot be removed.
Open Docker Desktop, wait until the engine runs, then run this again. Nothing was removed." 1
fi

say "This removes:"
n=0
if [ "$COMPOSE" = 1 ] && [ "$DOCKER_RUNNING" = 1 ]; then
  n=$((n + 1))
  say "  $n. Immich's containers, network, Docker volumes and images (docker compose down --volumes --rmi all),"
  say "     and any other image from ghcr.io/immich-app, such as the Immich CLI."
elif [ "$DOCKER_RUNNING" = 1 ]; then
  n=$((n + 1))
  say "  $n. Images from ghcr.io/immich-app left on this Mac, such as the Immich CLI."
fi
if [ -n "$UPLOAD" ] && [ -d "$UPLOAD" ] && ! under "$UPLOAD" "$APP_DIR"; then
  n=$((n + 1))
  say "  $n. Immich's library: $UPLOAD ($(size_of "$UPLOAD"))"
fi
if [ -d "$APP_DIR" ]; then
  n=$((n + 1))
  say "  $n. Immich's folder: $APP_DIR ($(size_of "$APP_DIR"))"
  if [ -n "$UPLOAD" ] && under "$UPLOAD" "$APP_DIR"; then say "     with Immich's library: $UPLOAD"; fi
  if [ -n "$DBDATA" ] && under "$DBDATA" "$APP_DIR"; then say "     with Immich's database: $DBDATA"; fi
fi
if [ -n "$DIR" ]; then
  n=$((n + 1))
  say "  $n. The download folder: $DIR ($(size_of "$DIR")), with its manifest"
fi
if [ -f "$KEY_FILE" ]; then
  n=$((n + 1))
  say "  $n. The saved Immich API key: $KEY_FILE"
fi
if [ "$n" = 0 ]; then
  say "  nothing: no Immich install was found in $APP_DIR."
fi
say ""
if [ -n "$UPLOAD" ]; then
  say "Immich's library holds every photo and video uploaded to Immich, including uploads from the"
  say "iPhone app, and Immich's database backups (in its backups folder). If you already turned off"
  say "iCloud Photos, some of those photos may exist nowhere else. Copy what you need first."
fi
say "Not touched: iCloud, your iPhone, the Photos app and its library, Docker Desktop and Tailscale."
say ""
[ "$n" -gt 0 ] || exit 0

if [ ! -t 0 ]; then
  say "Nothing was removed. This step needs your typed confirmation. Open Terminal yourself,"
  say "go to the kit folder and run the same command there:"
  if [ -n "$DIR" ]; then
    say "  bash scripts/immich/undo_mac.sh --app-dir \"$APP_DIR\" --dir \"$DIR\""
  else
    say "  bash scripts/immich/undo_mac.sh --app-dir \"$APP_DIR\""
  fi
  exit 2
fi
printf 'Type DELETE in capitals and press Return to remove the items above: '
answer=""
read -r answer || true
if [ "$answer" != "DELETE" ]; then
  say "Nothing was removed."
  exit 2
fi

# ---------------------------------------------------------------- remove

failed=0
if [ "$DOCKER_RUNNING" = 1 ]; then
  if [ "$COMPOSE" = 1 ]; then
    say "Removing Immich's containers, volumes and images..."
    # [D1] down: stops and removes containers and networks; --volumes also the named volumes,
    # --rmi all also the images the services use, --remove-orphans also containers of removed services.
    (cd "$APP_DIR" && "$DOCKER" compose down --volumes --rmi all --remove-orphans) || failed=1
  fi
  # [D2] Other Immich images, for example the CLI image or the machine learning image after
  # its service was commented out.
  ids=$("$DOCKER" image ls --filter 'reference=ghcr.io/immich-app/*' --format '{{.ID}}' 2>/dev/null | sort -u || true)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086  # one image ID per word
    "$DOCKER" image rm $ids >/dev/null 2>&1 || say "Some Immich images are still used by other containers; they stay."
  fi
fi
remove_dir() {
  if [ -e "$1" ]; then
    rm -rf "$1" 2>/dev/null || true
    if [ -e "$1" ]; then say "Could not remove all of $1. Delete what is left in Finder."; failed=1
    else say "Removed $1"; fi
  fi
}
if [ -n "$UPLOAD" ] && ! under "$UPLOAD" "$APP_DIR"; then remove_dir "$UPLOAD"; fi
remove_dir "$APP_DIR"
[ -z "$DIR" ] || remove_dir "$DIR"
if [ -f "$KEY_FILE" ]; then rm -f "$KEY_FILE" && say "Removed $KEY_FILE"; fi

# ---------------------------------------------------------------- by hand

cat <<'EOF'

To finish by hand, only if you want to:

Docker Desktop (skip this if you use Docker for anything else). Docker's guide warns that
uninstalling it deletes all Docker containers, images and volumes on this Mac.
  1. Open Docker Desktop, click the Troubleshoot icon (top right), then Uninstall, and confirm.
     Or run in Terminal: /Applications/Docker.app/Contents/MacOS/uninstall
  2. Move Docker from Applications to the Trash.
  3. Remove its leftover files:
       rm -rf ~/Library/Group\ Containers/group.com.docker
       rm -rf ~/.docker

Tailscale:
  1. Quit Tailscale from its icon in the menu bar, then move Tailscale from Applications to the
     Trash. Tailscale's own docs have an uninstall page with the full steps; this kit did not
     check them.
  2. In the Tailscale admin console (https://login.tailscale.com/admin/machines), open the menu
     next to this computer and remove it.
  3. On the iPhone, press and hold the Tailscale and Immich icons and choose Remove App. That does
     not delete any photo from the iPhone.

Keeping the Mac awake: if you ran scripts/always_on/mac.sh --apply, undo it with
  bash scripts/always_on/mac.sh --undo
EOF
exit "$failed"
