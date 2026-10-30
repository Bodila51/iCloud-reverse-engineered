#!/usr/bin/env python3
"""Homecloud: import a download folder into Immich, then check the result.

Run it from the repository folder:

    python3 scripts/immich/import_library.py save-key
    python3 scripts/immich/import_library.py check --dir DIR
    python3 scripts/immich/import_library.py all --dir DIR --yes

Commands
    save-key  Asks for an Immich API key with hidden input, tests it, and saves it in
              a file that only your user account can read
              (default: ~/.homecloud/immich-api-key). Run it in your own Terminal or
              PowerShell window, never through a chat.
    check     Read-only. Tests the server, the key, Docker and the folder, and prints
              the plan.
    upload    Copies every photo and video in DIR into Immich with the official Immich
              CLI, run from its Docker image. Files that Immich already has are skipped.
    apply     Marks favorites, fills albums and archives hidden items, as listed in
              DIR/.homecloud/manifest.jsonl. Running it again changes nothing more.
    verify    Read-only. Checks that Immich holds every file listed in the manifest.
    all       upload, then apply, then verify.

upload, apply and all change things. They show the plan and ask first. Pass --yes to
skip the question after the user has agreed to the plan.

Exit codes
    0  done; for verify: Immich holds every file, and favorites, albums and archived
       items match the manifest
    1  something failed, or (verify) Immich does not hold every file
    2  usage error, missing key, or nothing was changed because nobody confirmed
    3  (verify) every file is in Immich, but favorites, albums or archived items are
       not complete yet: run apply

Why the default is an upload and not an External Library: scripts/immich/README.md.

Sources
    Immich CLI, Docker usage and upload options:
        https://docs.immich.app/features/command-line-interface
    Immich API: every endpoint and field below is defined in the OpenAPI file of the
    Immich repository (release v3.3.5 checked):
        https://github.com/immich-app/immich/blob/main/open-api/immich-openapi-specs.json
        The API key goes in the x-api-key header (components.securitySchemes.api_key).
    Compose names the network <project>_default and makes each service name a host name:
        https://docs.docker.com/compose/how-tos/networking/
    docker run -e NAME takes the value from the environment, so the key never shows
    on a command line: https://docs.docker.com/reference/cli/docker/container/run/
    Docker Desktop shares /Users, /Volumes, /private, /tmp and /var/folders by default:
        https://docs.docker.com/desktop/settings-and-maintenance/settings/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

DEFAULT_URL = "http://localhost:2283"
DEFAULT_KEY_FILE = Path.home() / ".homecloud" / "immich-api-key"
DEFAULT_APP_DIR = Path.home() / "immich-app"
# Immich CLI docs, "Installation (Docker)". Pinned to the release the kit was checked against
# (CLI tags have no "v"); it works with every Immich 3 server.
DEFAULT_CLI_IMAGE = "ghcr.io/immich-app/immich-cli:3.3.5"
# docker-compose.yml from the Immich release sets "name: immich", so Compose creates the
# network immich_default, and the server is reachable there as immich-server:2283.
DEFAULT_NETWORK = "immich_default"
DEFAULT_CLI_URL = "http://immich-server:2283/api"
ML_CONTAINER = "immich_machine_learning"
BATCH = 1000  # the Immich CLI itself sends album updates in batches of 1000 or more
KINDS = ("photo", "video", "live_video", "edited")
STATUSES = ("done", "missing", "failed")
HIDDEN_CHOICES = ("archive", "keep")

# Permission names from the OpenAPI file (schema "Permission"). The Immich CLI asks the
# server for user.read when it logs in and checks asset.upload before it uploads.
REQUIRED_PERMISSIONS = {
    "user.read": "let the Immich CLI log in",
    "asset.upload": "upload files and look them up by checksum",
    "asset.read": "search your photos",
    "asset.update": "mark favorites and archive hidden items",
    "asset.statistics": "count your photos",
    "album.read": "list albums",
    "album.create": "create albums",
    "albumAsset.create": "add photos to albums",
}
DOCKER_SHARED_PREFIXES = ("/Users/", "/Volumes/", "/private/", "/tmp/", "/var/folders/")


# ----------------------------------------------------------------------------- output

def say(text: str = "") -> None:
    print(text, flush=True)


def warn(text: str) -> None:
    print("Warning: " + text, flush=True)


class Stop(Exception):
    """Ends the program with a message and an exit code."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def human_bytes(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1000.0
    return f"{n:,.1f} TB"


def count(n: int, one: str, many: Optional[str] = None) -> str:
    """'1 file', '2 files'."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def chunks(items: List, size: int) -> Iterable[List]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ------------------------------------------------------------------------------- API

class ApiError(Exception):
    def __init__(self, status: int, method: str, path: str, detail: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status}: {detail}")
        self.status = status


class Api:
    """Small client for the Immich REST API (base URL + /api)."""

    def __init__(self, url: str, key: Optional[str]) -> None:
        url = url.rstrip("/")
        if url.endswith("/api"):
            url = url[: -len("/api")]
        self.url = url
        self.base = url + "/api"
        self.key = key
        # Immich runs on this computer or on the tailnet, so ignore proxy settings.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, body=None, params: Optional[dict] = None,
                timeout: float = 120, retries: int = 3):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json"}
        if self.key:
            headers["x-api-key"] = self.key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode("utf-8")) if raw else None
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:400]
                if err.code >= 500 and attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                raise ApiError(err.code, method, path, detail) from None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as err:
                if attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                raise Stop(f"Cannot reach Immich at {self.url} ({err}). Is it running? "
                           "Run the setup script, or start Docker Desktop.") from None

    def get(self, path: str, params: Optional[dict] = None):
        return self.request("GET", path, params=params)

    def post(self, path: str, body, retries: int = 3):
        return self.request("POST", path, body=body, retries=retries)

    def put(self, path: str, body):
        return self.request("PUT", path, body=body)


def ping(api: Api) -> None:
    # GET /server/ping answers {"res": "pong"} without a key (operationId pingServer).
    answer = api.request("GET", "/server/ping", retries=2, timeout=10)
    if not isinstance(answer, dict) or answer.get("res") != "pong":
        raise Stop(f"{api.url} answered, but not like an Immich server.")


def server_version(api: Api) -> str:
    try:
        v = api.get("/server/version")
        return f"v{v['major']}.{v['minor']}.{v['patch']}"
    except (ApiError, KeyError, TypeError):
        return "unknown version"


# ------------------------------------------------------------------------------ key

def read_key_file(path: Path) -> Optional[str]:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return key or None


def load_key(args) -> str:
    key = os.environ.get("IMMICH_API_KEY", "").strip()
    if key:
        return key
    key = read_key_file(Path(args.key_file).expanduser())
    if key:
        return key
    if sys.stdin.isatty():
        key = getpass.getpass("Paste your Immich API key (it stays hidden), then press Return: ").strip()
        if key:
            return key
    raise Stop("No Immich API key found. Create one in Immich (Account Settings > API Keys > "
               "New API Key, tick Select all), then run this in your own Terminal or PowerShell:\n"
               "    python3 scripts/immich/import_library.py save-key", 2)


def check_key(api: Api) -> Tuple[dict, List[str]]:
    """Returns the user and the list of missing permissions."""
    try:
        me = api.get("/users/me")
    except ApiError as err:
        if err.status in (401, 403):
            raise Stop("Immich does not accept this API key. Create a new one in Account Settings > "
                       "API Keys and save it again with: import_library.py save-key", 2) from None
        raise
    try:
        key_info = api.get("/api-keys/me")
        granted = set(key_info.get("permissions") or [])
    except ApiError:
        granted = set()
    if "all" in granted:
        return me, []
    return me, [p for p in REQUIRED_PERMISSIONS if p not in granted]


def explain_missing_permissions(missing: List[str]) -> str:
    lines = ["This API key is missing permissions:"]
    lines += [f"  {p}: needed to {REQUIRED_PERMISSIONS[p]}" for p in missing]
    lines.append("Create a new key in Immich: Account Settings > API Keys > New API Key. "
                 "Tick Select all, then Create, and save it with: import_library.py save-key")
    return "\n".join(lines)


def cmd_save_key(args) -> int:
    if not sys.stdin.isatty():
        raise Stop("save-key reads the key with hidden input, so it needs a real terminal. Open "
                   "Terminal (Mac) or PowerShell (Windows) yourself and run:\n"
                   "    python3 scripts/immich/import_library.py save-key", 2)
    api = Api(args.url, None)
    ping(api)
    say(f"Immich {server_version(api)} answers at {api.url}.")
    say("In Immich, open Account Settings > API Keys > New API Key. Tick Select all, click Create,")
    say("then copy the key. Immich shows it only once.")
    key = getpass.getpass("Paste the key here (it stays hidden), then press Return: ").strip()
    if not key:
        raise Stop("No key entered. Nothing was saved.", 2)
    api.key = key
    me, missing = check_key(api)
    if missing:
        raise Stop(explain_missing_permissions(missing) + "\nNothing was saved.", 2)
    path = Path(args.key_file).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    say(f"The key works for {me.get('email')}. Saved to {path} (only your user account can read it).")
    say("To remove it later, delete that file and delete the key in Immich (Account Settings > API Keys).")
    message = machine_learning_warning(api, find_docker())
    if message:
        say(message)
    return 0


# --------------------------------------------------------------------------- docker

def find_docker() -> Optional[str]:
    exe = shutil.which("docker")
    if exe:
        return exe
    candidates: List[Path] = []
    if sys.platform == "darwin":
        # Docker Desktop install guide: CLI tools in $HOME/.docker/bin (newer versions) or
        # inside the app bundle.
        candidates = [Path.home() / ".docker" / "bin" / "docker", Path("/usr/local/bin/docker"),
                      Path("/Applications/Docker.app/Contents/Resources/bin/docker")]
    elif os.name == "nt":
        # Docker Desktop install guide: per-user install in %LOCALAPPDATA%\Programs\DockerDesktop,
        # all-users install in C:\Program Files\Docker\Docker. The resources\bin part is the
        # layout of the all-users install; the per-user one is assumed to match.
        candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
                      Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def docker_env(docker: str) -> Dict[str, str]:
    """The environment for docker commands. Docker Desktop's credential helper sits next to the
    docker command, so that folder goes on PATH even when the shell did not set it up."""
    env = os.environ.copy()
    folder = str(Path(docker).parent)
    env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    return env


def docker_ok(docker: str, *args: str, timeout: float = 60) -> Tuple[bool, str]:
    try:
        proc = subprocess.run([docker, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, env=docker_env(docker))
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, str(err)
    return proc.returncode == 0, proc.stdout.decode("utf-8", "replace").strip()


def detect_network(docker: str) -> Optional[str]:
    """The Docker network of the running Immich server. Immich's compose file names the
    container immich_server (container_name), so docker inspect finds it by that name."""
    ok, out = docker_ok(docker, "inspect", "--format", "{{json .NetworkSettings.Networks}}", "immich_server")
    if not ok:
        return None
    try:
        networks = json.loads(out)
    except ValueError:
        return None
    names = sorted(networks) if isinstance(networks, dict) else []
    if DEFAULT_NETWORK in names:
        return DEFAULT_NETWORK
    return names[0] if names else None


def ml_container_running(docker: Optional[str]) -> Optional[bool]:
    if not docker:
        return None
    ok, out = docker_ok(docker, "ps", "--filter", f"name=^{ML_CONTAINER}$", "--format", "{{.Names}}")
    if not ok:
        return None
    return ML_CONTAINER in out.split()


def machine_learning_warning(api: Api, docker: Optional[str]) -> Optional[str]:
    """A warning when machine learning is on in Immich but its container is not running."""
    try:
        features = api.get("/server/features")  # public endpoint, no key needed
    except (ApiError, Stop):
        return None
    ml_on = any(features.get(name) for name in ("smartSearch", "facialRecognition", "ocr", "duplicateDetection"))
    if ml_on and ml_container_running(docker) is False:
        return ("Warning: machine learning is on in Immich's settings, but its container is not running "
                "on this computer, so its jobs would fail for every new photo. Turn it off in Immich: "
                "Administration > Settings > Machine Learning Settings (Immich FAQ: How can I disable "
                "machine learning?).")
    return None


# ------------------------------------------------------------------------- manifest

# The script that wrote each manifest row, by its "source" value: (copy step, verify step).
SOURCE_STEPS = {
    "icloud-web": ("python3 scripts/icloud.py download --to DIR", "python3 scripts/icloud.py verify --to DIR"),
    "mac-photos": ("python3 scripts/mac/export_photos.py export --to DIR",
                   "python3 scripts/mac/export_photos.py verify --to DIR"),
    "usb": ("python3 scripts/vault/vault.py copy --to DIR", "python3 scripts/vault/vault.py verify --to DIR"),
    "folder": ("copy the missing files into DIR, then python3 scripts/immich/make_manifest.py --dir DIR",
               "python3 scripts/immich/make_manifest.py --dir DIR"),
    "google-web": ("python3 scripts/google/google_photos.py prepare --to DIR, then paste the script in your "
                   "Google Photos tab again", "python3 scripts/google/google_photos.py verify --to DIR"),
}


def source_step(entries: List["Entry"], which: int) -> str:
    """The copy (0) or verify (1) command of the scripts that made these entries."""
    sources = sorted({e.source for e in entries}) or ["icloud-web"]
    return " or ".join(SOURCE_STEPS[s][which] for s in sources)


class Entry:
    __slots__ = ("path", "filename", "kind", "status", "favorite", "hidden", "albums", "size",
                 "source", "on_disk", "disk_size")

    def __init__(self, raw: dict, lineno: int) -> None:
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip():
            raise Stop(f"Manifest line {lineno} has no path.")
        path = path.replace("\\", "/")
        parts = PurePosixPath(path).parts
        if path.startswith("/") or (len(path) > 1 and path[1] == ":") or ".." in parts:
            raise Stop(f"Manifest line {lineno} has a path outside the folder: {path}")
        self.path = path
        self.filename = raw.get("filename") if isinstance(raw.get("filename"), str) else parts[-1]
        kind = raw.get("kind")
        self.kind = kind if kind in KINDS else "photo"
        status = raw.get("status")
        self.status = status if status in STATUSES else "failed"
        self.favorite = raw.get("favorite") is True
        self.hidden = raw.get("hidden") is True
        albums = raw.get("albums") or []
        self.albums = [a for a in albums if isinstance(a, str) and a] if isinstance(albums, list) else []
        size = raw.get("size")
        self.size = size if isinstance(size, int) and size >= 0 else None
        source = raw.get("source")
        self.source = source if source in SOURCE_STEPS else "icloud-web"
        self.on_disk = False
        self.disk_size = 0


def load_manifest(folder: Path) -> List[Entry]:
    manifest = folder / ".homecloud" / "manifest.jsonl"
    if not manifest.is_file():
        raise Stop(f"No manifest at {manifest}. Make the copy first: scripts/icloud.py download, "
                   "scripts/mac/export_photos.py export, scripts/vault/vault.py copy, scripts/google/google_photos.py verify "
                   "or scripts/immich/make_manifest.py.", 2)
    entries: List[Entry] = []
    seen: Set[str] = set()
    duplicates = 0
    with manifest.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as err:
                raise Stop(f"Manifest line {lineno} is not valid JSON: {err}") from None
            if not isinstance(raw, dict):
                raise Stop(f"Manifest line {lineno} is not a JSON object.")
            entry = Entry(raw, lineno)
            if entry.path in seen:
                duplicates += 1
                continue
            seen.add(entry.path)
            entries.append(entry)
    if duplicates:
        warn(f"the manifest lists {duplicates} path(s) more than once; each path is counted once.")
    if not entries:
        raise Stop(f"The manifest at {manifest} is empty.")
    return entries


def stat_files(folder: Path, entries: List[Entry]) -> None:
    for e in entries:
        if e.status != "done":
            continue
        try:
            st = (folder / e.path).stat()
        except OSError:
            e.on_disk = False
            continue
        e.on_disk = True
        e.disk_size = st.st_size


# --------------------------------------------------------------------------- hashing

def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def hash_files(folder: Path, entries: List[Entry], workers: int = 4) -> Dict[str, str]:
    """SHA1 of every downloaded file. Immich stores the SHA1 of each uploaded file and the
    endpoint /assets/bulk-upload-check finds assets by it, the same way the Immich CLI and
    the iPhone app decide what is already on the server."""
    cache_path = folder / ".homecloud" / "immich-sha1-cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    result: Dict[str, str] = {}
    todo: List[Tuple[str, Path, int, int]] = []
    for e in entries:
        if e.status != "done" or not e.on_disk:
            continue
        p = folder / e.path
        st = p.stat()
        cached = cache.get(e.path)
        if (isinstance(cached, list) and len(cached) == 3 and cached[0] == st.st_size
                and cached[1] == st.st_mtime_ns and isinstance(cached[2], str)):
            result[e.path] = cached[2]
        else:
            todo.append((e.path, p, st.st_size, st.st_mtime_ns))
    if todo:
        total_bytes = sum(t[2] for t in todo)
        say(f"Reading {count(len(todo), 'file')} ({human_bytes(total_bytes)}) to compute checksums...")
        done_files = 0
        done_bytes = 0
        last = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(sha1_of, t[1]): t for t in todo}
            for fut in concurrent.futures.as_completed(futures):
                rel, _, size, mtime_ns = futures[fut]
                try:
                    digest = fut.result()
                except OSError as err:
                    warn(f"cannot read {rel}: {err}")
                    continue
                result[rel] = digest
                cache[rel] = [size, mtime_ns, digest]
                done_files += 1
                done_bytes += size
                if time.time() - last > 10:
                    last = time.time()
                    say(f"  {done_files:,} of {len(todo):,} files ({human_bytes(done_bytes)} of {human_bytes(total_bytes)})")
        try:
            tmp = cache_path.with_name(cache_path.name + ".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, cache_path)
        except OSError:
            pass  # the cache only saves time on the next run
    return result


def lookup_assets(api: Api, hashes: Dict[str, str]) -> Dict[str, Tuple[str, bool]]:
    """Maps manifest path -> (Immich asset id, is in trash) for files Immich holds.
    POST /assets/bulk-upload-check answers "reject"/"duplicate" with the assetId of an asset
    that has the same SHA1, including hidden Live Photo video parts and trashed assets."""
    found: Dict[str, Tuple[str, bool]] = {}
    items = sorted(hashes.items())
    for batch in chunks(items, BATCH):
        resp = api.post("/assets/bulk-upload-check",
                        {"assets": [{"id": rel, "checksum": sha} for rel, sha in batch]})
        for r in resp.get("results", []):
            if r.get("action") == "reject" and r.get("reason") == "duplicate" and r.get("assetId"):
                found[r["id"]] = (r["assetId"], bool(r.get("isTrashed")))
    return found


class State:
    def __init__(self, folder: Path, entries: List[Entry], hashes: Dict[str, str],
                 found: Dict[str, Tuple[str, bool]]) -> None:
        self.folder = folder
        self.entries = entries
        self.hashes = hashes
        self.found = found

    def held(self, e: Entry) -> Optional[str]:
        """Asset id if Immich holds this file outside the trash."""
        hit = self.found.get(e.path)
        if hit and not hit[1]:
            return hit[0]
        return None


def collect(args, api: Api) -> State:
    folder = resolve_dir(args.dir)
    entries = load_manifest(folder)
    stat_files(folder, entries)
    hashes = hash_files(folder, entries, args.hash_workers)
    found = lookup_assets(api, hashes)
    return State(folder, entries, hashes, found)


def resolve_dir(value: Optional[str]) -> Path:
    if not value:
        raise Stop("Give the download folder with --dir DIR.", 2)
    folder = Path(value).expanduser()
    if not folder.is_dir():
        raise Stop(f"Folder not found: {folder}", 2)
    return folder.resolve()


# -------------------------------------------------------------------------- search

def search_assets(api: Api, new_filter: dict, flat_filter: dict) -> List[dict]:
    """All assets matching a filter. Uses the structured filter of Immich v3.2 and later,
    and the older flat fields (deprecated in v3.2, removed in v4) on older v3 servers."""
    items: List[dict] = []
    cursor = None
    try:
        while True:
            body = {"filter": new_filter, "size": 1000}
            if cursor:
                body["cursor"] = cursor
            assets = api.post("/search/metadata", body)["assets"]
            items.extend(assets.get("items") or [])
            cursor = assets.get("nextCursor")
            if not cursor:
                return items
    except ApiError as err:
        if err.status != 400:
            raise
    items = []
    page = 1
    while True:
        body = dict(flat_filter)
        body.update({"size": 1000, "page": page})
        assets = api.post("/search/metadata", body)["assets"]
        items.extend(assets.get("items") or [])
        next_page = assets.get("nextPage")
        if not next_page:
            return items
        page = int(next_page)


def owned_albums(api: Api) -> Dict[str, dict]:
    albums = api.get("/albums", params={"isOwned": "true"}) or []
    by_name: Dict[str, dict] = {}
    for album in albums:
        by_name.setdefault(album.get("albumName", ""), album)
    return by_name


# ------------------------------------------------------------------------ preflight

def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def upload_location(app_dir: Path) -> Optional[Path]:
    env = read_env_file(app_dir / ".env")
    value = env.get("UPLOAD_LOCATION")
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else (app_dir / p)


def immich_usage_bytes(api: Api, user_id: str) -> int:
    try:
        stats = api.get("/server/statistics")
    except ApiError:
        return 0
    for row in stats.get("usageByUser") or []:
        if row.get("userId") == user_id:
            return int(row.get("usage") or 0)
    return 0


def supported_extensions(api: Api) -> Set[str]:
    try:
        types = api.get("/server/media-types")  # public, same list the Immich CLI uses
    except ApiError:
        return set()
    return {ext.lower() for ext in (types.get("image") or []) + (types.get("video") or [])}


def extension(path: str) -> str:
    name = PurePosixPath(path).name
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


class Preflight:
    def __init__(self) -> None:
        self.problems: List[str] = []
        self.notes: List[str] = []


def preflight(args, api: Api, for_upload: bool) -> Tuple[Preflight, dict, List[Entry], Path]:
    pf = Preflight()
    ping(api)
    pf.notes.append(f"Immich {server_version(api)} answers at {api.url}.")
    me, missing = check_key(api)
    if missing:
        pf.problems.append(explain_missing_permissions(missing))
    pf.notes.append(f"API key belongs to {me.get('email')}.")

    folder = resolve_dir(args.dir)
    entries = load_manifest(folder)
    stat_files(folder, entries)
    done = [e for e in entries if e.status == "done"]
    on_disk = [e for e in done if e.on_disk]
    total = sum(e.disk_size for e in on_disk)
    pf.notes.append(f"Manifest: {len(entries):,} files. Downloaded: {len(done):,}. "
                    f"On disk now: {len(on_disk):,} ({human_bytes(total)}).")
    not_done = len(entries) - len(done)
    if not_done:
        pf.notes.append(f"Not downloaded yet (status missing or failed): {count(not_done, 'file')}. "
                        "Immich cannot hold those until the download finishes.")
    if len(on_disk) < len(done):
        pf.problems.append(f"Downloaded but missing from {folder}: {count(len(done) - len(on_disk), 'file')}. "
                           f"Run: {source_step(entries, 1)}")

    exts = supported_extensions(api)
    if exts:
        unsupported = [e.path for e in on_disk if extension(e.path) not in exts]
        if unsupported:
            pf.notes.append(f"File types Immich does not accept, so they are skipped: {count(len(unsupported), 'file')}, "
                            f"for example: {', '.join(unsupported[:5])}")

    if for_upload:
        docker = find_docker()
        if not docker:
            pf.problems.append("Docker is not found. Run the setup script first.")
        else:
            ok, _ = docker_ok(docker, "info")
            if not ok:
                pf.problems.append("Docker Desktop is not running. Open it and wait until it says it is running.")
            else:
                if not args.network:
                    args.network = detect_network(docker) or DEFAULT_NETWORK
                ok, _ = docker_ok(docker, "network", "inspect", args.network)
                if not ok:
                    pf.problems.append(f"The Docker network {args.network} does not exist. Start Immich with "
                                       "the setup script, or pass the right one with --network.")
                else:
                    pf.notes.append(f"The Immich CLI container joins the Docker network {args.network}.")
        if sys.platform == "darwin" and not str(folder).startswith(DOCKER_SHARED_PREFIXES):
            pf.problems.append(f"{folder} is outside the folders Docker Desktop shares by default "
                               "(/Users, /Volumes, /private, /tmp, /var/folders). Move the folder, or add it in "
                               "Docker Desktop > Settings > Resources > File sharing.")
        location = upload_location(Path(args.app_dir).expanduser().resolve())
        if location is None:
            pf.notes.append(f"Could not read UPLOAD_LOCATION from {args.app_dir}/.env, so free space was not checked.")
        else:
            probe = location if location.exists() else location.parent
            try:
                free = shutil.disk_usage(str(probe)).free
            except OSError:
                free = None
            already = immich_usage_bytes(api, me.get("id", ""))
            # Immich keeps a copy of each file; thumbnails and transcoded videos add 10-20%
            # (Immich requirements, Storage). One more GB keeps the disk from filling up.
            need = int(max(0, total - already) * 1.2) + 1_000_000_000
            if free is not None:
                pf.notes.append(f"Immich stores its copy in {location}. Free there: {human_bytes(free)}. "
                                f"Needed: about {human_bytes(need)}.")
                if free < need and not args.ignore_space:
                    pf.problems.append(
                        f"Not enough free space in {location}: {human_bytes(free)} free, about "
                        f"{human_bytes(need)} needed. The upload copies every file into Immich. Free up "
                        "space, or use an External Library, which reads the files where they are "
                        "(scripts/immich/README.md). To try anyway, add --ignore-space.")
        message = machine_learning_warning(api, docker)
        if message:
            pf.notes.append(message)
    return pf, me, entries, folder


def confirm(args, plan: List[str]) -> None:
    say("Plan:")
    for line in plan:
        say("  " + line)
    if args.yes:
        return
    if sys.stdin.isatty():
        answer = input("Continue? Type y and press Return: ").strip().lower()
        if answer in ("y", "yes"):
            return
        raise Stop("Nothing was changed.", 2)
    raise Stop("Nothing was changed. Run again with --yes after the user agrees to the plan above.", 2)


def print_preflight(pf: Preflight) -> None:
    for note in pf.notes:
        say(note)
    for problem in pf.problems:
        say("Problem: " + problem)


# --------------------------------------------------------------------------- upload

class Progress(threading.Thread):
    """Prints a line every minute while the CLI runs without a terminal."""

    def __init__(self, api: Api, interval: float = 60) -> None:
        super().__init__(daemon=True)
        self.api = api
        self.interval = interval
        self.stopped = threading.Event()
        self.started_at = time.time()

    def run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                stats = self.api.get("/assets/statistics")
                count = f"Immich shows {int(stats.get('total', 0)):,} photos and videos."
            except (ApiError, Stop, TypeError, ValueError):
                count = ""
            minutes = int((time.time() - self.started_at) / 60)
            say(f"  Upload still running ({minutes} min). {count}")


def run_upload(args, api: Api, key: str, folder: Path) -> int:
    docker = find_docker()
    if not docker:
        raise Stop("Docker is not found. Run the setup script first.")
    tty = sys.stdout.isatty() and sys.stdin.isatty()
    say(f"Getting the Immich CLI image {args.cli_image} (about 300 MB the first time)...")
    pull = [docker, "pull"] + ([] if tty else ["--quiet"]) + [args.cli_image]
    try:
        pulled = subprocess.run(pull, env=docker_env(docker)).returncode == 0
    except OSError as err:
        raise Stop(f"Could not run docker: {err}") from None
    if not pulled:
        have, _ = docker_ok(docker, "image", "inspect", args.cli_image)
        if not have:
            raise Stop(f"Could not download {args.cli_image}. Check the internet connection and run this again.")
        warn("could not check for a newer CLI image; using the one already on this computer.")
    network = args.network or detect_network(docker) or DEFAULT_NETWORK
    cmd = [docker, "run", "--rm"]
    if tty:
        cmd.append("-t")  # shows the CLI progress bars
    cmd += ["--network", network,
            "-v", f"{folder}:/import:ro",
            "-e", "IMMICH_INSTANCE_URL", "-e", "IMMICH_API_KEY",
            args.cli_image,
            "upload", "--recursive", "--concurrency", str(args.concurrency)]
    if not tty:
        cmd.append("--no-progress")
    cmd.append("/import")
    env = docker_env(docker)
    env["IMMICH_INSTANCE_URL"] = args.cli_url
    env["IMMICH_API_KEY"] = key
    say("Running the official Immich CLI (the API key is passed through the environment):")
    say("  " + " ".join(shlex.quote(c) for c in cmd))
    progress = None
    if not tty:
        progress = Progress(api)
        progress.start()
    try:
        code = subprocess.run(cmd, env=env).returncode
    finally:
        if progress:
            progress.stopped.set()
    if code != 0:
        warn(f"the Immich CLI ended with exit code {code}.")
    return code


def cmd_check(args) -> int:
    api = Api(args.url, load_key(args))
    pf, _, _, _ = preflight(args, api, for_upload=True)
    print_preflight(pf)
    if pf.problems:
        return 1
    say("Ready. Next: import_library.py all --dir DIR (it shows the plan and asks first).")
    return 0


def upload_plan(args, folder: Path) -> List[str]:
    return [f"Copy every photo and video in {folder} into Immich with the official Immich CLI",
            f"(Docker image {args.cli_image}). Files Immich already has are skipped. Your folder is "
            "mounted read-only, so nothing in it changes."]


def cmd_upload(args) -> int:
    key = load_key(args)
    api = Api(args.url, key)
    pf, _, _, folder = preflight(args, api, for_upload=True)
    print_preflight(pf)
    if pf.problems:
        return 1
    confirm(args, upload_plan(args, folder))
    code = run_upload(args, api, key, folder)
    say("Next: import_library.py apply --dir DIR, then verify.")
    return 0 if code == 0 else 1


# ---------------------------------------------------------------------------- apply

def apply_plan(args) -> List[str]:
    plan = ["Mark as favorites the photos and videos that are favorites in the manifest.",
            "Create the manifest's albums that do not exist yet, and add their photos and videos."]
    if args.hidden == "archive":
        plan.append("Archive the items that were hidden in iCloud or archived in Google Photos: they leave the main timeline but stay "
                    "in Archive and in search.")
    plan.append("Nothing is removed or unfavorited. Live Photo video parts are left alone; Immich keeps "
                "them with their photo.")
    return plan


def apply_changes(args, api: Api, state: State) -> int:
    failures = 0
    usable = [e for e in state.entries if e.kind != "live_video"]

    fav_ids = sorted({a for a in (state.held(e) for e in usable if e.favorite) if a})
    for batch in chunks(fav_ids, BATCH):
        api.put("/assets", {"ids": batch, "isFavorite": True})  # PUT /assets, operationId updateAssets
    say(f"Favorites: {len(fav_ids):,} marked.")

    if args.hidden == "archive":
        hidden_ids = sorted({a for a in (state.held(e) for e in usable if e.hidden) if a})
        for batch in chunks(hidden_ids, BATCH):
            api.put("/assets", {"ids": batch, "visibility": "archive"})
        say(f"Hidden in iCloud: {len(hidden_ids):,} archived.")

    wanted: Dict[str, Set[str]] = {}
    for e in usable:
        asset_id = state.held(e)
        if not asset_id:
            continue
        for name in e.albums:
            wanted.setdefault(name, set()).add(asset_id)
    existing = owned_albums(api)
    created = added = already = 0
    for name in sorted(wanted):
        album = existing.get(name)
        if album is None:
            # POST /albums, operationId createAlbum. Not retried, so a timeout cannot create
            # the same album twice; the next run of apply finds it by name.
            album = api.post("/albums", {"albumName": name}, retries=1)
            existing[name] = album
            created += 1
        for batch in chunks(sorted(wanted[name]), BATCH):
            # PUT /albums/{id}/assets, operationId addAssetsToAlbum
            results = api.put(f"/albums/{album['id']}/assets", {"ids": batch}) or []
            for r in results:
                if r.get("success"):
                    added += 1
                elif r.get("error") == "duplicate":
                    already += 1
                else:
                    failures += 1
    say(f"Albums: {len(wanted):,} in the manifest, {created:,} created, {added:,} photos and videos "
        f"added, {already:,} were already there.")
    if failures:
        warn(f"{count(failures, 'item')} could not be added to an album. Run apply again; if it repeats, "
             "check Immich's server log.")
    not_held = sum(1 for e in usable if e.status == "done" and not state.held(e))
    if not_held:
        say(f"Not in Immich yet, so skipped: {count(not_held, 'downloaded file')}. Run upload first.")
    return 1 if failures else 0


def cmd_apply(args) -> int:
    api = Api(args.url, load_key(args))
    ping(api)
    _, missing = check_key(api)
    if missing:
        raise Stop(explain_missing_permissions(missing), 2)
    confirm(args, apply_plan(args))
    state = collect(args, api)
    return apply_changes(args, api, state)


# --------------------------------------------------------------------------- verify

LIVE_PHOTO_NOTE = (
    "How Live Photo video parts are counted: a Live Photo is two files, a photo and a short "
    "video, and the manifest lists both (the video with kind live_video). Immich stores each "
    "file as its own asset with its own checksum. When Immich reads the files' metadata, it "
    "links the video to its photo and hides the video. The timeline then shows one Live Photo, "
    "and Immich's photo and video counts leave the hidden video parts out. This check counts "
    "files, so every video part counts once, like any other file.")


def verify(args, api: Api, state: State) -> int:
    entries = state.entries
    n = len(entries)
    by_status = {s: sum(1 for e in entries if e.status == s) for s in STATUSES}
    by_kind = {k: sum(1 for e in entries if e.kind == k) for k in KINDS}
    held = [e for e in entries if state.held(e)]
    done = [e for e in entries if e.status == "done"]
    missing_on_disk = [e for e in done if not e.on_disk]
    changed = [e for e in done if e.on_disk and e.size is not None and e.size != e.disk_size]
    trashed = [e for e in done if e.path in state.found and state.found[e.path][1]]
    not_in_immich = [e for e in done if e.on_disk and e.path not in state.found]

    say(f"Manifest: {count(n, 'file')}: {count(by_kind['photo'], 'photo')}, {count(by_kind['video'], 'video')}, "
        f"{count(by_kind['live_video'], 'Live Photo video part')}, {count(by_kind['edited'], 'edited version')}.")
    say(f"Immich holds {len(held):,} of {count(n, 'file')}.")
    if by_status["missing"] or by_status["failed"]:
        say(f"  Not downloaded yet (status missing or failed): "
            f"{count(by_status['missing'] + by_status['failed'], 'file')}. "
            f"Finish the copy first ({source_step(entries, 0)}), then run verify again.")
    if missing_on_disk:
        say(f"  Downloaded but no longer in the folder: {count(len(missing_on_disk), 'file')}, for example "
            f"{missing_on_disk[0].path}. Run: {source_step(entries, 1)}")
    if not_in_immich:
        exts = supported_extensions(api)
        unsupported = [e for e in not_in_immich if exts and extension(e.path) not in exts]
        fixable = [e for e in not_in_immich if e not in unsupported]
        if fixable:
            say(f"  In the folder but not in Immich: {count(len(fixable), 'file')}. Run upload again. "
                f"Examples: {', '.join(e.path for e in fixable[:5])}")
        if unsupported:
            say(f"  File types Immich does not accept: {count(len(unsupported), 'file')}, for example "
                f"{', '.join(e.path for e in unsupported[:5])}. They stay in your download folder, "
                "but Immich cannot hold them.")
    if trashed:
        say(f"  In the Immich trash: {count(len(trashed), 'file')}. Restore in Immich: Trash > Restore.")
    if changed:
        say(f"  Size on disk differs from the manifest: {count(len(changed), 'file')}. "
            f"Run: {source_step(entries, 1)}")
    ids = [state.held(e) for e in held]
    if len(set(ids)) < len(ids):
        say(f"  Same content as another file in the manifest: {count(len(ids) - len(set(ids)), 'file')}. "
            "Immich keeps one item for each such group; each file still counts as held.")

    lives = [e for e in held if e.kind == "live_video"]
    if by_kind["live_video"]:
        say(f"Live Photo video parts: {by_kind['live_video']:,} in the manifest, {len(lives):,} in Immich.")
        try:
            stills = search_assets(api, {"isMotion": {"eq": True}}, {"isMotion": True})
            linked = {a.get("livePhotoVideoId") for a in stills if a.get("livePhotoVideoId")}
            n_linked = sum(1 for e in lives if state.held(e) in linked)
            say(f"  Linked to their photo: {n_linked:,}.")
            if n_linked < len(lives):
                waiting = metadata_queue(api)
                more = f" Immich is still reading metadata ({waiting:,} jobs waiting)." if waiting else ""
                say(f"  Not linked yet: {len(lives) - n_linked:,}.{more} Linking happens while Immich "
                    "reads metadata. Until then those video parts show as separate videos. "
                    "Run verify again later.")
        except ApiError as err:
            say(f"  Could not check linking: {err}")
        say("  " + LIVE_PHOTO_NOTE)

    files_ok = len(held) == n
    extras_ok = verify_extras(args, api, state)
    if files_ok and extras_ok:
        say(f"Result: OK. Immich holds {n:,} of {count(n, 'file')}, with favorites and albums.")
        return 0
    if files_ok:
        say(f"Result: all {count(n, 'file')} are in Immich, but favorites, albums or archived items are not "
            "complete. Run: import_library.py apply --dir DIR")
        return 3
    say(f"Result: NOT COMPLETE. Immich holds {len(held):,} of {count(n, 'file')}.")
    return 1


def metadata_queue(api: Api) -> int:
    try:
        q = api.get("/queues/metadataExtraction")  # needs an admin key with queue.read
        stats = q.get("statistics") or {}
        return int(stats.get("active", 0)) + int(stats.get("waiting", 0))
    except (ApiError, TypeError, ValueError):
        return 0


def verify_extras(args, api: Api, state: State) -> bool:
    """Favorites, archive and albums, compared with the manifest. Live Photo video parts
    are left out: Immich hides them and removes them from albums when it links them."""
    ok = True
    usable = [e for e in state.entries if e.kind != "live_video" and state.held(e)]

    want_fav = {state.held(e) for e in usable if e.favorite}
    if want_fav:
        have = {a["id"] for a in search_assets(api, {"isFavorite": {"eq": True}}, {"isFavorite": True})}
        miss = want_fav - have
        say(f"Favorites: {len(want_fav) - len(miss):,} of {len(want_fav):,} marked in Immich.")
        ok = ok and not miss

    if args.hidden == "archive":
        want_arch = {state.held(e) for e in usable if e.hidden}
        if want_arch:
            have = {a["id"] for a in search_assets(api, {"visibility": {"eq": "archive"}},
                                                   {"visibility": "archive"})}
            miss = want_arch - have
            say(f"Hidden in iCloud, archived in Immich: {len(want_arch) - len(miss):,} of {len(want_arch):,}.")
            ok = ok and not miss

    wanted: Dict[str, Set[str]] = {}
    for e in usable:
        for name in e.albums:
            wanted.setdefault(name, set()).add(state.held(e))
    if wanted:
        existing = owned_albums(api)
        complete = 0
        problems: List[str] = []
        for name in sorted(wanted):
            album = existing.get(name)
            if album is None:
                problems.append(f"{name} (missing)")
                continue
            have = {a["id"] for a in search_assets(api, {"albumIds": {"any": [album["id"]]}},
                                                   {"albumIds": [album["id"]]})}
            miss = wanted[name] - have
            if miss:
                problems.append(f"{name} ({len(miss):,} missing)")
            else:
                complete += 1
        say(f"Albums: {complete:,} of {len(wanted):,} complete in Immich.")
        if problems:
            say("  Not complete: " + "; ".join(problems[:10]) + (" ..." if len(problems) > 10 else ""))
            ok = False
    return ok


def cmd_verify(args) -> int:
    api = Api(args.url, load_key(args))
    ping(api)
    state = collect(args, api)
    return verify(args, api, state)


def cmd_all(args) -> int:
    key = load_key(args)
    api = Api(args.url, key)
    pf, _, _, folder = preflight(args, api, for_upload=True)
    print_preflight(pf)
    if pf.problems:
        return 1
    confirm(args, upload_plan(args, folder) + apply_plan(args) +
            ["Then check that Immich holds every file in the manifest (read-only)."])
    upload_code = run_upload(args, api, key, folder)
    say("")
    say("Matching files with Immich by checksum...")
    state = collect(args, api)
    apply_code = apply_changes(args, api, state)
    say("")
    verify_code = verify(args, api, state)
    if verify_code == 0 and (upload_code or apply_code):
        return 1
    return verify_code


# ----------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a Homecloud download folder into Immich and check the result.",
        epilog="Details and the order of steps: scripts/immich/README.md")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, needs_dir: bool = True) -> None:
        p.add_argument("--url", default=DEFAULT_URL, help=f"Immich address (default {DEFAULT_URL})")
        p.add_argument("--key-file", default=str(DEFAULT_KEY_FILE),
                       help="file with the API key (default ~/.homecloud/immich-api-key); "
                            "the IMMICH_API_KEY environment variable wins if set")
        if needs_dir:
            p.add_argument("--dir", "--to", dest="dir", required=True,
                           help="the folder with the copy (DIR); --to works too")
            p.add_argument("--hidden", choices=HIDDEN_CHOICES, default="archive",
                           help="items hidden in iCloud or archived in Google Photos: archive them in Immich (default) or keep them in the timeline")
            p.add_argument("--hash-workers", type=int, default=4, help="files read in parallel (default 4)")

    def upload_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--app-dir", default=str(DEFAULT_APP_DIR),
                       help="folder with Immich's docker-compose.yml and .env (default ~/immich-app)")
        p.add_argument("--cli-image", default=DEFAULT_CLI_IMAGE, help=f"default {DEFAULT_CLI_IMAGE}")
        p.add_argument("--network", default=None,
                       help=f"Docker network of Immich (default: the network of the immich_server container, "
                            f"else {DEFAULT_NETWORK})")
        p.add_argument("--cli-url", default=DEFAULT_CLI_URL, help=f"server address seen by the CLI container (default {DEFAULT_CLI_URL})")
        p.add_argument("--concurrency", type=int, default=4, help="files uploaded at the same time (default 4)")
        p.add_argument("--ignore-space", action="store_true", help="upload even if the free space check fails")

    p = sub.add_parser("save-key", help="save an Immich API key (hidden input)")
    common(p, needs_dir=False)
    p.set_defaults(func=cmd_save_key)

    p = sub.add_parser("check", help="read-only checks and the plan")
    common(p)
    upload_options(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("upload", help="copy the files into Immich with the official CLI")
    common(p)
    upload_options(p)
    p.add_argument("--yes", action="store_true", help="do not ask; the user already agreed")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("apply", help="favorites, albums and hidden items from the manifest")
    common(p)
    p.add_argument("--yes", action="store_true", help="do not ask; the user already agreed")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="read-only: does Immich hold every file in the manifest?")
    common(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("all", help="upload, apply, verify")
    common(p)
    upload_options(p)
    p.add_argument("--yes", action="store_true", help="do not ask; the user already agreed")
    p.set_defaults(func=cmd_all)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Stop as stop:
        say(str(stop))
        return stop.code
    except ApiError as err:
        say(f"Immich refused a request: {err}")
        return 1
    except KeyboardInterrupt:
        say("Stopped. Run the same command again to continue; finished work is kept.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
