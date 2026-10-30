#!/usr/bin/env python3
"""Homecloud: copy your Google Photos library to this computer through your own signed-in
photos.google.com tab, check the copy, and list it for import_library.py.

    python3 scripts/google/google_photos.py prepare --to DIR
    python3 scripts/google/google_photos.py status --to DIR --wait 100
    python3 scripts/google/google_photos.py verify --to DIR
    python3 scripts/google/google_photos.py takeout --to DIR

On Windows, use py instead of python3. Python 3.9 or newer, no extra packages.

How it works: prepare writes a copy of scripts/google/homecloud_google.js with its settings
filled in. You paste it into the browser console of your own photos.google.com tab. It lists
your library, compares its size with the free space, and saves every photo and video into DIR.
It changes nothing in Google Photos. This script never sees your Google password, cookies or
tokens, and it never runs the browser script anywhere but in the tab you open yourself.

Commands
    prepare  Creates DIR, measures its free space, writes DIR/.homecloud/homecloud_google.js,
             copies it to the clipboard, opens https://photos.google.com in Chrome, Edge,
             Brave, Arc, Vivaldi or Opera, and prints your steps. --browser picks the
             browser (default auto: the first one installed, in that order). --no-open and
             --no-clipboard skip those two parts.
    status   Prints the progress the browser script saves in DIR, and moves the files of
             finished zip files from the Downloads folder into DIR (see below). --wait
             SECONDS checks every 10 seconds until the browser script ends and its zip files
             are moved, or the time is up.
    verify   Moves finished zip files from the Downloads folder first, like status. Then it
             compares the browser script's list with the files in DIR, sets each file's
             modification time to the time it was taken, writes DIR/.homecloud/manifest.jsonl
             for import_library.py, and lists what is missing or wrong. The last line starts
             with "Result:".
             status and verify take --downloads FOLDER, more than once if needed, when the
             browser saves downloads somewhere else than the system's Downloads folder.
    takeout  Google's own export, an optional route. Opens https://takeout.google.com and
             prints the steps. With --import, it imports the Takeout archives in DIR into
             Immich with immich-go. It shows the plan and asks first; --yes skips the question.

Exit codes
    0  done; verify: every listed item is on disk with the right size
    1  failed, stopped or not complete
    2  usage error, or nothing was changed because nobody agreed to the plan
    4  status: the browser script is still working or has not started yet, or the browser is
       still saving its zip files in the Downloads folder

Zip files in the Downloads folder. When the page may not fetch some files itself, the browser
script lets the browser download them as zip files from Google, in batches of up to 200 items,
and writes one file per batch in DIR/.homecloud/google-batches: which items went into which
zip file, with their names and sizes, and no download address. status and verify find those
zip files in the Downloads folder (on Windows also the one that Windows names, on Linux the
one in ~/.config/user-dirs.dirs), match each one to its batch by size and by the names inside,
unpack the files of the batch into DIR/<YYYY>/<YYYY-MM>/, check each size against Google's,
and add a line per item to google-collected.jsonl. A zip file is deleted when every file in it
is in DIR; one with anything else in it stays where it is. Downloads that are not finished
(.crdownload) are left alone.

Files in DIR/.homecloud. The browser script writes google-list.json, google-done.jsonl,
google-progress.json and google-batches/. This script writes homecloud_google.js (prepare),
google-collected.jsonl (status and verify), manifest.jsonl and google-problems.txt (verify).
verify leaves alone a manifest that another Homecloud script wrote.

Sources
    Chrome's console paste warning and the words "allow pasting" (translated with the rest of
    DevTools): front_end/panels/console/ConsolePrompt.ts in
        https://github.com/ChromeDevTools/devtools-frontend
    Folders a web page may not pick (home, Desktop, Documents and Downloads themselves,
    ~/Library, /Applications, AppData, Program Files, Windows):
        chrome/browser/file_system_access/chrome_file_system_access_permission_context.cc
        and the prompt "Allow this site to edit files?" in chrome/app/generated_resources.grd,
        both in https://github.com/chromium/chromium
    Brave keeps the folder window behind brave://flags/#file-system-access-api:
        https://github.com/brave/brave-browser/issues/11407
    The browser asks once before a page downloads more than one file ("wants to download
    multiple files", with Allow and Block): chrome/browser/download/download_request_limiter.h
    and components/permissions_strings.grdp in https://github.com/chromium/chromium. Chrome
    saves a download as NAME.crdownload and renames it when it is complete.
    The Downloads folder: on Windows the Known Folder FOLDERID_Downloads
    {374DE290-123F-4565-9164-39C4925E467B} in the registry key HKEY_CURRENT_USER\\Software\\Microsoft\\
    Windows\\CurrentVersion\\Explorer\\User Shell Folders (learn.microsoft.com, KNOWNFOLDERID); on
    Linux XDG_DOWNLOAD_DIR in ~/.config/user-dirs.dirs (freedesktop.org xdg-user-dirs).
    immich-go v0.32.0, the newest release on 2026-10-30 (Immich's docs recommend it for
    Takeout archives): https://github.com/simulot/immich-go/releases/tag/v0.32.0
        Each build is checked against the release's checksums.txt and against the SHA-256
        values below, copied from that file.
        The API key reaches immich-go through the environment variable
        IMMICH_GO_UPLOAD_API_KEY: internal/config/manager.go sets the prefix IMMICH_GO, turns
        "." and "-" into "_" and calls AutomaticEnv; app/upload/upload.go registers --server
        and --api-key on the upload command; docs/environment.md lists the variable.
        https://github.com/simulot/immich-go/blob/v0.32.0/internal/config/manager.go
        https://github.com/simulot/immich-go/blob/v0.32.0/app/upload/upload.go
        https://github.com/simulot/immich-go/blob/v0.32.0/docs/environment.md
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import locale
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
BROWSER_SCRIPT = HERE / "homecloud_google.js"
# prepare replaces exactly this line of homecloud_google.js (the contract with that file).
CONFIG_LINE = 'const HOMECLOUD_CONFIG = {"version": 1, "freeBytes": null, "folderName": null, "lang": "en"};'
CONFIG_START = "const HOMECLOUD_CONFIG = "

SOURCE = "google-web"
STATE_DIR = ".homecloud"
LIST_NAME = "google-list.json"
DONE_NAME = "google-done.jsonl"
PROGRESS_NAME = "google-progress.json"
SCRIPT_NAME = "homecloud_google.js"
MANIFEST_NAME = "manifest.jsonl"
PROBLEMS_NAME = "google-problems.txt"
LIST_FORMAT = "homecloud-google-list"
BATCHES_DIR = "google-batches"
BATCH_FORMAT = "homecloud-google-batch"
COLLECTED_NAME = "google-collected.jsonl"
# Downloads that are not complete yet: Chromium browsers use .crdownload, the rest are other
# download tools that may save into the same folder.
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")
PIECE_SUFFIX = ".homecloud-part"  # a file this script is unpacking; renamed when it is complete
DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"
SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
COPY_CHUNK = 1024 * 1024

PHOTOS_URL = "https://photos.google.com"
TAKEOUT_URL = "https://takeout.google.com"
# Written before the first Takeout import into an empty Immich, so a second run may go on.
TAKEOUT_MARKER = "takeout-import.json"
RESET_WARNING = ("in DevTools, click the gear icon at the top right, and at the bottom of Preferences "
                 "click Restore defaults and reload.")
CHROME_URL = "https://www.google.com/chrome/"

GB = 1000 ** 3
MIN_MARGIN = 5 * GB      # free space the browser script leaves alone, at least
MARGIN_SHARE = 0.02      # or 2% of the disk, when that is more
POLL_SECONDS = 10
PRINT_EVERY = 60         # status --wait prints a change at most once a minute
QUIET_SECONDS = 10 * 60  # no update for this long: the tab may be closed or asleep
EXAMPLES = 10
RUNNING_STATES = ("listing", "downloading")
END_STATES = ("done", "failed", "stopped")
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".3gp", ".3g2", ".avi", ".mkv", ".mts", ".m2ts", ".webm",
              ".wmv", ".mpg", ".mpeg"}

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_RUNNING = 0, 1, 2, 4

DEFAULT_URL = "http://localhost:2283"
# The same file and the same order as import_library.py: IMMICH_API_KEY wins, then this file.
DEFAULT_KEY_FILE = Path.home() / ".homecloud" / "immich-api-key"
DEFAULT_APP_DIR = Path.home() / "immich-app"

IMMICH_GO_VERSION = "v0.32.0"
IMMICH_GO_RELEASE = "https://github.com/simulot/immich-go/releases/download/" + IMMICH_GO_VERSION + "/"
IMMICH_GO_CHECKSUMS = "checksums.txt"
# From checksums.txt of the v0.32.0 release, read on 2026-10-30.
IMMICH_GO_SHA256 = {
    "immich-go_Darwin_arm64.tar.gz": "0c1ce06932a21a738d9adaf2ddafdbf654113fc2791d31b6d2ea0044928e2f43",
    "immich-go_Darwin_x86_64.tar.gz": "35b9456d36c0bbb7d5087d08622b087f849f4f6d9631b15737a22cef39e5a314",
    "immich-go_Linux_arm64.tar.gz": "2c35d9284baae407ef9540bdac5f488971b0bdc7be758a4d7c05ab270af09fdb",
    "immich-go_Linux_x86_64.tar.gz": "6e2ad86bafdadb9466d6515de7cb882726c0aea1a21d51164dff361d7d480a97",
    "immich-go_Windows_arm64.zip": "3ba2ad0f7b175cb31fac15c1f13648f3622c9c82c5cbec0f63a46ce8a1110ac8",
    "immich-go_Windows_x86_64.zip": "37326f95d6654f7f67a5fde3263e96aa8f9cf288dcbb9a2164aafae0aaa144af",
}
IMMICH_GO_KEY_ENV = "IMMICH_GO_UPLOAD_API_KEY"

# Chromium browsers in the order "auto" tries them. Only they let a web page save files into
# a folder you pick (File System Access API); Safari and Firefox do not.
BROWSERS = [("chrome", "Google Chrome"), ("edge", "Microsoft Edge"), ("brave", "Brave"),
            ("arc", "Arc"), ("vivaldi", "Vivaldi"), ("opera", "Opera")]
MAC_APPS = {
    "chrome": ["Google Chrome.app"],
    "edge": ["Microsoft Edge.app"],
    "brave": ["Brave Browser.app"],
    "arc": ["Arc.app"],
    "vivaldi": ["Vivaldi.app"],
    "opera": ["Opera.app", "Opera GX.app"],
}
# Windows: (App Paths name, [(environment variable, path below it), ...]).
WINDOWS_EXES = {
    "chrome": ("chrome.exe", [("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
                              ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
                              ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe")]),
    "edge": ("msedge.exe", [("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
                            ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe")]),
    "brave": ("brave.exe", [("ProgramFiles", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                            ("ProgramFiles(x86)", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                            ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\Application\brave.exe")]),
    # Arc for Windows is an MSIX package; its command, if any, is an app execution alias.
    "arc": ("Arc.exe", [("LOCALAPPDATA", r"Microsoft\WindowsApps\Arc.exe")]),
    "vivaldi": ("vivaldi.exe", [("LOCALAPPDATA", r"Vivaldi\Application\vivaldi.exe"),
                                ("ProgramFiles", r"Vivaldi\Application\vivaldi.exe"),
                                ("ProgramFiles(x86)", r"Vivaldi\Application\vivaldi.exe")]),
    "opera": ("opera.exe", [("LOCALAPPDATA", r"Programs\Opera\opera.exe"),
                            ("LOCALAPPDATA", r"Programs\Opera GX\opera.exe"),
                            ("ProgramFiles", r"Opera\opera.exe")]),
}
LINUX_COMMANDS = {
    "chrome": ["google-chrome", "google-chrome-stable"],
    "edge": ["microsoft-edge", "microsoft-edge-stable"],
    "brave": ["brave-browser", "brave"],
    "arc": [],
    "vivaldi": ["vivaldi", "vivaldi-stable"],
    "opera": ["opera"],
}

# Replaced in tests.
_clock = time.monotonic
_sleep = time.sleep


# ----------------------------------------------------------------------------- output

def say(text: str = "") -> None:
    print(text, flush=True)


class Stop(Exception):
    """Ends the command with a message and an exit code."""

    def __init__(self, message: str, code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.code = code


def human_bytes(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1000.0
    return f"{n:,.1f} TB"


def count(n: int, one: str, many: Optional[str] = None) -> str:
    """'1 item', '2 items'."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def is_windows() -> bool:
    return os.name == "nt"


def python_command() -> str:
    return "py" if is_windows() else "python3"


def quote_arg(text: str) -> str:
    """Quotes a path for the person's shell: on Windows, double quotes and forward slashes, which
    PowerShell and Git Bash both read (Git Bash drops unquoted backslashes); shell quoting elsewhere."""
    if is_windows():
        return '"' + text.replace("\\", "/") + '"'
    return shlex.quote(text)


def kit_command(command: str, folder: Path, extra: str = "") -> str:
    return f"{python_command()} scripts/google/google_photos.py {command} --to {quote_arg(str(folder))}{extra}"


def clean_text(value, limit: int = 300) -> str:
    """Text from the browser script, made safe to print: one line, short, and without links,
    because a download link from Google works like a password until it expires."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\b(?:https?|blob|data):\S+", "(link left out)", value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def taken_day(ms: Optional[int]) -> str:
    when = taken_datetime(ms)
    return when.strftime("%Y-%m-%d") if when else "on an unknown date"


def taken_datetime(ms: Optional[int]) -> Optional[datetime.datetime]:
    if ms is None:
        return None
    try:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(milliseconds=ms)
    except (OverflowError, ValueError):
        return None


def parse_time(value) -> Optional[datetime.datetime]:
    """ISO-8601 from JavaScript's toISOString(), such as 2026-10-30T14:22:33.123Z."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return count(seconds, "second")
    if seconds < 90 * 60:
        return count(round(seconds / 60), "minute")
    return count(round(seconds / 3600), "hour")


def ago(seconds: float) -> str:
    return duration(seconds) + " ago"


# ------------------------------------------------------------------------------ files

def homecloud_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("HOMECLOUD_HOME") or "~/.homecloud"))


def state_path(folder: Path, name: str) -> Path:
    return folder / STATE_DIR / name


def existing_folder(value: str, first: str = "prepare") -> Path:
    folder = Path(os.path.expanduser(value))
    if not folder.is_dir():
        raise Stop(f"No folder at {folder}. Run {first} first: {kit_command(first, folder)}", EXIT_USAGE)
    return folder.resolve()


def write_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Writes a temporary file next to the target and renames it, so a reader never sees half
    a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def file_size(path: Path) -> Optional[int]:
    try:
        st = path.stat()
    except (OSError, ValueError):
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def safe_rel(value) -> Optional[str]:
    """A relative POSIX path that stays inside DIR and outside DIR/.homecloud, or None."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if "\\" in value or "\0" in value or value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or parts[0] == STATE_DIR:
        return None
    return value


def path_key(rel: str) -> str:
    """Two paths with the same key are the same file on this computer. Mac and Windows disks
    ignore upper and lower case and the way an accent is stored."""
    if sys.platform == "darwin" or is_windows():
        return unicodedata.normalize("NFC", rel).casefold()
    return rel


def same_path(a: Path, b: Path) -> bool:
    return path_key(str(a)) == path_key(str(b))


def inside(path: Path, base: Path) -> bool:
    a, b = path_key(str(path)), path_key(str(base))
    return a == b or a.startswith(b.rstrip("/\\") + os.sep)


def foreign_sources(manifest: Path) -> Set[str]:
    """Sources of a manifest that another Homecloud script wrote ("unreadable" for lines that
    are not JSON). Empty when the file is missing or holds only this script's rows."""
    found: Set[str] = set()
    try:
        fh = open(manifest, "r", encoding="utf-8")
    except FileNotFoundError:
        return found
    except OSError:
        return {"unreadable"}
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                source = json.loads(line).get("source")
            except (ValueError, AttributeError):
                found.add("unreadable")
                continue
            if source != SOURCE:
                found.add(str(source))
    return found


def refuse_foreign(manifest: Path, folder: Path) -> Optional[str]:
    others = foreign_sources(manifest)
    if not others:
        return None
    other = folder.parent / ("Google Photos" if folder.name != "Google Photos" else "Google Photos copy")
    return (f"{manifest} was written by another Homecloud script ({', '.join(sorted(others))}). "
            "This script leaves it alone. Use a new, empty folder for Google Photos, for example "
            f"{quote_arg(str(other))}, so the two copies stay apart.")


# ---------------------------------------------------------------------------- prepare

def safety_margin(total: int) -> int:
    return max(MIN_MARGIN, int(total * MARGIN_SHARE))


def picker_problem(folder: Path) -> Optional[str]:
    """Chrome does not let a page pick these folders (Chromium's block list for the File
    System Access API). Returns a plain sentence, or None."""
    home = Path.home()
    example = quote_arg(str(home / "Pictures" / "Google Photos"))
    whole = [(home, "your home folder"), (home / "Desktop", "the Desktop folder"),
             (home / "Documents", "the Documents folder"), (home / "Downloads", "the Downloads folder")]
    for path, label in whole:
        if same_path(folder, path) or (path.exists() and same_path(folder, path.resolve())):
            return (f"The browser does not let a web page save into {label} itself. Use a new folder "
                    f"inside it instead, for example {example}.")
    blocked: List[Path] = []
    if sys.platform == "darwin":
        blocked = [home / "Library", home / "Applications", Path("/Applications"), Path("/System/Volumes")]
    elif is_windows():
        for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot", "APPDATA",
                     "LOCALAPPDATA", "ProgramData"):
            value = os.environ.get(name)
            if value:
                blocked.append(Path(value))
    for base in blocked:
        if inside(folder, base):
            return (f"The browser does not let a web page save into {base} or the folders in it. "
                    f"Use a folder in your home folder, for example {example}.")
    return None


def read_template() -> str:
    try:
        text = BROWSER_SCRIPT.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Stop(f"The browser script {BROWSER_SCRIPT} is missing. Get the whole kit again "
                   "and run this again.") from None
    if text.count(CONFIG_LINE) != 1:
        raise Stop(f"{BROWSER_SCRIPT} does not hold its settings line exactly once, so this script "
                   "cannot fill it in. The two files do not match: get the whole kit again.")
    return text


def fill_settings(template: str, free_bytes: int, folder_name: str, lang: str) -> str:
    settings = {"version": 1, "freeBytes": int(free_bytes), "folderName": folder_name, "lang": lang}
    # The same layout as the line it replaces. ensure_ascii keeps the script ASCII.
    line = CONFIG_START + json.dumps(settings, ensure_ascii=True) + ";"
    return template.replace(CONFIG_LINE, line, 1)


def normalize_lang(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*([A-Za-z]{2,3})(?:[-_.@\s]|$)", value)
    if not match or match.group(1).lower() in ("c",):
        return None
    return match.group(1).lower()


def system_language() -> str:
    """The language of the person's computer, as a two- or three-letter code. English when
    it cannot be found."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=5, check=False).stdout.decode("utf-8", "replace")
            match = re.search(r"\(\s*\"?([A-Za-z]{2,3})", out)
            if match:
                return match.group(1).lower()
        except (OSError, subprocess.SubprocessError):
            pass
    if is_windows():
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            code = normalize_lang(locale.windows_locale.get(langid))
            if code:
                return code
        except (AttributeError, OSError, ValueError):
            pass
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        code = normalize_lang(os.environ.get(name))
        if code:
            return code
    return "en"


def copy_to_clipboard(text: str) -> bool:
    """pbcopy on a Mac; Set-Clipboard through Windows PowerShell on Windows, reading the
    script from standard input (it is ASCII, so the console code page does not matter)."""
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError:
        if is_windows():
            return False
        data = text.encode("utf-8")
    commands: List[List[str]] = []
    if sys.platform == "darwin":
        commands = [["pbcopy"]]
    elif is_windows():
        powershell = shutil.which("powershell") or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        commands = [[powershell, "-NoProfile", "-NonInteractive", "-Command",
                     "Set-Clipboard -Value ([Console]::In.ReadToEnd())"]]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in commands:
        try:
            done = subprocess.run(command, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False


def mac_app_dirs() -> List[Path]:
    return [Path("/Applications"), Path.home() / "Applications"]


def windows_app_path(exe_name: str) -> Optional[str]:
    """The App Paths registry key that installers write for exe_name (per user, then all users)."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    sub = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name
    views = [(winreg.HKEY_CURRENT_USER, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)]
    for root, view in views:
        try:
            with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            path = os.path.expandvars(value.strip().strip('"'))
            if os.path.exists(path):
                return path
    return None


def browser_target(key: str) -> Optional[str]:
    """What opens this browser: an .app bundle on a Mac, an .exe on Windows, a command on
    Linux. None if it is not installed."""
    if sys.platform == "darwin":
        for folder in mac_app_dirs():
            for app in MAC_APPS[key]:
                if (folder / app).is_dir():
                    return str(folder / app)
        return None
    if is_windows():
        exe_name, places = WINDOWS_EXES[key]
        for variable, below in places:
            base = os.environ.get(variable)
            if base and os.path.exists(os.path.join(base, below)):
                return os.path.join(base, below)
        return windows_app_path(exe_name)
    for command in LINUX_COMMANDS[key]:
        found = shutil.which(command)
        if found:
            return found
    return None


def find_browser(choice: str) -> Tuple[Optional[Tuple[str, str, str]], List[str]]:
    """(key, name, target) of the browser to use, and the names of all Chromium browsers
    found."""
    found = []
    for key, name in BROWSERS:
        target = browser_target(key)
        if target:
            found.append((key, name, target))
    names = [name for _, name, _ in found]
    if choice == "auto":
        return (found[0] if found else None), names
    for entry in found:
        if entry[0] == choice:
            return entry, names
    return None, names


def open_in_browser(target: str, url: str) -> bool:
    try:
        if sys.platform == "darwin":
            done = subprocess.run(["open", "-a", target, url], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30, check=False)
            return done.returncode == 0
        flags = {}
        if is_windows():
            flags["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP
        else:
            flags["start_new_session"] = True
        subprocess.Popen([target, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **flags)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def open_default(url: str) -> bool:
    """Opens a page in the default browser: open on a Mac, start on Windows, xdg-open on Linux."""
    try:
        if is_windows():
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            command = ["open", url]
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            command = ["xdg-open", url]
        else:
            return False
        done = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def keys_for_platform() -> Dict[str, str]:
    if sys.platform == "darwin":
        return {"console": "Cmd+Option+J", "devtools": "Cmd+Option+I", "paste": "Cmd+V", "enter": "Return"}
    return {"console": "Ctrl+Shift+J", "devtools": "F12", "paste": "Ctrl+V", "enter": "Enter"}


def cmd_prepare(args) -> int:
    folder = Path(os.path.expanduser(args.to))
    if folder.exists() and not folder.is_dir():
        raise Stop(f"{folder} is a file, not a folder. Pick a folder for the copy.", EXIT_USAGE)
    problem = picker_problem(folder.resolve())
    if problem:
        raise Stop(problem, EXIT_USAGE)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise Stop(f"Could not create {folder}: {err.strerror or err}", EXIT_USAGE) from None
    folder = folder.resolve()
    refusal = refuse_foreign(state_path(folder, MANIFEST_NAME), folder)
    if refusal:
        raise Stop(refusal, EXIT_USAGE)

    usage = shutil.disk_usage(str(folder))
    margin = safety_margin(usage.total)
    allowed = usage.free - margin
    if allowed <= 0:
        raise Stop(f"Only {human_bytes(usage.free)} is free on the disk with {folder}, less than the "
                   f"{human_bytes(margin)} that must stay free for your computer. Free up space, or use "
                   "a folder on a drive with more room.")
    chosen = None
    if not args.no_open:
        chosen, names = find_browser(args.browser)
        if chosen is None:
            if args.browser != "auto" and names:
                wanted = dict(BROWSERS)[args.browser]
                raise Stop(f"{wanted} is not installed on this computer. Installed: {', '.join(names)}. "
                           "Run prepare again without --browser, or with one of those.")
            raise Stop("No Chrome, Edge, Brave, Arc, Vivaldi or Opera is installed on this computer. The "
                       "browser script needs one of them, because only these browsers let a web page save "
                       "files into a folder you pick. Safari and Firefox cannot. Install Google Chrome from "
                       f"{CHROME_URL} and run prepare again.")
    lang = normalize_lang(args.lang) if args.lang else system_language()
    script = fill_settings(read_template(), allowed, folder.name or str(folder), lang or "en")
    script_file = state_path(folder, SCRIPT_NAME)
    write_atomic(script_file, script, encoding="ascii" if script.isascii() else "utf-8")
    earlier = state_path(folder, LIST_NAME).is_file()

    say(f"The browser script is ready: {script_file}")
    if args.no_clipboard:
        clipboard = False
    else:
        clipboard = copy_to_clipboard(script)
        if clipboard:
            say("It is copied to your clipboard.")
        else:
            say("It could not be copied to your clipboard. Open the file above in a text editor, select "
                "everything and copy it.")
    say(f"Free space on the disk with {folder}: {human_bytes(usage.free)}. The browser script keeps "
        f"{human_bytes(margin)} of it free as a safety margin for your computer, so it stops before "
        f"downloading if your library needs more than {human_bytes(allowed)}.")
    say("Immich makes its own copy when you import, so the whole move needs about 2.2 times the size "
        "of your library on this computer, unless Immich keeps its files on another drive.")
    if earlier:
        say("This folder already holds part of a copy. The browser script continues it and downloads "
            "only what is missing.")

    browser_name = "Chrome, Edge, Brave, Arc, Vivaldi or Opera"
    brave = False
    if chosen is not None:
        key, browser_name, target = chosen
        brave = key == "brave"
        if open_in_browser(target, PHOTOS_URL):
            say(f"Google Photos is opening in {browser_name}.")
        else:
            say(f"{browser_name} did not open. Open {PHOTOS_URL} in {browser_name} yourself.")
    print_steps(folder, browser_name, clipboard, brave, script_file)
    return EXIT_OK


def print_steps(folder: Path, browser: str, clipboard: bool, brave: bool, script_file: Path) -> None:
    keys = keys_for_platform()
    enter = keys["enter"]
    steps: List[List[str]] = []
    if brave:
        steps.append(["Brave keeps the folder window that this needs switched off. Open "
                      "brave://flags/#file-system-access-api in Brave, set it to Enabled, and click Relaunch. When the "
                      "move is done, set it back to Default."])
    if not clipboard:
        steps.append([f"Copy the script: open {script_file} in a text editor, select everything and copy it."])
    steps += [
        [f"In {browser}, on {PHOTOS_URL}: if it asks, sign in with the Google account that holds your photos. "
         "If you use more than one account, check the picture at the top right."],
        [f"Open the console: press {keys['console']}. If nothing opens, press {keys['devtools']} and click Console."],
        [f"Click in the console, paste the script with {keys['paste']}, and press {enter}.",
         "The first time, the browser blocks the paste and shows a warning that asks you to type allow pasting. "
         "If your browser uses another language, it shows those words in that language. Type exactly what it "
         f"shows, press {enter}, then paste again and press {enter}. This switches the warning off in this "
         "browser profile. When the move is done, switch it back on: " + RESET_WARNING,
         "Why the warning is there: scammers tell people to paste code into this console to take over their "
         "accounts. Paste only the script that this kit just made, never code that someone sends you."],
        [f"A Homecloud panel appears on the page. Click Start. In the folder window, choose the folder "
         f"\"{folder.name or folder}\" ({folder}). When the browser asks \"Allow this site to edit files?\", "
         "click Allow."],
        ["If the browser says that photos.google.com wants to download multiple files, click Allow. The files "
         "that the page cannot save directly then arrive as zip files in your Downloads folder. Leave them "
         "there: Claude Code moves them into the folder."],
        ["The script lists your library, compares its size with the free space, and downloads every photo and "
         "video into that folder. Leave the tab open, ideally in front, and keep the computer awake and "
         "plugged in."],
        ["When the panel says Done, tell Claude Code. If the panel shows a problem, tell Claude Code what it "
         "says. If the tab closes or the computer sleeps, run prepare again and repeat these steps: the "
         "script continues where it stopped."],
    ]
    say("")
    say("Your steps:")
    for number, (first, *more) in enumerate(steps, 1):
        say(f"  {number}. {first}")
        for line in more:
            say("     " + line)
    say("")
    say("Good to know:")
    say("  - The script reads your library and downloads your files, in your own tab. It changes nothing "
        "in Google Photos and sends your photos nowhere else.")
    say("  - Google's terms forbid automated access and reverse engineering. Google can restrict a whole "
        "account, with Gmail, Drive and YouTube. No such case was found for read-only tools that run in "
        "your own tab, but the risk is there.")
    say("  - Google can change the requests the script uses any day. If it stops with a message, Google "
        f"Takeout still works: {kit_command('takeout', folder)}")
    say("  - Items saved in Storage saver have no original at Google, so you get the copy Google keeps. "
        "Locked Folder and Trash are not included.")
    say("  - Zip files in your Downloads folder: status and verify unpack the files of each one into the folder, "
        "check their sizes, and then delete the zip file. A zip file that holds anything else stays there.")
    say("")
    say(f"Progress: {kit_command('status', folder)}")
    say(f"When the panel says Done: {kit_command('verify', folder)}")


# ---------------------------------------------------------------- zip files in Downloads

def windows_downloads() -> Optional[Path]:
    """The Downloads folder that Windows names, also after the person moved it."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
            value, _kind = winreg.QueryValueEx(key, DOWNLOADS_GUID)
    except OSError:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(os.path.expandvars(value.strip()))


def xdg_downloads() -> Optional[Path]:
    """XDG_DOWNLOAD_DIR from ~/.config/user-dirs.dirs, the Downloads folder on Linux."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    try:
        text = (Path(config) / "user-dirs.dirs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"([^"\n]+)"', text, re.M)
    if not match:
        return None
    return Path(match.group(1).replace("$HOME", str(Path.home())))


def downloads_folders(given: Optional[List[str]] = None) -> List[Path]:
    """--downloads, or the folders where browsers save downloads on this computer."""
    candidates: List[Optional[Path]] = []
    if given:
        candidates = [Path(os.path.expanduser(value)) for value in given]
    else:
        if is_windows():
            candidates.append(windows_downloads())
            profile = os.environ.get("USERPROFILE")
            if profile:
                candidates.append(Path(profile) / "Downloads")
        elif sys.platform != "darwin":
            candidates.append(xdg_downloads())
        candidates.append(Path.home() / "Downloads")
    out: List[Path] = []
    seen: Set[str] = set()
    for candidate in candidates:
        try:
            if candidate is None or not candidate.is_dir():
                continue
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if path_key(str(resolved)) not in seen:
            seen.add(path_key(str(resolved)))
            out.append(resolved)
    return out


def fold_name(name: str) -> str:
    """Names compared the way fold() in homecloud_google.js compares them."""
    return unicodedata.normalize("NFC", name).upper().lower()


def split_name(name: str) -> Tuple[str, str]:
    """(stem, extension), by the rule of splitName() in homecloud_google.js."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1 or len(name) - dot > 12:
        return name, ""
    return name[:dot], name[dot:]


def loose_stem(stem: str) -> str:
    """A stem without the characters that a zip tool or a disk may replace."""
    return re.sub(r'[\x00-\x1f"*/:<>?\\|~_\s]', "", fold_name(stem))


def member_name(info: zipfile.ZipInfo) -> str:
    """A zip entry's file name without its folder. Names without the UTF-8 flag are tried as
    UTF-8 first, since zipfile reads them as cp437."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def finished_line(record) -> bool:
    """A line for an item that is on disk: its file, and the video part of a Live Photo or null."""
    if not isinstance(record, dict) or not isinstance(record.get("mediaKey"), str) or "error" in record:
        return False
    if safe_rel(record.get("path")) is None or not int_or_none(record.get("size")):
        return False
    live = record.get("livePath")
    return live is None or (safe_rel(live) is not None and bool(int_or_none(record.get("liveSize"))))


def parse_batch(data) -> Optional[dict]:
    if not isinstance(data, dict) or data.get("format") != BATCH_FORMAT or data.get("version") != 1:
        return None
    batch_id, zips, raw_items = data.get("batch"), data.get("zips"), data.get("items")
    if not isinstance(batch_id, str) or not batch_id or not isinstance(zips, list) or not isinstance(raw_items, list):
        return None
    parts = []
    for z in zips:
        if not isinstance(z, dict):
            return None
        parts.append({"name": z.get("name") if isinstance(z.get("name"), str) else "",
                      "size": int_or_none(z.get("size")) or None})
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("mediaKey"), str):
            continue
        path, name = safe_rel(raw.get("path")), raw.get("fileName")
        live = raw.get("live") is True
        live_path = safe_rel(raw.get("livePath")) if live else None
        if path is None or not isinstance(name, str) or not name or (live and live_path is None):
            continue
        stem, ext = split_name(name.replace("\\", "/").rsplit("/", 1)[-1])
        items.append({"key": raw["mediaKey"], "name": name, "size": int_or_none(raw.get("size")),
                      "kind": "video" if raw.get("kind") == "video" else "photo", "live": live, "path": path,
                      "live_path": live_path, "stem": fold_name(stem), "loose": loose_stem(stem),
                      "ext": fold_name(ext)})
    return {"batch": batch_id, "zips": parts, "items": items}


def load_batches(folder: Path) -> List[dict]:
    base = state_path(folder, BATCHES_DIR)
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".json"))
    except OSError:
        return []
    batches = []
    for name in names:
        try:
            batch = parse_batch(read_json(base / name))
        except (OSError, ValueError):
            continue
        if batch:
            batches.append(batch)
    return batches


def load_collected(folder: Path) -> Tuple[Dict[str, dict], Set[Tuple[str, int]]]:
    """The items that status or verify moved here from zip files, and the zip files they
    finished, from google-collected.jsonl."""
    records: Dict[str, dict] = {}
    parts: Set[Tuple[str, int]] = set()
    try:
        fh = open(state_path(folder, COLLECTED_NAME), "r", encoding="utf-8", errors="replace")
    except OSError:
        return records, parts
    with fh:
        for line in fh:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if isinstance(record.get("batch"), str) and int_or_none(record.get("part")) is not None:
                parts.add((record["batch"], record["part"]))
            elif finished_line(record):
                old = records.get(record["mediaKey"])
                if old is None or old.get("livePath") is None or record.get("livePath") is not None:
                    records[record["mediaKey"]] = record
    return records, parts


def merge_collected(records: Dict[str, dict], collected: Dict[str, dict]) -> Dict[str, dict]:
    """google-done.jsonl with the lines of google-collected.jsonl: a collected line counts when the
    browser script has no finished line for the item, or one without the video part it brings."""
    for key, record in collected.items():
        current = records.get(key)
        if not finished_line(current) or (current.get("livePath") is None and record.get("livePath") is not None):
            records[key] = record
    return records


def same_download_name(name: str, wanted: str) -> bool:
    """The name the browser gave a download: the name Google sent, perhaps with " (1)" added."""
    if not wanted:
        return False
    stem, ext = split_name(name)
    want_stem, want_ext = split_name(wanted)
    stem = re.sub(r" \(\d+\)$", "", stem)
    return fold_name(stem) == fold_name(want_stem) and fold_name(ext) == fold_name(want_ext)


def item_for(batch: dict, name: str) -> Tuple[Optional[dict], bool]:
    """(the item a file in a zip belongs to, whether it is the video part of a Live Photo)."""
    stem, ext = split_name(name)
    ext = fold_name(ext)
    found = [i for i in batch["items"] if i["stem"] == fold_name(stem)]
    if not found:
        found = [i for i in batch["items"] if i["loose"] == loose_stem(stem)]
    for item in found:
        if ext == item["ext"]:
            return item, False
    for item in found:
        if item["live"] and ext in VIDEO_EXTS:
            return item, True
    return None, False


class Moved:
    """What one look at the Downloads folder did."""

    def __init__(self) -> None:
        self.files = 0
        self.zips = 0
        self.kept: List[str] = []
        self.problems: List[str] = []
        self.full = False


def place_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """Unpacks one file into a piece next to its place, checks it, and renames it into place.
    zipfile checks the CRC of the bytes as it reads them."""
    target.parent.mkdir(parents=True, exist_ok=True)
    piece = target.with_name(f"{target.name}.{os.getpid()}{PIECE_SUFFIX}")
    try:
        with zf.open(info) as src, open(piece, "wb") as dst:
            shutil.copyfileobj(src, dst, COPY_CHUNK)
            dst.flush()
            os.fsync(dst.fileno())
        if file_size(piece) != info.file_size:
            raise zipfile.BadZipFile("a file in the zip ended early")
        os.replace(piece, target)
    except BaseException:
        try:
            os.unlink(piece)
        except OSError:
            pass
        raise


def move_plain(src: Path, target: Path) -> None:
    """Moves a downloaded file into place: a rename on the same disk, else a checked copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, target)
        return
    except OSError:
        pass
    piece = target.with_name(f"{target.name}.{os.getpid()}{PIECE_SUFFIX}")
    try:
        with open(src, "rb") as fin, open(piece, "wb") as fout:
            shutil.copyfileobj(fin, fout, COPY_CHUNK)
            fout.flush()
            os.fsync(fout.fileno())
        if file_size(piece) != file_size(src):
            raise OSError("the copy is incomplete")
        os.replace(piece, target)
    except BaseException:
        try:
            os.unlink(piece)
        except OSError:
            pass
        raise
    os.unlink(src)


def sizes_fit(item: dict, size: int, live_size: Optional[int]) -> bool:
    """Google's size is the photo or video, or for a Live Photo the photo alone or both parts."""
    if item["size"] is None:
        return True
    if size == item["size"]:
        return True
    return item["live"] and live_size is not None and size + live_size == item["size"]


def known_on_disk(folder: Path, rel: str, records: Dict[str, dict], key: str, live: bool) -> Optional[int]:
    """The size of a file that is already in place and that a finished line vouches for."""
    record = records.get(key)
    disk = file_size(folder / rel)
    if disk is None or not finished_line(record):
        return None
    if live:
        ok = record.get("livePath") == rel and record.get("liveSize") == disk
    else:
        ok = record.get("path") == rel and record.get("size") == disk
    return disk if ok else None


def collect_zip(folder: Path, zip_path: Path, batch: dict, records: Dict[str, dict],
                moved: Moved) -> Tuple[bool, List[dict]]:
    """Unpacks the files of one batch from one zip. (every file used, lines for google-collected.jsonl)."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        by_item: Dict[str, Dict[str, zipfile.ZipInfo]] = {}
        extra = 0
        for info in members:
            item, is_video = item_for(batch, member_name(info))
            slot = "video" if is_video else "main"
            if item is None or slot in by_item.get(item["key"], {}):
                extra += 1
                continue
            by_item.setdefault(item["key"], {})[slot] = info
        used_all = extra == 0
        lines = []
        items = {i["key"]: i for i in batch["items"]}
        for key, parts in by_item.items():
            item = items[key]
            main, video = parts.get("main"), parts.get("video")
            if main is None:
                used_all = False
                continue
            live_size = video.file_size if video is not None and item["live"] else None
            if not sizes_fit(item, main.file_size, live_size):
                moved.problems.append(f"{item['name']}: the zip file holds {main.file_size:,} bytes, Google lists "
                                      f"{item['size']:,}. It was not moved; a new run of the browser script "
                                      "downloads it again.")
                used_all = False
                continue
            placed = False
            wanted = [(main, item["path"], False)]
            if live_size is not None:
                wanted.append((video, item["live_path"], True))
            ok = True
            for info, rel, live in wanted:
                disk = file_size(folder / rel)
                if disk is not None:
                    if disk != info.file_size and known_on_disk(folder, rel, records, key, live) is None:
                        moved.problems.append(f"{rel} is already in the folder with another size. It was left "
                                              "as it is.")
                        ok = used_all = False
                    continue
                place_member(zf, info, folder / rel)
                moved.files += 1
                placed = True
            if not ok:
                continue
            old = records.get(key)
            if placed or not finished_line(old) or (old.get("livePath") is None and live_size is not None):
                main_size = file_size(folder / item["path"]) or main.file_size
                line = {"mediaKey": key, "path": item["path"], "size": main_size,
                        "livePath": item["live_path"] if live_size is not None else None,
                        "liveSize": file_size(folder / item["live_path"]) if live_size is not None else None}
                lines.append(line)
                records[key] = line
        return used_all, lines


def collect_downloads(folder: Path, places: List[Path]) -> Tuple[Moved, int, int]:
    """Moves the files of the zip files from the browser script's batches out of the Downloads
    folder into DIR. (what it did, zip files still to come, zip files in all)."""
    moved = Moved()
    batches = load_batches(folder)
    if not batches:
        return moved, 0, 0
    done_records, _bad = load_done(folder)
    collected, parts_done = load_collected(folder)
    records = merge_collected(done_records, collected)
    pending: Dict[Tuple[str, int], Tuple[dict, dict]] = {}
    total = 0
    for batch in batches:
        for index, z in enumerate(batch["zips"]):
            total += 1
            if (batch["batch"], index) not in parts_done:
                pending[(batch["batch"], index)] = (batch, z)
    for place in places:
        if not pending or moved.full:
            break
        try:
            entries = sorted(os.scandir(place), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if not pending or moved.full:
                break
            name = entry.name
            if name.startswith(".") or name.lower().endswith(PARTIAL_SUFFIXES) or name.endswith(PIECE_SUFFIX):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            fits = [k for k, (_b, z) in pending.items()
                    if z["size"] == size or (z["size"] is None and same_download_name(name, z["name"]))]
            if not fits:
                continue
            path = Path(entry.path)
            try:
                found = collect_file(folder, path, size, fits, pending, records, moved)
            except OSError as err:
                if getattr(err, "errno", None) == 28:
                    moved.full = True
                    moved.problems.append("The disk is full. Free some space, then run status or verify again.")
                    break
                moved.problems.append(f"{name} in {place} could not be moved ({err.strerror or err}).")
                continue
            except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError) as err:
                moved.problems.append(f"{name} in {place} could not be unpacked ({clean_text(str(err), 80)}). It "
                                      "stays there.")
                continue
            if found:
                pending.pop(found, None)
    return moved, len(pending), total


def collect_file(folder: Path, path: Path, size: int, fits: List[Tuple[str, int]],
                 pending: Dict[Tuple[str, int], Tuple[dict, dict]], records: Dict[str, dict],
                 moved: Moved) -> Optional[Tuple[str, int]]:
    """Handles one file in the Downloads folder that has the size of a zip file to come."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = [member_name(i) for i in zf.infolist() if not i.is_dir()]
        best, best_hits = None, 0
        for part in fits:
            batch = pending[part][0]
            hits = sum(1 for n in names if item_for(batch, n)[0] is not None)
            if hits > best_hits:
                best, best_hits = part, hits
        if best is None:
            return None
        batch = pending[best][0]
        used_all, lines = collect_zip(folder, path, batch, records, moved)
        append_collected(folder, lines + [{"batch": best[0], "part": best[1], "files": len(names)}])
        moved.zips += 1
        if used_all:
            try:
                os.unlink(path)
            except OSError:
                moved.kept.append(path.name)
        else:
            moved.kept.append(path.name)
        return best
    # A plain file: Google sends a single video without a zip.
    for part in fits:
        batch = pending[part][0]
        if len(batch["zips"]) != 1 or len(batch["items"]) != 1:
            continue
        item = batch["items"][0]
        stem, ext = split_name(path.name)
        stem = re.sub(r" \(\d+\)$", "", stem)
        if item["live"] or fold_name(ext) != item["ext"] or (fold_name(stem) != item["stem"] and item["size"] != size):
            continue
        if item["size"] is not None and size != item["size"]:
            continue
        target = folder / item["path"]
        disk = file_size(target)
        if disk is None:
            move_plain(path, target)
            moved.files += 1
        elif disk != size and known_on_disk(folder, item["path"], records, item["key"], False) is None:
            moved.problems.append(f"{item['path']} is already in the folder with another size. It was left as it is.")
            return None
        else:
            moved.kept.append(path.name)
        line = {"mediaKey": item["key"], "path": item["path"], "size": file_size(target) or size,
                "livePath": None, "liveSize": None}
        append_collected(folder, [line, {"batch": part[0], "part": part[1], "files": 1}])
        records[item["key"]] = line
        moved.zips += 1
        return part
    return None


def append_collected(folder: Path, lines: List[dict]) -> None:
    """Adds whole lines to google-collected.jsonl, which only this script writes."""
    if not lines:
        return
    text = "".join(json.dumps(line, ensure_ascii=True, separators=(",", ":")) + "\n" for line in lines)
    path = state_path(folder, COLLECTED_NAME)
    with open(path, "a", encoding="ascii", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def collect_and_say(folder: Path, places: List[Path], quiet_when_idle: bool = True) -> Tuple[int, int]:
    """Runs one collection and prints what it did. (zip files still to come, zip files in all)."""
    try:
        moved, waiting, total = collect_downloads(folder, places)
    except OSError as err:
        say(f"The Downloads folder could not be read ({err.strerror or err}).")
        return 0, 0
    if moved.files or moved.zips:
        say(f"Moved {count(moved.files, 'file')} from {count(moved.zips, 'download')} in the Downloads folder "
            f"into {folder}.")
    if moved.kept:
        say(f"Left in the Downloads folder: {', '.join(moved.kept[:5])}{' ...' if len(moved.kept) > 5 else ''}. "
            "They hold files that are not in the list, or a file named above did not fit.")
    for line in moved.problems[:EXAMPLES]:
        say(line)
    if total and not places:
        say("No Downloads folder was found on this computer. Run the command again with --downloads FOLDER, "
            "the folder where the browser saves downloads.")
    elif total and not quiet_when_idle:
        say(f"Zip files from the browser: {total - waiting:,} of {total:,} moved into the folder.")
    return waiting, total


# ----------------------------------------------------------------------------- status

def read_progress(folder: Path) -> Optional[dict]:
    path = state_path(folder, PROGRESS_NAME)
    for attempt in (1, 2):
        try:
            data = read_json(path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt == 1:
                _sleep(0.5)  # caught while the browser rewrote it
                continue
            return {"state": "unreadable"}
        return data if isinstance(data, dict) else {"state": "unreadable"}
    return {"state": "unreadable"}


def number(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0


def describe_progress(progress: Optional[dict], folder: Path,
                      now: Optional[datetime.datetime] = None) -> Tuple[str, List[str], tuple]:
    """(state, lines to print, what changed) for one look at google-progress.json."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if progress is None:
        lines = [f"The browser script has not saved anything in {folder} yet. Paste it in the Google Photos "
                 "tab, click Start and choose this folder. The steps are in the output of prepare."]
        return "not started", lines, ("not started",)
    state = progress.get("state") if isinstance(progress.get("state"), str) else "unknown"
    listed, done, failed = number(progress.get("listed")), number(progress.get("done")), number(progress.get("failed"))
    bytes_done, bytes_total = number(progress.get("bytesDone")), number(progress.get("bytesTotal"))
    message = clean_text(progress.get("message"))
    updated = parse_time(progress.get("updated"))
    of_listed = f"{done:,} of {count(listed, 'item')}"
    if state == "listing":
        lines = [f"Listing your library: {count(listed, 'item')} so far."]
    elif state == "downloading":
        percent = f" ({done * 100 // listed}%)" if listed else ""
        size = f", {human_bytes(bytes_done)} of {human_bytes(bytes_total)}" if bytes_total else ""
        lines = [f"Downloading: {of_listed}{percent}{size}. Failed so far: {failed:,}."]
    elif state == "done":
        lines = [f"Done: {of_listed} saved, {failed:,} failed."]
    elif state == "stopped":
        lines = [f"Stopped: {of_listed} saved, {failed:,} failed."]
    elif state == "failed":
        lines = [f"The browser script stopped because of a problem: {of_listed} saved, {failed:,} failed."]
    elif state == "unreadable":
        lines = ["The progress file cannot be read right now."]
    else:
        lines = [f"The progress file has a state this script does not know: {clean_text(state, 40)}."]
    if message:
        lines.append("Message from the browser script: " + message)
    quiet = None
    if updated:
        quiet = (now - updated).total_seconds()
        lines.append("Last update: " + ago(quiet) + ".")
    if state in RUNNING_STATES and quiet is not None and quiet > QUIET_SECONDS:
        lines.append(f"No update for {int(quiet // 60)} minutes. Is the Google Photos tab still open? If the "
                     "tab closed or the computer slept, run prepare again and repeat the steps: the script "
                     "continues where it stopped.")
    return state, lines, (state, listed, done, failed, message)


def next_step(state: str, folder: Path, progress: Optional[dict]) -> List[str]:
    if state == "done":
        lines = [f"Next: {kit_command('verify', folder)}"]
        if progress and number(progress.get("failed")):
            lines.insert(0, "Some items failed. A second run of the browser script in the same folder tries "
                            "them again. verify lists them.")
        return lines
    if state == "stopped":
        return ["To continue, run prepare again and repeat the steps in the same folder. The script skips "
                "what is already saved."]
    if state == "failed":
        return ["Tell Claude Code what the message says. If Google changed something, the browser script "
                f"cannot work until it is updated. Google Takeout still works: {kit_command('takeout', folder)}"]
    return []


def exit_code_for(state: str) -> int:
    if state == "done":
        return EXIT_OK
    if state in ("failed", "stopped"):
        return EXIT_FAIL
    return EXIT_RUNNING


def zips_still_due(progress: Optional[dict], waiting: int, state: str) -> bool:
    """Zip files are still to come while the browser script runs, and for 10 minutes after it ends."""
    if not waiting:
        return False
    if state not in END_STATES:
        return True
    updated = parse_time(progress.get("updated")) if progress else None
    if updated is None:
        return False
    return (datetime.datetime.now(datetime.timezone.utc) - updated).total_seconds() <= QUIET_SECONDS


def cmd_status(args) -> int:
    folder = existing_folder(args.to)
    places = downloads_folders(getattr(args, "downloads", None))
    wait = max(0.0, float(args.wait or 0))
    deadline = _clock() + wait
    if wait:
        say(f"Checking every {POLL_SECONDS} seconds for up to {duration(wait)}.")
    shown: Optional[tuple] = None
    shown_at = 0.0
    while True:
        waiting, total = collect_and_say(folder, places)
        progress = read_progress(folder)
        state, lines, signature = describe_progress(progress, folder)
        now = _clock()
        due = zips_still_due(progress, waiting, state)
        finished = (state in END_STATES and not due) or now >= deadline
        # Print the first look, then a change at most once a minute, and the last change.
        if shown is None or (signature != shown and (finished or now - shown_at >= PRINT_EVERY)):
            for line in lines:
                say(line)
            shown, shown_at = signature, now
        if finished:
            break
        _sleep(max(0.0, min(POLL_SECONDS, deadline - now)))
    if total:
        say(f"Zip files from the browser: {total - waiting:,} of {total:,} moved into the folder.")
        if waiting and state in END_STATES and due:
            say("The browser is still saving the rest in the Downloads folder. Check again in a minute.")
        elif waiting and state in END_STATES:
            say(f"{count(waiting, 'zip file')} did not arrive in the Downloads folder. If the browser's download "
                "list shows a failed download, or the browser blocked downloads from photos.google.com, click "
                "Start in the panel again: it downloads what is missing. If the browser saves downloads in "
                "another folder, add --downloads FOLDER to this command.")
    if due and state in END_STATES:
        return EXIT_RUNNING
    for line in next_step(state, folder, progress):
        say(line)
    if state not in END_STATES and wait:
        say(f"Not finished after {duration(wait)}. Check again later.")
    return exit_code_for(state)


# ----------------------------------------------------------------------------- verify

class Item:
    __slots__ = ("key", "taken", "name", "size", "kind", "live", "original", "favorite", "archived",
                 "albums", "path", "live_path")

    def __init__(self, raw: dict) -> None:
        self.key: str = raw["mediaKey"]
        taken = raw.get("takenTime")
        self.taken = None
        if isinstance(taken, (int, float)) and not isinstance(taken, bool) and taken == taken and abs(taken) < 1e16:
            self.taken = int(taken)
        self.path = safe_rel(raw.get("path"))
        name = raw.get("fileName")
        self.name = name if isinstance(name, str) and name.strip() else (
            self.path.rsplit("/", 1)[-1] if self.path else self.key)
        size = raw.get("size")
        self.size = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
        kind = raw.get("kind")
        if kind not in ("photo", "video"):
            kind = "video" if extension(self.path or self.name) in VIDEO_EXTS else "photo"
        self.kind = kind
        self.live = raw.get("live") is True
        original = raw.get("originalQuality")
        self.original = original if isinstance(original, bool) else None
        self.favorite = raw.get("favorite") is True
        self.archived = raw.get("archived") is True
        albums = raw.get("albums")
        self.albums = sorted({a for a in albums if isinstance(a, str) and a.strip()}) if isinstance(albums, list) else []
        self.live_path = safe_rel(raw.get("livePath"))


def extension(name: str) -> str:
    tail = name.rsplit("/", 1)[-1]
    return ("." + tail.rsplit(".", 1)[1].lower()) if "." in tail else ""


def load_list(folder: Path) -> Tuple[dict, List[Item], int, int]:
    """(the list file, its items, entries that cannot be read, duplicate entries)."""
    path = state_path(folder, LIST_NAME)
    try:
        data = read_json(path)
    except FileNotFoundError:
        raise Stop(f"There is no list from the browser script in {path.parent} yet. The browser script "
                   "writes it after it lists your library.") from None
    except (OSError, ValueError) as err:
        raise Stop(f"{path} cannot be read ({err}). Run the browser script again in the same folder.") from None
    if not isinstance(data, dict) or data.get("format") != LIST_FORMAT:
        raise Stop(f"{path} is not a list from the Homecloud browser script.")
    if data.get("version") != 1:
        raise Stop(f"{path} comes from another version of the browser script (version "
                   f"{clean_text(str(data.get('version')), 20)}). Get the whole kit again, so both parts match.")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise Stop(f"{path} has no list of items.")
    items: List[Item] = []
    seen: Set[str] = set()
    unreadable = duplicates = 0
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("mediaKey"), str) or not raw.get("mediaKey"):
            unreadable += 1
            continue
        if raw["mediaKey"] in seen:
            duplicates += 1
            continue
        seen.add(raw["mediaKey"])
        items.append(Item(raw))
    return data, items, unreadable, duplicates


def load_done(folder: Path) -> Tuple[Dict[str, dict], int]:
    """The last line for each mediaKey in google-done.jsonl, and the number of lines that
    cannot be read."""
    records: Dict[str, dict] = {}
    bad = 0
    try:
        fh = open(state_path(folder, DONE_NAME), "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return records, 0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(record, dict) and isinstance(record.get("mediaKey"), str):
                records[record["mediaKey"]] = record
            else:
                bad += 1
    return records, bad


def int_or_none(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def set_taken_time(path: Path, taken_ms: Optional[int]) -> Optional[bool]:
    """Sets the modification time to the taken time. True if changed, False if it already was
    (within 2 seconds, the precision of FAT and exFAT drives), None if it failed."""
    if taken_ms is None:
        return False
    target = taken_ms * 1_000_000
    try:
        st = path.stat()
        if abs(st.st_mtime_ns - target) < 2_000_000_000:
            return False
        os.utime(path, ns=(st.st_atime_ns, target))
        return True
    except (OSError, ValueError, OverflowError):
        return None


def video_part_name(item: Item, live_path: str) -> str:
    """The photo's original name with the video file's extension, such as IMG_0001.MOV."""
    on_disk = live_path.rsplit("/", 1)[-1]
    if "." not in on_disk:
        return on_disk
    stem = item.name.rsplit(".", 1)[0] if "." in item.name else item.name
    return stem + on_disk[on_disk.rindex("."):]


def manifest_row(item: Item, path: str, filename: str, size: Optional[int], kind: str, status: str,
                 variant: str) -> dict:
    when = taken_datetime(item.taken)
    return {
        "asset_id": "google:" + item.key,
        "filename": filename,
        "path": path,
        "size": size,
        "sha256": None,
        "created": when.isoformat(timespec="seconds") if when else None,
        "kind": kind,
        "favorite": item.favorite,
        "hidden": item.archived,
        "albums": list(item.albums),
        "source": SOURCE,
        "status": status,
        "variant": variant,
    }


def leftover_pieces(folder: Path) -> int:
    """Files ending in .crswap or .homecloud-part: Chrome and this script write into one while a
    file arrives and rename it when it is complete, so one that is left over is unfinished."""
    n = 0
    for _base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != STATE_DIR]
        n += sum(1 for f in files if f.lower().endswith((".crswap", PIECE_SUFFIX)))
    return n


class Report:
    """Problem lines by kind, in plain words."""

    def __init__(self) -> None:
        self.groups: Dict[str, List[str]] = {}

    def add(self, group: str, line: str) -> None:
        self.groups.setdefault(group, []).append(line)

    def total(self) -> int:
        return sum(len(v) for v in self.groups.values())


PROBLEM_TITLES = [
    ("missing", "Not downloaded yet"),
    ("failed", "The browser script could not download"),
    ("wrong", "Wrong size on disk"),
    ("unchecked", "On disk, but without a record of its size, so it cannot be checked"),
    ("live", "Live Photo video parts missing or wrong"),
    ("paths", "Not usable in the list"),
]


def cmd_verify(args) -> int:
    try:
        folder = existing_folder(args.to)
    except Stop as stop:
        say(str(stop))
        say("Result: NOT CHECKED. There is no folder to check.")
        return stop.code
    manifest = state_path(folder, MANIFEST_NAME)
    refusal = refuse_foreign(manifest, folder)
    if refusal:
        say(refusal)
        say("Result: NOT CHECKED. This folder holds another script's copy, so nothing was changed.")
        return EXIT_USAGE
    collect_and_say(folder, downloads_folders(getattr(args, "downloads", None)), quiet_when_idle=False)
    progress = read_progress(folder)
    running = progress is not None and progress.get("state") in RUNNING_STATES
    try:
        meta, items, unreadable, duplicates = load_list(folder)
    except Stop as stop:
        say(str(stop))
        if running:
            say("The browser script is still working. Run verify again when the panel says Done.")
        say("Result: NOT COMPLETE. There is no list from the browser script to check.")
        return EXIT_FAIL
    records, bad_lines = load_done(folder)
    records = merge_collected(records, load_collected(folder)[0])

    report = Report()
    rows: List[dict] = []
    used: Dict[str, Item] = {}
    ok = stamped = stamp_failed = 0
    live_expected = live_ok = no_video_part = storage_saver = 0
    video_failed: List[str] = []
    photos = sum(1 for i in items if i.kind == "photo")
    videos = len(items) - photos
    lives = sum(1 for i in items if i.live)
    album_members: Dict[str, int] = {}

    for item in items:
        day = taken_day(item.taken)
        if item.original is False:
            storage_saver += 1
        for title in item.albums:
            album_members[title] = album_members.get(title, 0) + 1
        # The last line in google-done.jsonl for this item: a finished download, an error, or none.
        record = records.get(item.key) or {}
        finished = "error" not in record and isinstance(record.get("path"), str)
        error = (clean_text(record.get("error")) or "no reason given") if "error" in record else ""
        path = (safe_rel(record.get("path")) if finished else None) or item.path
        if path is None:
            report.add("paths", f"{item.name}, taken {day}: the list gives no usable file path for it.")
            continue
        if path_key(path) in used:
            other = used[path_key(path)]
            report.add("paths", f"{item.name}, taken {day}: the list puts it in the same file as "
                                f"{other.name} ({path}).")
            continue
        used[path_key(path)] = item

        # Google's size when the list has it, else the size the browser script wrote.
        expected = item.size if item.size is not None else (
            int_or_none(record.get("size")) if finished else None)
        disk = file_size(folder / path)
        if disk is None:
            status = "failed" if error else "missing"
            if error:
                report.add("failed", f"{item.name}, taken {day}: {error}")
            else:
                report.add("missing", f"{item.name}, taken {day}, should be at {path}")
        elif expected is None:
            status = "missing"
            report.add("unchecked", f"{item.name}, taken {day}, at {path}")
        elif disk != expected:
            status = "failed"
            if disk < expected:
                report.add("wrong", f"{item.name}, taken {day}: only {disk:,} of {expected:,} bytes, an "
                                    f"unfinished download ({path})")
            else:
                report.add("wrong", f"{item.name}, taken {day}: {disk:,} bytes on disk, {expected:,} "
                                    f"expected ({path})")
        else:
            status = "done"
        rows.append(manifest_row(item, path, item.name, disk if status == "done" else expected,
                                 item.kind, status, "original"))

        # A Live Photo saved without its video part has a finished line with livePath null.
        still_only = finished and item.live and "livePath" in record and record.get("livePath") is None
        live_path = None if still_only else (
            (safe_rel(record.get("livePath")) if finished else None) or item.live_path)
        live_status = None
        if live_path and path_key(live_path) in used:
            report.add("paths", f"{item.name}, taken {day}: the list puts its video part in the file of "
                                f"another item ({live_path}).")
            live_status = "failed"
        elif live_path:
            used[path_key(live_path)] = item
            live_expected += 1
            video_name = video_part_name(item, live_path)
            want = int_or_none(record.get("liveSize")) if finished else None
            live_disk = file_size(folder / live_path)
            if live_disk is None:
                live_status = "failed" if error else "missing"
                report.add("live", f"{video_name}, the video part of {item.name}, taken {day}: not on disk "
                                   f"({live_path})")
            elif (want is not None and live_disk != want) or (want is None and live_disk == 0):
                live_status = "failed"
                report.add("live", f"{video_name}, the video part of {item.name}, taken {day}: {live_disk:,} "
                                   f"bytes on disk, {want if want is not None else 'more'} expected ({live_path})")
            else:
                live_status = "done"
                live_ok += 1
            rows.append(manifest_row(item, live_path, video_name,
                                     live_disk if live_status == "done" else want,
                                     "live_video", live_status, "live"))
        elif item.live and status == "done" and record.get("liveError"):
            video_failed.append(f"{item.name}, taken {day}: {clean_text(record.get('liveError'), 120)}")
        elif item.live:
            no_video_part += 1

        if status == "done" and live_status in (None, "done"):
            ok += 1
        for done_path, done_status in ((path, status), (live_path, live_status)):
            if done_path and done_status == "done":
                changed = set_taken_time(folder / done_path, item.taken)
                if changed:
                    stamped += 1
                elif changed is None:
                    stamp_failed += 1

    listed = len(items) + unreadable
    if unreadable:
        report.add("paths", f"{count(unreadable, 'entry', 'entries')} in the list cannot be read (no mediaKey).")
    totals = meta.get("totals") if isinstance(meta.get("totals"), dict) else {}
    total_count = int_or_none(totals.get("count"))
    total_bytes = int_or_none(totals.get("bytes"))
    count_differs = total_count is not None and total_count != listed + duplicates

    # Summary
    made = parse_time(meta.get("created"))
    made_text = f", made {made.strftime('%Y-%m-%d %H:%M')} UTC" if made else ""
    size_text = f", {human_bytes(total_bytes)}" if total_bytes is not None else ""
    say(f"List from the browser script{made_text}: {count(listed, 'item')}{size_text}: "
        f"{count(photos, 'photo')} and {count(videos, 'video')}, {count(lives, 'Live Photo')}.")
    if duplicates:
        say(f"  The list names {count(duplicates, 'item')} twice. Each one counts once.")
    if count_differs:
        say(f"  The list's own total says {count(total_count, 'item')}, but it holds {listed + duplicates:,}. "
            "The listing may have stopped early.")
    if running:
        say("The browser script is still working, so the copy is not finished yet.")
    say(f"On disk with the right size: {ok:,} of {count(listed, 'item')}.")
    if live_expected:
        say(f"Live Photo video parts: {live_ok:,} of {live_expected:,} on disk with the right size.")
    if no_video_part:
        say(f"Live Photos without a separate video file: {no_video_part:,}. They are kept as the file Google "
            "gave for them.")
    if video_failed:
        say(f"Live Photos saved without their video part, because it did not download: {len(video_failed):,}. "
            "The photo is complete. A new run of the browser script in this folder tries the video parts again.")
        for line in video_failed[:EXAMPLES]:
            say("  " + line)
    problems_file = state_path(folder, PROBLEMS_NAME)
    full: List[str] = []
    for group, title in PROBLEM_TITLES:
        lines = report.groups.get(group, [])
        if not lines:
            continue
        say(f"{title}: {len(lines):,}.")
        for line in lines[:EXAMPLES]:
            say("  " + line)
        if len(lines) > EXAMPLES:
            say(f"  ...and {len(lines) - EXAMPLES:,} more, all in {problems_file}")
        full.append(f"{title}: {len(lines):,}")
        full.extend("  " + line for line in lines)
    if bad_lines:
        say(f"Lines in {DONE_NAME} that cannot be read: {bad_lines:,}. The items they name count as not downloaded.")
    if storage_saver:
        say(f"Saved at Google in Storage saver: {count(storage_saver, 'item')}. Google has no original of these, "
            "so the copy on disk is the reduced copy Google keeps.")
    albums = meta.get("albums") if isinstance(meta.get("albums"), list) else []
    short_albums = []
    for album in albums:
        if isinstance(album, dict) and isinstance(album.get("title"), str):
            want = int_or_none(album.get("count"))
            have = album_members.get(album["title"], 0)
            if want is not None and have < want:
                short_albums.append(f"{album['title']} ({have:,} of {want:,})")
    if albums:
        say(f"Albums: {len(albums):,} in Google Photos.")
        if short_albums:
            say("  Albums with fewer items in the list than Google counts (photos that others added to a shared "
                "album stay theirs): " + "; ".join(short_albums[:5]) + (" ..." if len(short_albums) > 5 else ""))
    if stamped or stamp_failed:
        say(f"Dates: set {count(stamped, 'file')} to the time they were taken."
            + (f" Could not set {count(stamp_failed, 'file')}." if stamp_failed else ""))
    pieces = leftover_pieces(folder)
    if pieces:
        say(f"Unfinished download pieces (files ending in .crswap or {PIECE_SUFFIX}): {pieces:,}. They hold "
            "nothing you need. Delete them, or leave them; import skips them.")

    write_atomic(manifest, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    kinds = {k: sum(1 for r in rows if r["kind"] == k) for k in ("photo", "video", "live_video")}
    say(f"Wrote {manifest}: {count(len(rows), 'file')} ({count(kinds['photo'], 'photo')}, "
        f"{count(kinds['video'], 'video')}, {count(kinds['live_video'], 'Live Photo video part')}) "
        "for import_library.py.")
    if full:
        write_atomic(problems_file, "\n".join(full) + "\n")
    else:
        try:
            problems_file.unlink()
        except OSError:
            pass

    complete = ok == listed and listed > 0 and not count_differs and report.total() == 0
    if complete:
        say(f"Next, the import into Immich: {python_command()} scripts/immich/import_library.py all "
            f"--dir {quote_arg(str(folder))} --yes")
        say("Last step in the browser: switch the console's paste warning back on. " + RESET_WARNING
            + " In Brave, also set brave://flags/#file-system-access-api back to Default.")
        say(f"Result: OK. {ok:,} of {count(listed, 'item')} are on disk with the right size.")
        return EXIT_OK
    if listed == 0:
        say("Result: NOT COMPLETE. The list is empty, so there is nothing to check.")
        return EXIT_FAIL
    say(f"Result: NOT COMPLETE. {ok:,} of {count(listed, 'item')} are on disk with the right size. To get the "
        f"rest, run {kit_command('prepare', folder)} and repeat the browser steps in the same folder: the "
        "script downloads only what is missing. Then run verify again.")
    return EXIT_FAIL


# ---------------------------------------------------------------------------- takeout

TAKEOUT_PART = re.compile(r"^(takeout-.+?)-(\d{3})\.zip$", re.IGNORECASE)


def takeout_archives(folder: Path) -> List[Path]:
    try:
        names = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        return []
    return [p for p in names if p.name.lower().startswith("takeout-") and p.name.lower().endswith(".zip")]


def check_archives(archives: List[Path]) -> List[str]:
    """Plain sentences about archives that are cut off or parts that are missing."""
    problems = []
    parts: Dict[str, Set[int]] = {}
    for path in archives:
        try:
            with zipfile.ZipFile(path) as zf:
                zf.infolist()
        except (zipfile.BadZipFile, OSError, EOFError):
            problems.append(f"{path.name} is cut off or damaged. Download it again from Google's email.")
        match = TAKEOUT_PART.match(path.name)
        if match:
            parts.setdefault(match.group(1).lower(), set()).add(int(match.group(2)))
    for export, numbers in sorted(parts.items()):
        gaps = [n for n in range(1, max(numbers) + 1) if n not in numbers]
        if gaps:
            problems.append(f"Parts missing from {export}: " + ", ".join(f"{n:03d}" for n in gaps)
                            + ". Download them from Google's email into the same folder.")
    return problems


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


def immich_storage(app_dir: Path) -> Optional[Path]:
    value = read_env_file(app_dir / ".env").get("UPLOAD_LOCATION")
    if not value:
        return None
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else app_dir / path


def load_api_key(key_file: Path) -> str:
    key = os.environ.get("IMMICH_API_KEY", "").strip()
    if key:
        return key
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if key:
        return key
    raise Stop(f"No Immich API key in {key_file}. The setup script saves it there. To save one yourself, run "
               f"this in your own Terminal or PowerShell window: {python_command()} scripts/immich/import_library.py save-key",
               EXIT_USAGE)


def immich_url(value: str) -> str:
    url = value.strip().rstrip("/")
    return url[: -len("/api")] if url.endswith("/api") else url


def immich_get(url: str, path: str, key: Optional[str] = None, timeout: float = 15):
    headers = {"Accept": "application/json"}
    if key:
        headers["x-api-key"] = key
    request = urllib.request.Request(url + "/api" + path, headers=headers)
    # Immich runs on this computer or on the tailnet, so proxy settings do not apply.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else None


def check_immich(url: str, key: str) -> None:
    try:
        answer = immich_get(url, "/server/ping")
    except (urllib.error.URLError, OSError, ValueError):
        answer = None
    if not isinstance(answer, dict) or answer.get("res") != "pong":
        raise Stop(f"Immich does not answer at {url}. Start Docker Desktop, wait until Immich runs, and run "
                   "this again.")
    try:
        immich_get(url, "/users/me", key)
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise Stop("Immich does not accept the saved API key. Make a new one with the setup script, or save "
                       f"one yourself: {python_command()} scripts/immich/import_library.py save-key", EXIT_USAGE) from None
        raise Stop(f"Immich answered HTTP {err.code} when asked for your account.") from None
    except (urllib.error.URLError, OSError, ValueError) as err:
        raise Stop(f"Immich at {url} did not answer properly ({err}).") from None


def immich_asset_count(url: str, key: str) -> Optional[int]:
    """How many photos and videos your Immich account holds, archived ones included, or None when
    Immich does not say."""
    total = 0
    for query in ("", "?visibility=archive"):
        try:
            answer = immich_get(url, "/assets/statistics" + query, key)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        value = answer.get("total") if isinstance(answer, dict) else None
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        total = max(total, value)
    return total


def import_started_empty(marker: Path) -> bool:
    """True when an earlier run of this Takeout import found Immich empty. What Immich holds now then
    came from that run, and immich-go skips those files."""
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("immichAssetsBefore") == 0
    except (OSError, ValueError, AttributeError):
        return False


def machine() -> str:
    m = platform.machine().lower()
    if sys.platform == "darwin" and m == "x86_64":
        # Python running under Rosetta says x86_64 on Apple silicon; the arm64 build is native there.
        try:
            out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=5, check=False).stdout.strip()
            if out == b"1":
                return "arm64"
        except (OSError, subprocess.SubprocessError):
            pass
    if m in ("arm64", "aarch64", "armv8", "armv8l"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64"):
        return "x86_64"
    return m


def immich_go_asset(system: Optional[str] = None, cpu: Optional[str] = None) -> str:
    system = system or ("darwin" if sys.platform == "darwin" else "windows" if is_windows()
                        else "linux" if sys.platform.startswith("linux") else sys.platform)
    cpu = cpu or machine()
    names = {"darwin": "Darwin", "windows": "Windows", "linux": "Linux"}
    if system not in names or cpu not in ("arm64", "x86_64"):
        raise Stop(f"immich-go {IMMICH_GO_VERSION} has no build for this computer ({system}, {cpu}).")
    return f"immich-go_{names[system]}_{cpu}" + (".zip" if system == "windows" else ".tar.gz")


def fetch(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Homecloud"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as err:
        raise Stop(f"Could not download {url} ({err}). Check the internet connection and run this again.") from None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_checksums(text: str) -> Dict[str, str]:
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def binary_from_archive(asset: str, data: bytes, name: str) -> bytes:
    """Reads only the immich-go program out of the archive; nothing else is unpacked."""
    try:
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.is_dir() and PurePosixPath(info.filename).name == name:
                        return zf.read(info)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                for member in tf.getmembers():
                    if member.isfile() and PurePosixPath(member.name).name == name:
                        fh = tf.extractfile(member)
                        if fh is not None:
                            return fh.read()
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError):
        raise Stop(f"{asset} cannot be unpacked. Nothing was installed.") from None
    raise Stop(f"{asset} does not hold {name}. Nothing was installed.")


def immich_go_folder() -> Path:
    return homecloud_home() / "tools" / ("immich-go-" + IMMICH_GO_VERSION)


def install_immich_go(asset: Optional[str] = None) -> Path:
    """Downloads the pinned immich-go build for this computer from GitHub, checks its SHA-256
    against the release's checksums.txt and the value in this script, and keeps only the
    program in ~/.homecloud/tools. A copy that is already there is checked again."""
    asset = asset or immich_go_asset()
    folder = immich_go_folder()
    exe = folder / ("immich-go.exe" if asset.endswith(".zip") else "immich-go")
    marker = folder / "immich-go.sha256"
    if exe.is_file() and marker.is_file():
        try:
            if sha256_file(exe) == marker.read_text(encoding="utf-8").strip():
                say(f"immich-go {IMMICH_GO_VERSION} is already in {folder}, unchanged since its check.")
                return exe
        except OSError:
            pass
        say("The saved copy of immich-go changed after its check, so it is downloaded again.")
    say(f"Downloading immich-go {IMMICH_GO_VERSION} ({asset}) from GitHub...")
    published = parse_checksums(fetch(IMMICH_GO_RELEASE + IMMICH_GO_CHECKSUMS).decode("utf-8", "replace")).get(asset)
    pinned = IMMICH_GO_SHA256.get(asset)
    if not published:
        raise Stop(f"The release's {IMMICH_GO_CHECKSUMS} has no line for {asset}. Nothing was installed.")
    if pinned and published != pinned:
        raise Stop(f"The SHA-256 of {asset} in the release's {IMMICH_GO_CHECKSUMS} is not the one this kit "
                   "was made with. Nothing was installed.")
    data = fetch(IMMICH_GO_RELEASE + asset)
    if sha256_hex(data) != published:
        raise Stop(f"The download of {asset} does not match its SHA-256 in the release's {IMMICH_GO_CHECKSUMS}. "
                   "Nothing was installed. Run this again; if it repeats, tell Claude Code.")
    say(f"SHA-256 matches the release's {IMMICH_GO_CHECKSUMS}: {published}")
    program = binary_from_archive(asset, data, exe.name)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = exe.with_name(exe.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(program)
    os.chmod(tmp, 0o755)
    os.replace(tmp, exe)
    write_atomic(marker, sha256_hex(program) + "\n")
    say(f"Saved immich-go in {folder}.")
    return exe


def show_command(command: List[str]) -> str:
    if is_windows():
        return " ".join(quote_arg(c) for c in command)
    return " ".join(shlex.quote(c) for c in command)


def run_immich_go(exe: Path, url: str, key: str, archives: List[Path]) -> int:
    command = [str(exe), "upload", "from-google-photos", "--server=" + url,
               "--include-unmatched", "--pause-immich-jobs=false", "--on-errors=continue"]
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        command.append("--no-ui")
    command += [str(a) for a in archives]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("IMMICH_GO_")}
    env[IMMICH_GO_KEY_ENV] = key
    say(f"Running immich-go. The API key goes in the environment variable {IMMICH_GO_KEY_ENV}, "
        "never on the command line:")
    say("  " + show_command(command))
    # Run from its own folder, so no immich-go.toml in another folder changes its settings.
    return subprocess.run(command, env=env, cwd=str(exe.parent)).returncode


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
        raise Stop("Nothing was changed.", EXIT_USAGE)
    raise Stop("Nothing was changed. Run again with --yes after you agree to the plan above.", EXIT_USAGE)


def downloads_hint(folder: Path) -> Optional[str]:
    downloads = Path.home() / "Downloads"
    if same_path(downloads, folder):
        return None
    found = takeout_archives(downloads)
    if not found:
        return None
    size = sum(file_size(p) or 0 for p in found)
    return (f"Your Downloads folder holds {count(len(found), 'Takeout archive')} ({human_bytes(size)}). Move them "
            f"into {folder} first.")


def takeout_steps(folder: Path) -> None:
    say("Google Takeout is Google's own export. It takes longer than the browser script, but Google supports it.")
    say("Your steps on takeout.google.com:")
    say("  1. If it asks, sign in with the Google account that holds your photos.")
    say("  2. Click \"Deselect all\". Scroll down to Google Photos and tick it.")
    say("  3. Click \"Next step\" at the bottom.")
    say("  4. Choose \"Send download link via email\", \"Export once\", the file type .zip and the largest "
        "file size (50 GB), so there are fewer parts.")
    say("  5. Click \"Create export\". Google prepares it and sends you an email. That can take hours or days.")
    say("  6. In the email, download every part. Google may ask for your password again: type it there, in "
        "the browser, never in a chat. The links work for 7 days.")
    say(f"  7. Move every part into {folder}. Keep them as they are; do not unpack them.")
    say("You need free space for all parts in this folder, and about as much again where Immich keeps its "
        "files. Albums can make the parts bigger than your library, because a photo in an album comes twice.")
    say("Then Claude Code imports the parts into Immich with immich-go, a free tool that Immich's own "
        "documentation recommends for Takeout. It reads the archives as they are:")
    say("  " + kit_command("takeout", folder, " --import"))


def cmd_takeout(args) -> int:
    folder = Path(os.path.expanduser(args.to))
    if not args.do_import:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise Stop(f"Could not create {folder}: {err.strerror or err}", EXIT_USAGE) from None
        folder = folder.resolve()
        if not args.no_open:
            if open_default(TAKEOUT_URL):
                say(f"{TAKEOUT_URL} is opening in your browser.")
            else:
                say(f"Open {TAKEOUT_URL} in your browser.")
        takeout_steps(folder)
        found = takeout_archives(folder)
        if found:
            size = sum(file_size(p) or 0 for p in found)
            say(f"{folder} already holds {count(len(found), 'Takeout archive')} ({human_bytes(size)}).")
        hint = downloads_hint(folder)
        if hint:
            say(hint)
        say(f"Free space on the disk with {folder}: {human_bytes(shutil.disk_usage(str(folder)).free)}.")
        return EXIT_OK

    folder = existing_folder(args.to)
    archives = takeout_archives(folder)
    tgz = [p.name for p in folder.iterdir() if p.is_file() and p.name.lower().endswith((".tgz", ".tar.gz"))]
    if not archives:
        lines = [f"No Takeout archives (takeout-...zip) in {folder}."]
        if tgz:
            lines.append("immich-go reads .zip archives only. Create the export again with the file type .zip.")
        hint = downloads_hint(folder)
        if hint:
            lines.append(hint)
        raise Stop(" ".join(lines))
    problems = check_archives(archives)
    if problems:
        raise Stop("\n".join(problems))
    total = sum(file_size(p) or 0 for p in archives)
    url = immich_url(args.url)
    key = load_api_key(Path(os.path.expanduser(args.key_file)))
    check_immich(url, key)
    # immich-go "upgrades": when a file in the archives has the same name and time as a photo in Immich,
    # but more bytes, it uploads the file and deletes the photo in Immich for good, past the trash.
    marker = folder / ".homecloud" / TAKEOUT_MARKER
    held = immich_asset_count(url, key)
    if held is None:
        raise Stop("Immich did not say how many photos and videos it holds, so the import did not start. "
                   "Check that Immich runs, and run this again.")
    rerun = held > 0 and import_started_empty(marker)
    if held and not rerun:
        raise Stop(f"Immich already holds {held:,} photos and videos, so the import did not start. When a file in "
                   "the archives has the same name and time as a photo in Immich, but more bytes, immich-go "
                   "uploads it and deletes the photo in Immich for good, past Immich's trash. So the Takeout "
                   "import runs only into an Immich that holds no photos yet. If your photos are in both clouds, "
                   "import the Takeout archives first, before any other photos.")
    say(f"Found {count(len(archives), 'Takeout archive')} in {folder}, {human_bytes(total)}. Immich answers at {url}.")
    say("Check that these are all the parts Google's email lists: " + ", ".join(p.name for p in archives[:12])
        + (" ..." if len(archives) > 12 else ""))

    storage = immich_storage(Path(os.path.expanduser(args.app_dir)))
    if storage is None:
        say(f"Could not read UPLOAD_LOCATION from {args.app_dir}/.env, so the free space for Immich was not checked.")
    else:
        probe = storage if storage.exists() else storage.parent
        try:
            free = shutil.disk_usage(str(probe)).free
        except OSError:
            free = None
        # Immich keeps a copy of each file; thumbnails and smaller videos add 10 to 20 percent.
        need = int(total * 1.2) + GB
        if free is not None:
            say(f"Immich keeps its files in {storage}. Free there: {human_bytes(free)}. Needed: about "
                f"{human_bytes(need)} at most (less when photos sit in albums, because those come twice).")
            if free < need and not args.ignore_space:
                raise Stop(f"Not enough free space in {storage}: {human_bytes(free)} free, up to about "
                           f"{human_bytes(need)} needed. Free up space first. To try anyway, add --ignore-space.")

    have = (immich_go_folder() / ("immich-go.exe" if is_windows() else "immich-go")).is_file()
    plan = []
    if not have:
        plan.append(f"Download immich-go {IMMICH_GO_VERSION} from github.com/simulot/immich-go, check its SHA-256, "
                    f"and keep it in {immich_go_folder()}.")
    plan += [f"Import the {count(len(archives), 'archive')} in {folder} into Immich at {url} with immich-go. "
             "It creates your albums, includes photos whose .json file is missing, and leaves out what is in "
             "Google's trash.",
             ("Immich holds only what this import added in an earlier run, and immich-go skips those files."
              if rerun else "Immich holds no photos yet, so immich-go cannot replace or delete any."),
             "Your archives are only read. Nothing in Google Photos changes."]
    confirm(args, plan)
    if not rerun:
        write_atomic(marker, json.dumps({
            "started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "immichAssetsBefore": 0}) + "\n")
    exe = install_immich_go()
    code = run_immich_go(exe, url, key, archives)
    if code == 0:
        say("Result: OK. immich-go finished. Its report above lists what it found in the archives, what it uploaded "
            "and any errors. Check that it lists no errors, and that a few albums in Immich have the same number "
            "of items as in Google Photos.")
        return EXIT_OK
    say(f"Result: immich-go stopped with exit code {code}. Run the same command again: immich-go skips what "
        "Immich already has.")
    return EXIT_FAIL


# ------------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy your Google Photos library to this computer through your own photos.google.com tab, "
                    "check it, and list it for import_library.py.",
        epilog="On Windows, use py instead of python3.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="write the browser script, copy it, open Google Photos, print the steps")
    p.add_argument("--to", required=True, help="the folder for the copy (DIR); it is created if needed")
    p.add_argument("--browser", default="auto", choices=["auto"] + [k for k, _ in BROWSERS],
                   help="which browser to open (default auto: Chrome, then Edge, Brave, Arc, Vivaldi, Opera)")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.add_argument("--no-clipboard", action="store_true", help="do not copy the script to the clipboard")
    p.add_argument("--lang", help="language of the panel, such as en or de (default: this computer's language)")
    p.set_defaults(func=cmd_prepare)

    downloads_help = ("a folder where the browser saves downloads, if not the system's Downloads folder; "
                      "may be given more than once")
    p = sub.add_parser("status", help="the progress of the browser script; moves its zip files from Downloads")
    p.add_argument("--to", required=True, help="the folder for the copy (DIR)")
    p.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                   help="check every 10 seconds until the browser script ends or this many seconds pass")
    p.add_argument("--downloads", action="append", metavar="FOLDER", help=downloads_help)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("verify", help="check the copy, set file dates, write the manifest for import_library.py")
    p.add_argument("--to", "--dir", dest="to", required=True, help="the folder for the copy (DIR); --dir works too")
    p.add_argument("--downloads", action="append", metavar="FOLDER", help=downloads_help)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("takeout", help="Google Takeout: the steps, and the import of its archives with immich-go")
    p.add_argument("--to", required=True, help="the folder for the Takeout archives (DIR)")
    p.add_argument("--no-open", action="store_true", help="do not open takeout.google.com")
    p.add_argument("--import", dest="do_import", action="store_true",
                   help="import the takeout-*.zip archives in DIR into Immich with immich-go")
    p.add_argument("--url", default=DEFAULT_URL, help=f"Immich address (default {DEFAULT_URL})")
    p.add_argument("--key-file", default=str(DEFAULT_KEY_FILE),
                   help="file with the Immich API key (default ~/.homecloud/immich-api-key); "
                        "the IMMICH_API_KEY environment variable wins if set")
    p.add_argument("--app-dir", default=str(DEFAULT_APP_DIR),
                   help="folder with Immich's .env, for the free space check (default ~/immich-app)")
    p.add_argument("--ignore-space", action="store_true", help="import even if the free space check fails")
    p.add_argument("--yes", action="store_true", help="do not ask; you already agreed to the plan")
    p.set_defaults(func=cmd_takeout)
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
    except KeyboardInterrupt:
        say("Stopped. Run the same command again to continue; finished work is kept.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
