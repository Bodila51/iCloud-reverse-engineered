#!/usr/bin/env python3
"""Homecloud: copy your whole iCloud Photos library to this computer.

This script signs in to iCloud on the web, the way a browser does, and only
reads. It never deletes, edits, moves or uploads anything in iCloud. Every
request is checked against a short list of read-only addresses before it
leaves this computer, and anything else is refused.

Commands, run from the kit folder:

    python3 scripts/icloud.py login                  sign in, in your own Terminal window
    python3 scripts/icloud.py scan [--json FILE]     count your library (reads only)
    python3 scripts/icloud.py download --to DIR      copy everything into DIR
    python3 scripts/icloud.py verify --to DIR        check every file in DIR
    python3 scripts/icloud.py logout                 sign out and delete the session key

Files land in DIR/YYYY/MM/<original file name>, by the date the photo was
taken. A Live Photo's video sits next to its photo (IMG_0001.HEIC and
IMG_0001.MOV). Edited versions are separate files (IMG_0001_edited.JPG).
DIR/.homecloud/manifest.jsonl lists every file.

The sign-in session is kept in ~/.homecloud/session, readable only by you,
until you run logout. Your Apple Account password is never saved, shown or
logged. logout is the one request that changes anything at Apple: it ends
this computer's sign-in session. It never touches your photos.

Needs Python 3.10 or newer and the packages in scripts/requirements.txt,
installed into a virtual environment. verify and --help work without them.

Exit codes: 0 done. 1 something failed, the message says what. 2 sign in
again with login. 3 iCloud does not allow web access, the message says how
to allow it. 4 paused, run the same command again. 130 stopped with Ctrl+C.

Homecloud is not made by Apple and is not endorsed by Apple.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import errno
import getpass
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import parse_qs, urlsplit

VERSION = "1.0.0"
PYICLOUD_PIN = "2026.10.1"
SOURCE = "icloud-web"
STATE_DIR = ".homecloud"
MANIFEST_NAME = "manifest.jsonl"
LAST_RUN_NAME = "last-download.json"
REPORT_NAME = "verify-report.txt"
LOCK_NAME = "lock"
PARTIAL_DIR = "partial"

DEFAULT_WORKERS = 4
MAX_WORKERS = 16
PAGE_SIZE = 200          # CloudKit records per page: about 100 items plus their master records
LOOKUP_BATCH = 100       # records per lookup when download links need renewing
URL_REFRESH_MARGIN = 10 * 60
URL_ASSUMED_LIFETIME = 50 * 60
CHUNK = 1024 * 1024
RETRIES = 5
RETRY_BASE = 2.0
APPROVAL_WAIT_MINUTES = 10
SAVE_EVERY = 60.0

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOGIN = 2
EXIT_BLOCKED = 3
EXIT_PAUSED = 4
EXIT_STOPPED = 130

PRIMARY_ZONE = {"zoneName": "PrimarySync", "zoneType": "REGULAR_CUSTOM_ZONE"}
SHARED_ZONE_PREFIX = "SharedSync-"
LIST_ALL = "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted"
LIST_HIDDEN = "CPLAssetAndMasterHiddenByAssetDate"
COUNT_ALL = "CPLAssetByAssetDateWithoutHiddenOrDeleted"
COUNT_HIDDEN = "CPLAssetHiddenByAssetDate"
COUNT_DELETED = "CPLAssetDeletedByExpungedDate"

# The only CloudKit calls this script makes. All of them read.
CK_READ_PATHS = (
    "records/query",
    "records/lookup",
    "zones/list",
    "internal/records/query/batch",
)

DESIRED_KEYS = [
    "recordName", "recordType", "recordChangeTag", "masterRef",
    "filenameEnc", "itemType", "dataClassType",
    "resOriginalRes", "resOriginalFileType",
    "resOriginalAltRes", "resOriginalAltFileType",
    "resOriginalVidComplRes", "resOriginalVidComplFileType",
    "resJPEGFullRes", "resJPEGFullFileType",
    "resVidFullRes", "resVidFullFileType",
    "resVidComplRes", "resVidComplFileType",
    "assetDate", "addedDate", "timeZoneOffset",
    "isFavorite", "isHidden", "isDeleted", "adjustmentType",
    "assetSubtype", "assetSubtypeV2",
]

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Safari/605.1.15"
)

UTI_EXT = {
    "public.heic": ".HEIC", "public.heif": ".HEIF", "public.jpeg": ".JPG",
    "public.png": ".PNG", "com.compuserve.gif": ".GIF", "public.tiff": ".TIF",
    "org.webmproject.webp": ".WEBP", "com.microsoft.bmp": ".BMP", "public.avif": ".AVIF",
    "com.adobe.raw-image": ".DNG", "com.canon.cr2-raw-image": ".CR2",
    "com.canon.cr3-raw-image": ".CR3", "com.canon.crw-raw-image": ".CRW",
    "com.nikon.raw-image": ".NEF", "com.nikon.nrw-raw-image": ".NRW",
    "com.sony.arw-raw-image": ".ARW", "com.fuji.raw-image": ".RAF",
    "com.olympus.raw-image": ".ORF", "com.olympus.or-raw-image": ".ORF",
    "com.panasonic.rw2-raw-image": ".RW2", "com.pentax.raw-image": ".PEF",
    "com.apple.quicktime-movie": ".MOV", "public.mpeg-4": ".MP4",
    "com.apple.m4v-video": ".M4V", "public.avi": ".AVI", "public.3gpp": ".3GP",
    "public.mpeg": ".MPG",
}
VIDEO_UTIS = {
    "com.apple.quicktime-movie", "public.mpeg-4", "com.apple.m4v-video",
    "public.avi", "public.3gpp", "public.3gpp2", "public.mpeg", "public.movie",
    "public.video",
}

# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

ADP_MESSAGE = """iCloud does not allow access to your photos from the web right now.
This is usually because Advanced Data Protection is on and web access is off.

To allow it, on your iPhone:
  iOS 26.4 or later:   Settings > [your name] > iCloud > iCloud.com, turn on Allow Data Access.
  Earlier iOS:         Settings > [your name] > iCloud, turn on Access iCloud Data on the Web.

Then run the same command again. You can turn it off again when the download is done."""

LOGIN_NEEDED = """Your iCloud sign-in has expired or is missing.
Run this in your own Terminal window, then run the same command again:
    python3 scripts/icloud.py login"""

NOT_SIGNED_IN = """This computer is not signed in to iCloud yet.
Run this in your own Terminal window first:
    python3 scripts/icloud.py login"""

LOCKED_MESSAGE = """Apple has locked this Apple Account for security reasons.
Unlock it at https://iforgot.apple.com, then run login again."""

WRONG_PASSWORD = """Apple did not accept that Apple Account and password.
Check both and run login again. After several wrong tries Apple locks the
account for a while."""

NO_CONNECTION = "Could not reach iCloud. Check the internet connection and try again."

TERMS_MESSAGE = """Apple needs you to accept new iCloud terms first.
Sign in at https://www.icloud.com in a web browser, accept them, then run login again."""

NOT_ACTIVATED = """iCloud Photos is not ready for web access on this account.
Sign in once at https://www.icloud.com/photos in a web browser, check that your
photos show up there, then run the same command again."""

INDEXING_MESSAGE = """iCloud is still preparing your library for the web.
This can take a while after you turn on iCloud Photos. Try again in an hour."""

APPROVAL_WAIT = """iCloud asks for your approval before it shares your photos with this computer.
This happens when Advanced Data Protection is on. Each approval lasts about an hour.
Look at your iPhone, iPad or Mac for a request to allow access to your iCloud data,
and tap Allow. Waiting up to {minutes} minutes."""

APPROVAL_PAUSED = """Paused: iCloud needs your approval again.
With Advanced Data Protection on, each approval lasts about an hour.
Nothing is lost. Files that finished downloading are saved and listed in the manifest.

To go on:
  1. Approve the request on your iPhone, or run: python3 scripts/icloud.py login
  2. Run the same command again. A download goes on where it stopped."""

THROTTLE_PAUSED = """Paused: iCloud keeps refusing requests from this computer.
It may be limiting how fast this computer reads, or the sign-in needs renewing.
Nothing is lost. Files that finished downloading are saved and listed in the manifest.

Wait an hour, then run the same command again. A download goes on where it stopped.
If it stops again right away, run: python3 scripts/icloud.py login"""

SESSION_NOTE = """The session key stays on this computer, in {folder},
until you run:  python3 scripts/icloud.py logout
While it is there, it opens your iCloud account the same way signing in to
icloud.com in a browser does: photos, mail, contacts, iCloud Drive and the rest.
Only your user account on this computer can read it."""

SIGN_OUT_QUESTION = "Everything is here. Sign out of iCloud and delete the session key now? [Y/n] "

NOT_SIGNED_OUT = "NOT SIGNED OUT: the session key is still on this computer."

NEEDS_PACKAGES = """This command needs Python 3.10 or newer with the packages in
scripts/requirements.txt. This is Python {version}{missing}.
If you have no Python 3.10 or newer, install one from python.org first.
Then, once, from the kit folder:
    {make}
    {python} -m pip install -r scripts/requirements.txt
and run this script with {python} instead of {plain}.{note}"""

AMPERSAND_NOTE = "In PowerShell, put & in front of a command that starts with a quoted path."


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class HomecloudError(Exception):
    """A problem with a message meant for the user."""

    exit_code = EXIT_FAIL


class SessionExpired(HomecloudError):
    exit_code = EXIT_LOGIN


class AccessBlocked(HomecloudError):
    exit_code = EXIT_BLOCKED


class ApprovalNeeded(HomecloudError):
    exit_code = EXIT_PAUSED


class DiskFull(HomecloudError):
    pass


class ReadOnlyViolation(RuntimeError):
    """Raised before any request or call that could change something in iCloud."""


class CloudError(Exception):
    """A failed CloudKit call.

    kind is one of: transient (try again), auth (renew the session),
    access (iCloud refused: approval or throttling), adp_off (web access is
    turned off), fatal.
    """

    def __init__(self, kind, message, status=None, code=None, retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after


class FetchError(Exception):
    """A failed file download. kind: retry, throttle, expired, auth, stopped, disk."""

    def __init__(self, kind, message="", retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

class Out(object):
    """Prints messages. On a terminal, progress updates one line in place."""

    def __init__(self, stream=None):
        self._stream = stream
        self._lock = threading.RLock()
        self._status_width = 0

    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stdout

    def is_tty(self):
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def line(self, text=""):
        with self._lock:
            self._clear_status()
            self._write(text + "\n")

    def status(self, text):
        with self._lock:
            if self.is_tty():
                pad = " " * max(0, self._status_width - len(text))
                self._write("\r" + text + pad)
                self._status_width = len(text)
            else:
                self._write(text + "\n")

    def end_status(self):
        with self._lock:
            if self._status_width:
                self._write("\n")
                self._status_width = 0

    def _clear_status(self):
        if self._status_width:
            self._write("\r" + " " * self._status_width + "\r")
            self._status_width = 0

    def _write(self, text):
        stream = self.stream
        try:
            stream.write(text)
        except UnicodeEncodeError:
            stream.write(text.encode("ascii", "replace").decode("ascii"))
        try:
            stream.flush()
        except Exception:
            pass


OUT = Out()


class Ticker(object):
    """Says when it is time to print the next progress line."""

    def __init__(self, out, tty_every=0.5, log_every=20.0):
        self.every = tty_every if out.is_tty() else log_every
        self.last = None

    def due(self, force=False):
        now = time.monotonic()
        if force or self.last is None or now - self.last >= self.every:
            self.last = now
            return True
        return False


def fmt_int(value):
    return "{:,}".format(int(value))


def fmt_size(num):
    num = float(num or 0)
    if num >= 1e12:
        return "%.2f TB" % (num / 1e12)
    if num >= 1e9:
        return "%.1f GB" % (num / 1e9)
    if num >= 1e6:
        return "%.1f MB" % (num / 1e6)
    if num >= 1e3:
        return "%.0f KB" % (num / 1e3)
    return "%d bytes" % num


def fmt_pair(done, total):
    if total >= 1e9:
        unit, div = "GB", 1e9
    elif total >= 1e6:
        unit, div = "MB", 1e6
    else:
        unit, div = "KB", 1e3
    return "%.1f / %.1f %s" % (done / div, total / div, unit)


def fmt_eta(seconds):
    if seconds is None:
        return "working out the time left"
    if seconds < 60:
        return "less than a minute left"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return "about %d min left" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours >= 48:
        return "about %d days left" % int(round(hours / 24.0))
    if minutes:
        return "about %d h %d min left" % (hours, minutes)
    return "about %d h left" % hours


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%d s" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%d min" % minutes
    return "%d h %d min" % divmod(minutes, 60)


def short_error(exc, limit=200):
    text = str(getattr(exc, "reason", "") or "") or str(exc) or type(exc).__name__
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def backoff(attempt, base=RETRY_BASE, cap=120.0):
    """Exponential backoff with jitter: about 2, 4, 8, 16... seconds."""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay * (0.5 + random.random())


def parse_retry_after(headers):
    if not headers:
        return None
    try:
        value = headers.get("Retry-After")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return max(0.0, min(600.0, float(value)))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Read-only guard
# --------------------------------------------------------------------------

_SETUP_BLOCKED_WORDS = ("logout", "repairdone", "delete", "remove", "reset", "modify", "upload")
_AUTH_HOSTS = ("idmsa.apple.com", "idmsa.apple.com.cn")
_CONTENT_SUFFIXES = (".icloud-content.com", ".icloud-content.com.cn", ".cdn-apple.com")


_SIGN_OUT_ALLOWED = threading.Event()


@contextlib.contextmanager
def allow_sign_out():
    """Lets the one sign-out request through, for the logout command only."""
    _SIGN_OUT_ALLOWED.set()
    try:
        yield
    finally:
        _SIGN_OUT_ALLOWED.clear()


def _host_is(host, name):
    return host == name or host.endswith("." + name) or host.endswith("-" + name)


def check_request(method, url):
    """Refuse any request that is not sign-in, a CloudKit read or a file download.

    Raises ReadOnlyViolation before the request leaves this computer.
    """
    if isinstance(url, bytes):
        url = url.decode("utf-8", "replace")
    method = (method or "GET").upper()
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    if parts.scheme != "https":
        raise ReadOnlyViolation("Blocked a request that is not HTTPS: %s %s" % (method, host))
    if host in _AUTH_HOSTS:
        return
    if _host_is(host, "setup.icloud.com") or _host_is(host, "setup.icloud.com.cn"):
        if method == "POST" and path.endswith("/logout") and _SIGN_OUT_ALLOWED.is_set():
            return
        if any(word in path for word in _SETUP_BLOCKED_WORDS):
            raise ReadOnlyViolation("Blocked an iCloud account change: %s %s" % (method, path))
        return
    if _host_is(host, "ckdatabasews.icloud.com") or _host_is(host, "ckdatabasews.icloud.com.cn"):
        if method == "POST" and any(path.endswith("/" + p) for p in CK_READ_PATHS):
            return
        raise ReadOnlyViolation(
            "Blocked a CloudKit request that is not a read: %s %s" % (method, path))
    if any(host.endswith(sfx) for sfx in _CONTENT_SUFFIXES):
        if method in ("GET", "HEAD"):
            return
        raise ReadOnlyViolation("Blocked a %s to the iCloud file store" % method)
    raise ReadOnlyViolation(
        "Blocked a request to %s: this script only talks to Apple sign-in, "
        "iCloud Photos reads and iCloud file downloads." % (host or "an unknown host"))


def install_request_guard():
    """Check every request made through the requests library, pyicloud included."""
    import requests

    current = requests.Session.request
    if getattr(current, "_homecloud_guard", False):
        return

    def guarded_request(self, method, url, *args, **kwargs):
        check_request(method, url)
        return current(self, method, url, *args, **kwargs)

    guarded_request._homecloud_guard = True
    requests.Session.request = guarded_request


WRITE_METHODS = frozenset([
    "delete", "set_favorite", "favorite", "unfavorite", "rename", "add_photo",
    "remove_photo", "upload", "upload_file", "upload_asset", "create_album",
    "modify", "logout", "hide", "unhide", "set_hidden", "move",
])
_WRITE_MODULES = (
    "pyicloud.base",
    "pyicloud.services.photos_cloudkit.service",
    "pyicloud.services.photos_cloudkit.client",
    "pyicloud.services.photos_cloudkit.upload",
    "pyicloud.services.photos_cloudkit.sync",
    "pyicloud.services.photos_legacy",
    "pyicloud.common.cloudkit.client",
)


def _make_blocker(owner, name):
    def blocked(*args, **kwargs):
        raise ReadOnlyViolation(
            "Homecloud never calls %s.%s: it would change iCloud." % (owner, name))
    blocked._homecloud_blocked = True
    return blocked


def install_write_blockers():
    """Replace every pyicloud method that could change iCloud with one that refuses."""
    import importlib
    import inspect

    blocked = []
    for modname in _WRITE_MODULES:
        try:
            module = importlib.import_module(modname)
        except Exception:
            continue
        for cls in list(vars(module).values()):
            if not inspect.isclass(cls) or not str(cls.__module__).startswith("pyicloud"):
                continue
            for name in WRITE_METHODS:
                attr = cls.__dict__.get(name)
                if attr is None or getattr(attr, "_homecloud_blocked", False):
                    continue
                if inspect.isfunction(attr) or isinstance(attr, (staticmethod, classmethod)):
                    setattr(cls, name, _make_blocker(cls.__name__, name))
                    blocked.append("%s.%s" % (cls.__name__, name))
    return blocked


def quiet_library_logs():
    logging.getLogger("pyicloud").setLevel(logging.CRITICAL)
    for name in ("urllib3", "requests", "fido2"):
        logging.getLogger(name).setLevel(logging.ERROR)


# --------------------------------------------------------------------------
# Session folder
# --------------------------------------------------------------------------

def homecloud_home():
    return os.path.abspath(os.path.expanduser(os.environ.get("HOMECLOUD_HOME") or "~/.homecloud"))


def session_dir():
    return os.path.join(homecloud_home(), "session")


def _account_file():
    return os.path.join(session_dir(), "account.json")


def secure_session_dir():
    """Folder 0700, files 0600. Owner only."""
    for folder in (homecloud_home(), session_dir()):
        os.makedirs(folder, exist_ok=True)
        if os.name != "nt":
            try:
                os.chmod(folder, 0o700)
            except OSError:
                pass
    if os.name == "nt":
        return
    folder = session_dir()
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


def load_account():
    try:
        with open(_account_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("apple_id"), str) and data["apple_id"]:
            return data
    except (OSError, ValueError):
        pass
    return None


def save_account(apple_id, china=False):
    secure_session_dir()
    path = _account_file()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"apple_id": apple_id, "china_mainland": bool(china)}, fh)


def session_candidates():
    """Apple IDs (or their file-name form) with a saved session, newest first.

    pyicloud names its files after the Apple ID with every character that is
    not a letter, digit or _ removed. That form opens the same files.
    """
    names = []
    account = load_account()
    if account:
        names.append(account["apple_id"])
    try:
        entries = os.listdir(session_dir())
    except OSError:
        entries = []
    stems = []
    for name in entries:
        if name.endswith(".session"):
            path = os.path.join(session_dir(), name)
            try:
                stems.append((os.path.getmtime(path), name[: -len(".session")]))
            except OSError:
                pass
    known = set(re.sub(r"[^\w]", "", n) for n in names)
    for _, stem in sorted(stems, reverse=True):
        if stem and stem not in known:
            names.append(stem)
            known.add(stem)
    return names


def mask_id(apple_id):
    apple_id = str(apple_id or "")
    if "@" in apple_id:
        user, _, domain = apple_id.partition("@")
        return (user[:2] + "***@" + domain) if len(user) > 2 else ("***@" + domain)
    if len(apple_id) > 4:
        return "***" + apple_id[-4:]
    return "***"


def _service_kwargs(china=False):
    kwargs = dict(cookie_directory=session_dir(), with_family=False, refresh_interval=None)
    if china:
        kwargs["china_mainland"] = True
    return kwargs


def _status_ok(status):
    return bool(status.get("authenticated") and status.get("trusted_session")
                and not status.get("requires_2fa") and not status.get("requires_2sa"))


def signed_in_name(api, fallback=""):
    try:
        info = api.data.get("dsInfo") or {}
        return info.get("appleId") or info.get("primaryEmail") or fallback
    except Exception:
        return fallback


def open_session(service_factory=None, renew=True):
    """Open the saved session without a password. Raises SessionExpired if it is gone."""
    from pyicloud import exceptions as E
    if service_factory is None:
        from pyicloud import PyiCloudService as service_factory
    candidates = session_candidates()
    if not candidates:
        raise SessionExpired(NOT_SIGNED_IN)
    account = load_account() or {}
    for apple_id in candidates:
        china = bool(account.get("china_mainland")) and apple_id == account.get("apple_id")
        try:
            api = service_factory(apple_id, None, authenticate=False, **_service_kwargs(china))
            if _status_ok(api.get_auth_status()):
                return api
            if not renew:
                continue
            api.authenticate()
            if not (api.requires_2fa or api.requires_2sa) and api.is_trusted_session:
                return api
        except E.PyiCloudAccountLockedException:
            raise HomecloudError(LOCKED_MESSAGE)
        except E.PyiCloudConnectionException:
            raise HomecloudError(NO_CONNECTION)
        except E.PyiCloudException:
            continue
    raise SessionExpired(LOGIN_NEEDED)


def web_access_state(api):
    """Apple's web access switches, or None if they cannot be read."""
    probe = getattr(api, "_check_pcs_consent", None)
    if probe is None:
        return None
    try:
        state = probe()
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _error_chain(exc):
    chain = []
    while exc is not None and exc not in chain and len(chain) < 10:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


def classify_pyicloud_error(exc):
    """Sort a pyicloud error into: adp_off, indexing, connection, auth, pcs,
    access, not_active, transient, fatal."""
    from pyicloud import exceptions as E
    chain = _error_chain(exc)
    text = " ".join("%s %s" % (getattr(x, "reason", "") or "", x) for x in chain).lower()
    codes = set(str(getattr(x, "code", "") or "").upper() for x in chain)
    statuses = set(getattr(getattr(x, "response", None), "status_code", None) for x in chain)
    if "private db access disabled" in text:
        return "adp_off"
    if "indexing" in text:
        return "indexing"
    if any(isinstance(x, E.PyiCloudConnectionException) for x in chain):
        return "connection"
    if any(isinstance(x, (E.PyiCloud2FARequiredException, E.PyiCloud2SARequiredException,
                          E.PyiCloudAuthRequiredException, E.PyiCloudFailedLoginException))
           for x in chain):
        return "auth"
    if any(isinstance(x, E.PyiCloudPCSTimeoutException) for x in chain) or "pcs" in text:
        return "pcs"
    if "ACCESS_DENIED" in codes or "access_denied" in text:
        return "access"
    if codes & {"401", "421", "450", "AUTHENTICATION_FAILED", "AUTHENTICATION_REQUIRED"} \
            or statuses & {401, 421, 450} or "authentication required" in text:
        return "auth"
    if "403" in codes or 403 in statuses:
        return "auth"
    if codes & {"RETRY_LATER", "TRY_AGAIN_LATER", "THROTTLED", "CAS_OP_LOCK", "429", "500",
                "502", "503", "504", "SERVER_ERROR", "INTERNAL_ERROR", "SERVICE_UNAVAILABLE"} \
            or statuses & {429, 500, 502, 503, 504}:
        return "transient"
    if "ZONE_NOT_FOUND" in codes or "finish setting up" in text:
        return "not_active"
    return "fatal"


def pyicloud_to_cloud_error(exc):
    kind = classify_pyicloud_error(exc)
    mapping = {"connection": "transient", "pcs": "access", "indexing": "fatal",
               "not_active": "fatal"}
    kind = mapping.get(kind, kind)
    response = getattr(exc, "response", None)
    return CloudError(kind, short_error(exc),
                      status=getattr(response, "status_code", None),
                      code=getattr(exc, "code", None),
                      retry_after=parse_retry_after(getattr(response, "headers", None)))


def _wait_for_consent(api, deadline):
    while time.monotonic() < deadline:
        time.sleep(10)
        state = web_access_state(api)
        if state is None:
            return
        if state.get("isWebAccessAllowed") is False:
            raise AccessBlocked(ADP_MESSAGE)
        if state.get("isDeviceConsentedForPCS", True):
            return


def acquire_photos(api, out, wait_seconds):
    """Open iCloud Photos. With Advanced Data Protection on, this waits for the
    user to tap Allow on a trusted device (up to wait_seconds)."""
    from pyicloud import exceptions as E
    deadline = time.monotonic() + max(0, wait_seconds)
    told = False
    tries = 0
    while True:
        try:
            return api.photos
        except E.PyiCloudException as exc:
            kind = classify_pyicloud_error(exc)
            if kind == "adp_off":
                raise AccessBlocked(ADP_MESSAGE)
            if kind == "indexing":
                raise HomecloudError(INDEXING_MESSAGE)
            if kind == "not_active":
                raise HomecloudError(NOT_ACTIVATED)
            if kind == "auth":
                raise SessionExpired(LOGIN_NEEDED)
            if kind in ("connection", "transient"):
                tries += 1
                if tries > 4:
                    raise HomecloudError(NO_CONNECTION)
                time.sleep(5 * tries)
                continue
            if kind in ("pcs", "access"):
                if time.monotonic() >= deadline:
                    raise ApprovalNeeded(APPROVAL_PAUSED)
                if not told:
                    out.line(APPROVAL_WAIT.format(minutes=max(1, int(round(wait_seconds / 60.0)))))
                    told = True
                _wait_for_consent(api, deadline)
                continue
            raise HomecloudError("iCloud Photos could not be opened: %s" % short_error(exc))


def storage_summary(api):
    """iCloud storage use: (used, total, photos_bytes, photos_label) or None."""
    try:
        storage = api.account.storage
        used = storage.usage.used_storage_in_bytes
        total = storage.usage.total_storage_in_bytes
        photos_bytes, label = None, "Photos and Videos"
        for key, media in (storage.usages_by_media or {}).items():
            if str(key).lower() == "photos":
                photos_bytes = media.usage_in_bytes
                label = media.label or label
        return {"used_bytes": used, "total_bytes": total,
                "photos_bytes": photos_bytes, "photos_label": label}
    except Exception:
        return None


# --------------------------------------------------------------------------
# CloudKit access
# --------------------------------------------------------------------------

class PyiCloudTransport(object):
    """Sends the read-only CloudKit calls through pyicloud's signed-in session."""

    def __init__(self, api, photos):
        self.api = api
        self.update(photos)

    def update(self, photos):
        self.photos = photos
        private = str(photos.service_endpoint).rstrip("/")
        base = private[: -len("/private")] if private.endswith("/private") else private.rsplit("/", 1)[0]
        self.endpoints = {"private": base + "/private", "shared": base + "/shared"}
        self.params = dict(photos.params)

    def post_json(self, db, path, payload):
        from pyicloud.exceptions import PyiCloudException
        if path not in CK_READ_PATHS:
            raise ReadOnlyViolation("Blocked CloudKit call %s" % path)
        url = "%s/%s" % (self.endpoints[db], path)
        try:
            response = self.api.session.post(url, params=self.params, json=payload, timeout=(20, 120))
        except ReadOnlyViolation:
            raise
        except PyiCloudException as exc:
            raise pyicloud_to_cloud_error(exc)
        try:
            data = response.json()
        except ValueError:
            raise CloudError("transient", "iCloud sent an answer this script could not read")
        if not isinstance(data, dict):
            raise CloudError("fatal", "iCloud sent an unexpected answer")
        return data


def _raise_for_record_access(data):
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return
    for rec in records:
        if not isinstance(rec, dict):
            continue
        code = str(rec.get("serverErrorCode") or "").upper()
        reason = str(rec.get("reason") or "")
        if code == "ACCESS_DENIED":
            kind = "adp_off" if "private db access disabled" in reason.lower() else "access"
            raise CloudError(kind, reason or code, code=code)
        if code in ("AUTHENTICATION_FAILED", "AUTHENTICATION_REQUIRED"):
            raise CloudError("auth", reason or code, code=code)


class CloudClient(object):
    """Retries CloudKit reads, renews the session and waits for approvals."""

    def __init__(self, transport, recover_auth=None, recover_access=None, out=None,
                 sleep=time.sleep, retry_base=RETRY_BASE, max_retries=RETRIES):
        self.transport = transport
        self.recover_auth = recover_auth
        self.recover_access = recover_access
        self.out = out or OUT
        self.sleep = sleep
        self.retry_base = retry_base
        self.max_retries = max_retries

    def post_json(self, db, path, payload, recover=True):
        attempt = 0
        renewed = False
        access_tries = 0
        while True:
            try:
                data = self.transport.post_json(db, path, payload)
                if recover:
                    _raise_for_record_access(data)
                return data
            except CloudError as exc:
                if exc.kind == "transient":
                    attempt += 1
                    if attempt > self.max_retries:
                        raise
                    delay = exc.retry_after if exc.retry_after is not None else backoff(attempt, self.retry_base)
                    if delay >= 10:
                        self.out.line("iCloud is busy. Trying again in %d seconds." % int(delay))
                    self.sleep(min(delay, 600))
                    continue
                if not recover:
                    raise
                if exc.kind == "adp_off":
                    raise AccessBlocked(ADP_MESSAGE)
                if exc.kind == "auth" and not renewed:
                    renewed = True
                    if self.recover_auth is not None and self.recover_auth():
                        continue
                    raise SessionExpired(LOGIN_NEEDED)
                if exc.kind in ("auth", "access"):
                    if access_tries < 2 and self.recover_access is not None:
                        access_tries += 1
                        if self.recover_access(exc):
                            continue
                    raise ApprovalNeeded(exc.message)
                raise


class CloudBundle(object):
    """What scan and download need: a CloudKit client, a file fetcher, albums."""

    def __init__(self, client, fetcher=None, albums=None, adp=False, storage=None):
        self.client = client
        self.fetcher = fetcher
        self._albums = albums
        self.adp = adp
        self._storage = storage

    def albums(self, membership, out):
        if self._albums is None:
            return [], {}, []
        return self._albums(membership, out)

    def storage(self):
        return self._storage() if callable(self._storage) else self._storage


class RealCloud(CloudBundle):
    """The live connection: pyicloud for sign-in, own CloudKit reads for listing."""

    def __init__(self, out, approval_wait_minutes=APPROVAL_WAIT_MINUTES):
        install_request_guard()
        install_write_blockers()
        quiet_library_logs()
        secure_session_dir()
        self.out = out
        self.wait = max(1, int(approval_wait_minutes)) * 60
        self.api = open_session()
        self.name = signed_in_name(self.api)
        state = web_access_state(self.api) or {}
        if state.get("isWebAccessAllowed") is False:
            raise AccessBlocked(ADP_MESSAGE)
        self.access_waits = 0
        self.photos = acquire_photos(self.api, out, self.wait)
        self.transport = PyiCloudTransport(self.api, self.photos)
        client = CloudClient(self.transport, self.recover_auth, self.recover_access, out=out)
        CloudBundle.__init__(self, client, fetcher=None, albums=self._real_albums,
                             adp=bool(state.get("isICDRSDisabled")),
                             storage=lambda: storage_summary(self.api))

    def make_fetcher(self):
        if self.fetcher is None:
            ua = None
            try:
                ua = self.api.session.headers.get("User-Agent")
            except Exception:
                pass
            self.fetcher = HttpFetcher(user_agent=ua or BROWSER_UA)
        return self.fetcher

    def recover_auth(self):
        from pyicloud import exceptions as E
        self.out.line("Renewing the iCloud session.")
        try:
            self.api.authenticate(force_refresh=True)
        except E.PyiCloudException:
            return False
        if self.api.requires_2fa or self.api.requires_2sa:
            return False
        try:
            self.photos = acquire_photos(self.api, self.out, self.wait)
        except AccessBlocked:
            raise
        except HomecloudError:
            return False
        self.transport.update(self.photos)
        secure_session_dir()
        return True

    def recover_access(self, exc):
        state = web_access_state(self.api)
        if state is not None and state.get("isWebAccessAllowed") is False:
            raise AccessBlocked(ADP_MESSAGE)
        if state is not None:
            self.adp = bool(state.get("isICDRSDisabled"))
        if self.adp:
            try:
                self.photos = acquire_photos(self.api, self.out, self.wait)
            except ApprovalNeeded:
                return False
            self.transport.update(self.photos)
            return True
        self.access_waits += 1
        if self.access_waits > 3:
            return False
        delay = 60 * self.access_waits
        self.out.line("iCloud refused a request. It may be limiting how fast this computer "
                      "reads. Waiting %d min, then trying again." % (delay // 60))
        time.sleep(delay)
        return True

    def _real_albums(self, membership, out):
        """User albums (not smart albums or folders), and which items are in each."""
        names, members, warnings = [], {}, []
        try:
            from pyicloud.services.photos import PhotoAlbum, PhotoAlbumFolder, SmartPhotoAlbum
            albums = list(self.photos.albums)
        except Exception as exc:
            return [], {}, ["Album names could not be read (%s). The files are not affected." % short_error(exc)]
        user_albums = [a for a in albums if isinstance(a, PhotoAlbum)
                       and not isinstance(a, (SmartPhotoAlbum, PhotoAlbumFolder))]
        ticker = Ticker(out)
        for index, album in enumerate(user_albums, 1):
            name = getattr(album, "fullname", None) or getattr(album, "title", None) or "Album"
            name = str(name)
            names.append(name)
            if not membership:
                continue
            if ticker.due():
                out.status("Reading albums: %d of %d" % (index, len(user_albums)))
            try:
                for photo in album.photos:
                    members.setdefault(photo.id, []).append(name)
            except Exception as exc:
                warnings.append("Could not read the photos in album %r (%s)." % (name, short_error(exc)))
        if membership and user_albums:
            out.status("Read %d albums." % len(user_albums))
            out.end_status()
        return names, members, warnings


# --------------------------------------------------------------------------
# Library listing
# --------------------------------------------------------------------------

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


class Source(object):
    """One iCloud Photos zone: the personal library or a Shared Library."""

    def __init__(self, library, db, zone):
        self.library = library
        self.db = db
        self.zone = zone
        self.counts = {}
        self.listed = {}
        self.errors = []

    @property
    def label(self):
        return "your personal library" if self.library == "personal" else "the Shared Library"


class Res(object):
    __slots__ = ("variant", "kind", "field", "holder", "url", "size", "checksum", "uti",
                 "url_time")

    def __init__(self, variant, kind, field, holder, info, now):
        self.variant = variant
        self.kind = kind
        self.field = field
        self.holder = holder
        self.url = info.get("url")
        self.size = int(info.get("size") or 0)
        self.checksum = info.get("checksum")
        self.uti = info.get("uti") or ""
        self.url_time = now


class Asset(object):
    __slots__ = ("asset_id", "master_id", "filename", "created", "is_video", "favorite",
                 "hidden", "library", "db", "zone", "resources", "albums")

    @property
    def folder(self):
        if self.created is None:
            return "1970/01"
        return "%04d/%02d" % (self.created.year, self.created.month)

    @property
    def timestamp(self):
        if self.created is None:
            return None
        return (self.created - EPOCH).total_seconds()

    @property
    def is_live(self):
        return any(r.variant == "live" for r in self.resources)

    @property
    def is_edited(self):
        return any(r.variant in ("edited", "edited_live") for r in self.resources)

    @property
    def original_bytes(self):
        return sum(r.size for r in self.resources if r.variant in ("original", "alt"))

    @property
    def total_bytes(self):
        return sum(r.size for r in self.resources)


def _field(fields, name):
    item = fields.get(name) if isinstance(fields, dict) else None
    return item.get("value") if isinstance(item, dict) else None


def _resource(fields, prefix):
    value = _field(fields, prefix + "Res")
    if not isinstance(value, dict):
        return None
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    url = value.get("downloadURL")
    return {
        "size": size,
        "url": url if isinstance(url, str) and url else None,
        "checksum": value.get("fileChecksum"),
        "uti": str(_field(fields, prefix + "FileType") or ""),
    }


def decode_text(item):
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if not isinstance(value, str) or not value:
        return None
    if item.get("type") == "STRING":
        return value
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None if item.get("type") == "ENCRYPTED_BYTES" else value


def is_video_uti(uti):
    uti = (uti or "").lower()
    return uti in VIDEO_UTIS or "movie" in uti or "video" in uti


def uti_ext(uti, default=""):
    return UTI_EXT.get((uti or "").lower(), default)


def capture_time(ms, offset):
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        utc = EPOCH + dt.timedelta(milliseconds=float(ms))
    except (OverflowError, ValueError):
        return None
    if isinstance(offset, (int, float)) and not isinstance(offset, bool) and abs(offset) <= 18 * 3600:
        return utc.astimezone(dt.timezone(dt.timedelta(seconds=int(offset))))
    try:
        return utc.astimezone()
    except (OverflowError, OSError, ValueError):
        return utc


def master_ref(asset_rec):
    ref = _field(asset_rec.get("fields") or {}, "masterRef")
    if isinstance(ref, dict):
        name = ref.get("recordName")
        return name if isinstance(name, str) else None
    return None


def parse_asset(asset_rec, master_rec, source, hidden, now=None):
    now = time.time() if now is None else now
    af = asset_rec.get("fields") or {}
    mf = master_rec.get("fields") or {}
    asset = Asset()
    asset.asset_id = asset_rec["recordName"]
    asset.master_id = master_rec["recordName"]
    uti = str(_field(mf, "itemType") or _field(mf, "resOriginalFileType") or "")
    asset.is_video = is_video_uti(uti) or is_video_uti(str(_field(mf, "resOriginalFileType") or ""))
    name = decode_text(mf.get("filenameEnc"))
    asset.filename = name or (asset.asset_id + uti_ext(uti, ".MOV" if asset.is_video else ".JPG"))
    ms = _field(af, "assetDate")
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        ms = _field(af, "addedDate")
    asset.created = capture_time(ms, _field(af, "timeZoneOffset"))
    asset.favorite = bool(_field(af, "isFavorite"))
    asset.hidden = bool(hidden or _field(af, "isHidden"))
    asset.library = source.library
    asset.db = source.db
    asset.zone = source.zone
    asset.albums = []
    kind = "video" if asset.is_video else "photo"
    found = []
    info = _resource(mf, "resOriginal")
    if info:
        found.append(Res("original", kind, "resOriginal", "master", info, now))
    info = _resource(mf, "resOriginalAlt")
    if info:
        found.append(Res("alt", kind, "resOriginalAlt", "master", info, now))
    if asset.is_video:
        info = _resource(af, "resVidFull")
        if info:
            found.append(Res("edited", "edited", "resVidFull", "asset", info, now))
    else:
        info = _resource(mf, "resOriginalVidCompl")
        if info:
            found.append(Res("live", "live_video", "resOriginalVidCompl", "master", info, now))
        info = _resource(af, "resJPEGFull")
        if info:
            found.append(Res("edited", "edited", "resJPEGFull", "asset", info, now))
        info = _resource(af, "resVidCompl")
        if info:
            found.append(Res("edited_live", "edited", "resVidCompl", "asset", info, now))
    asset.resources = found
    return asset


class Library(object):
    def __init__(self):
        self.assets = []
        self.sources = []
        self.warnings = []
        self.problems = []
        self.unreadable = []
        self.limited = False
        self.complete = True
        self.recently_deleted = None
        self.album_names = []
        self.count_mismatch = False


class ListingReporter(object):
    def __init__(self, out):
        self.out = out
        self.ticker = Ticker(out)
        self.count = 0

    def add(self):
        self.count += 1
        if self.ticker.due():
            self.out.status("Reading your library from iCloud: %s items so far" % fmt_int(self.count))

    def done(self):
        self.out.status("Read %s items from iCloud." % fmt_int(self.count))
        self.out.end_status()


def discover_sources(client, lib):
    sources = [Source("personal", "private", dict(PRIMARY_ZONE))]
    seen = {PRIMARY_ZONE["zoneName"]}
    for db in ("private", "shared"):
        try:
            data = client.post_json(db, "zones/list", {}, recover=(db == "private"))
        except CloudError as exc:
            if db == "private":
                lib.warnings.append("Could not list iCloud Photos zones (%s). Only the personal "
                                    "library was read." % exc.message)
            continue
        for zone in data.get("zones") or []:
            if not isinstance(zone, dict) or zone.get("deleted"):
                continue
            zid = zone.get("zoneID")
            if not isinstance(zid, dict):
                continue
            name = str(zid.get("zoneName") or "")
            if name.startswith(SHARED_ZONE_PREFIX) and name not in seen:
                seen.add(name)
                sources.append(Source("shared", db, dict(zid)))
    return sources


def fetch_counts(client, source, lib):
    ids = [COUNT_ALL, COUNT_HIDDEN, COUNT_DELETED]
    payload = {"batch": [{
        "resultsLimit": 1,
        "query": {
            "recordType": "HyperionIndexCountLookup",
            "filterBy": {"fieldName": "indexCountID", "comparator": "IN",
                         "fieldValue": {"type": "STRING_LIST", "value": [cid]}},
        },
        "zoneWide": True,
        "zoneID": source.zone,
    } for cid in ids]}
    try:
        data = client.post_json(source.db, "internal/records/query/batch", payload, recover=False)
    except CloudError as exc:
        lib.warnings.append("iCloud did not report item counts for %s (%s)." % (source.label, exc.message))
        return {}
    counts = {}
    for cid, entry in zip(ids, data.get("batch") or []):
        try:
            counts[cid] = int(entry["records"][0]["fields"]["itemCount"]["value"])
        except (KeyError, IndexError, TypeError, ValueError):
            counts[cid] = None
    return counts


def _query_payload(source, list_type, rank, direction):
    return {
        "query": {
            "recordType": list_type,
            "filterBy": [
                {"fieldName": "startRank", "comparator": "EQUALS",
                 "fieldValue": {"type": "INT64", "value": int(rank)}},
                {"fieldName": "direction", "comparator": "EQUALS",
                 "fieldValue": {"type": "STRING", "value": direction}},
            ],
        },
        "resultsLimit": PAGE_SIZE,
        "desiredKeys": DESIRED_KEYS,
        "zoneID": source.zone,
    }


def lookup_records(client, db, zone, names):
    """recordName -> record (or error entry), in batches."""
    found = {}
    names = list(names)
    for start in range(0, len(names), LOOKUP_BATCH):
        chunk = names[start:start + LOOKUP_BATCH]
        data = client.post_json(db, "records/lookup", {
            "records": [{"recordName": n} for n in chunk],
            "zoneID": zone,
            "desiredKeys": DESIRED_KEYS,
        })
        for rec in data.get("records") or []:
            if isinstance(rec, dict) and isinstance(rec.get("recordName"), str):
                found[rec["recordName"]] = rec
    return found


def iter_assets(client, source, list_type, hidden, lib, expected=None):
    """Yield every asset of one list, oldest first, joined with its master record."""
    seen = set()
    window = {}
    previous = {}
    orphans = []
    listed = 0
    direction = "ASCENDING"
    rank = 0
    pages = 0
    while True:
        data = client.post_json(source.db, "records/query",
                                _query_payload(source, list_type, rank, direction))
        records = data.get("records") or []
        assets = []
        masters = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("serverErrorCode"):
                source.errors.append(str(rec.get("serverErrorCode")))
                continue
            kind = rec.get("recordType")
            if kind == "CPLMaster" and isinstance(rec.get("recordName"), str):
                masters[rec["recordName"]] = rec
            elif kind == "CPLAsset" and isinstance(rec.get("recordName"), str):
                assets.append(rec)
        if not assets:
            if pages == 0 and direction == "ASCENDING" and expected:
                direction = "DESCENDING"
                rank = expected - 1
                pages += 1
                continue
            break
        pages += 1
        window = dict(previous)
        window.update(masters)
        new = 0
        for rec in assets:
            name = rec["recordName"]
            if name in seen:
                continue
            seen.add(name)
            new += 1
            listed += 1
            master = window.get(master_ref(rec) or "")
            if master is None:
                orphans.append(rec)
                continue
            yield parse_asset(rec, master, source, hidden)
        previous = masters
        if new == 0:
            break
        if direction == "ASCENDING":
            rank += len(assets)
        else:
            rank -= len(assets)
            if rank < 0:
                break
        if pages > 1000000:
            break
    if orphans:
        wanted = set(master_ref(rec) for rec in orphans if master_ref(rec))
        found = lookup_records(client, source.db, source.zone, wanted) if wanted else {}
        for rec in orphans:
            master = found.get(master_ref(rec) or "")
            if master is None or master.get("serverErrorCode"):
                lib.unreadable.append(rec["recordName"])
                continue
            yield parse_asset(rec, master, source, hidden)
    source.listed[list_type] = listed


def list_library(client, out, limit=None, keep=None):
    """Read the whole library: personal library, hidden items and Shared Library."""
    lib = Library()
    reporter = ListingReporter(out)
    lib.sources = discover_sources(client, lib)
    for source in lib.sources:
        source.counts = fetch_counts(client, source, lib)
        if source.library == "personal":
            lib.recently_deleted = source.counts.get(COUNT_DELETED)
        for list_type, hidden, count_id in ((LIST_ALL, False, COUNT_ALL),
                                            (LIST_HIDDEN, True, COUNT_HIDDEN)):
            try:
                for asset in iter_assets(client, source, list_type, hidden, lib,
                                         expected=source.counts.get(count_id)):
                    reporter.add()
                    if keep is not None and not keep(asset):
                        continue
                    lib.assets.append(asset)
                    if limit and len(lib.assets) >= limit:
                        lib.limited = True
                        lib.complete = False
                        reporter.done()
                        return lib
            except CloudError as exc:
                lib.complete = False
                message = "Could not read %s%s: %s" % (
                    "the hidden items of " if hidden else "", source.label, exc.message)
                if source.library == "personal" and not hidden:
                    raise HomecloudError(message)
                lib.problems.append(message)
    reporter.done()
    for source in lib.sources:
        if source.errors:
            lib.complete = False
            lib.problems.append("iCloud reported %d unreadable records in %s." % (
                len(source.errors), source.label))
        for list_type, count_id, what in ((LIST_ALL, COUNT_ALL, ""), (LIST_HIDDEN, COUNT_HIDDEN, "hidden ")):
            expected = source.counts.get(count_id)
            listed = source.listed.get(list_type)
            if expected is not None and listed is not None and listed < expected:
                lib.count_mismatch = True
                lib.problems.append("iCloud reports %s %sitems in %s, but only %s could be read." % (
                    fmt_int(expected), what, source.label, fmt_int(listed)))
    if lib.unreadable:
        lib.problems.append("%s items have no original file record in iCloud." % fmt_int(len(lib.unreadable)))
    return lib


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def library_stats(lib):
    stats = {
        "total_assets": 0, "photos": 0, "videos": 0, "live_photos": 0, "edited": 0,
        "hidden": 0, "favorites": 0, "shared_library_items": 0,
        "original_bytes": 0, "download_files": 0, "download_bytes": 0,
    }
    by_year = {}
    for asset in lib.assets:
        stats["total_assets"] += 1
        stats["videos" if asset.is_video else "photos"] += 1
        stats["live_photos"] += 1 if asset.is_live else 0
        stats["edited"] += 1 if asset.is_edited else 0
        stats["hidden"] += 1 if asset.hidden else 0
        stats["favorites"] += 1 if asset.favorite else 0
        stats["shared_library_items"] += 1 if asset.library == "shared" else 0
        stats["original_bytes"] += asset.original_bytes
        stats["download_files"] += len(asset.resources)
        stats["download_bytes"] += asset.total_bytes
        year = str(asset.created.year) if asset.created else "unknown"
        row = by_year.setdefault(year, {"assets": 0, "original_bytes": 0, "download_bytes": 0})
        row["assets"] += 1
        row["original_bytes"] += asset.original_bytes
        row["download_bytes"] += asset.total_bytes
    stats["by_year"] = dict(sorted(by_year.items()))
    return stats


def print_scan(out, lib, stats, storage, account_name):
    width = 22

    def row(label, value, note=""):
        out.line("  %-*s %12s%s" % (width, label, value, ("  " + note) if note else ""))

    out.line("")
    out.line("Your iCloud Photos library" + (" (%s)" % mask_id(account_name) if account_name else ""))
    row("Items", fmt_int(stats["total_assets"]))
    row("  Photos", fmt_int(stats["photos"]))
    row("  Videos", fmt_int(stats["videos"]))
    row("Live Photos", fmt_int(stats["live_photos"]))
    row("Edited", fmt_int(stats["edited"]))
    row("Hidden", fmt_int(stats["hidden"]))
    row("Favorites", fmt_int(stats["favorites"]))
    row("Albums", fmt_int(len(lib.album_names)))
    if stats["shared_library_items"]:
        row("Shared Library", fmt_int(stats["shared_library_items"]), "items, included above")
    row("Originals", fmt_size(stats["original_bytes"]))
    row("Full download", fmt_size(stats["download_bytes"]),
        "%s files: originals, Live Photo videos, edited versions" % fmt_int(stats["download_files"]))
    if storage:
        out.line("")
        out.line("iCloud storage")
        if storage.get("used_bytes") is not None:
            total = storage.get("total_bytes")
            row("Used", fmt_size(storage["used_bytes"]),
                ("of %s" % fmt_size(total)) if total else "")
        if storage.get("photos_bytes") is not None:
            row(storage.get("photos_label") or "Photos and Videos", fmt_size(storage["photos_bytes"]))
    out.line("")
    out.line("  %-8s %10s %14s" % ("Year", "Items", "Size"))
    for year, info in stats["by_year"].items():
        out.line("  %-8s %10s %14s" % (year, fmt_int(info["assets"]), fmt_size(info["download_bytes"])))
    out.line("")
    if lib.recently_deleted:
        out.line("Recently Deleted holds %s items. They are left out." % fmt_int(lib.recently_deleted))
    for warning in lib.warnings:
        out.line("Note: " + warning)
    for problem in lib.problems:
        out.line("Problem: " + problem)
    out.line("Nothing was changed in iCloud.")


def paused_text(cloud):
    return APPROVAL_PAUSED if getattr(cloud, "adp", False) else THROTTLE_PAUSED


def run_scan(cloud, out, json_path=None, account_name="", data_out=None):
    """Count the library. With json_path "-", the JSON goes to data_out (stdout)
    and progress stays on out (stderr), so the JSON can be piped."""
    try:
        lib = list_library(cloud.client, out)
    except ApprovalNeeded:
        out.end_status()
        out.line(paused_text(cloud))
        return EXIT_PAUSED
    names, _, warnings = cloud.albums(False, out)
    lib.album_names = names
    lib.warnings.extend(warnings)
    stats = library_stats(lib)
    storage = None
    try:
        storage = cloud.storage()
    except Exception:
        storage = None
    data = dict(stats)
    data.update({
        "albums": len(lib.album_names),
        "recently_deleted": lib.recently_deleted,
        "icloud_counts": {
            ("%s:%s" % (s.library, s.zone.get("zoneName"))): s.counts for s in lib.sources},
        "listed": {
            ("%s:%s" % (s.library, s.zone.get("zoneName"))): s.listed for s in lib.sources},
        "icloud_storage": storage,
        "complete": lib.complete and not lib.count_mismatch,
        "warnings": lib.warnings,
        "problems": lib.problems,
        "scanned_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
    })
    if json_path == "-":
        (data_out or out).line(json.dumps(data, indent=2))
    else:
        print_scan(out, lib, stats, storage, account_name)
        if json_path:
            path = os.path.abspath(os.path.expanduser(json_path))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.write("\n")
            out.line("Wrote the numbers to %s" % path)
    return EXIT_OK if (lib.complete and not lib.problems) else EXIT_FAIL


def cmd_scan(args, out=None):
    out = out or OUT
    data_out = None
    if args.json == "-":
        data_out, out = out, Out(sys.stderr)
    cloud = RealCloud(out, APPROVAL_WAIT_MINUTES)
    try:
        out.line("Signed in as %s. Reading your library. This only reads." % mask_id(cloud.name))
        return run_scan(cloud, out, args.json, cloud.name, data_out)
    finally:
        secure_session_dir()


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------

def _ask_code(ask, prompt):
    code = ""
    while not code:
        code = re.sub(r"[\s-]", "", ask(prompt) or "")
    return code


def _two_factor(api, E, ask, out):
    keys = None
    try:
        keys = api.security_key_names
    except Exception:
        keys = None
    if keys:
        devices = list(api.fido2_devices or [])
        if not devices:
            out.line("Your Apple Account asks for a security key. Plug it in, then run login again.")
            return EXIT_FAIL
        out.line("Touch your security key to finish signing in.")
        try:
            api.confirm_security_key(devices[0])
        except E.PyiCloudException:
            out.line("The security key check did not work. Run login again.")
            return EXIT_FAIL
        return EXIT_OK
    try:
        api.request_2fa_code()
    except E.PyiCloudNoTrustedNumberAvailable:
        out.line("Apple found no trusted phone number to send a code to. Add one in "
                 "Settings > [your name] > Sign-In & Security, then run login again.")
        return EXIT_FAIL
    except E.PyiCloudConnectionException:
        raise
    except E.PyiCloudAPIResponseException:
        try:
            api.use_existing_trusted_device_code()
        except Exception:
            pass
    method = getattr(api, "two_factor_delivery_method", "unknown")
    if method == "sms":
        out.line("Apple sent a 6-digit code by text message to your trusted phone number.")
    else:
        out.line("Apple sent a 6-digit code to your trusted devices. On your iPhone, "
                 "tap Allow if it asks, then read the code.")
    attempts = 3
    for attempt in range(attempts):
        code = _ask_code(ask, "Enter the code: ")
        try:
            ok = api.validate_2fa_code(code)
        except E.PyiCloudTrustedDeviceVerificationException:
            ok = False
        if ok:
            break
        left = attempts - attempt - 1
        if not left:
            out.line("That code did not work. Run login again to get a new code.")
            return EXIT_FAIL
        out.line("That code did not work. %d %s left." % (left, "try" if left == 1 else "tries"))
    if not api.is_trusted_session:
        api.trust_session()
    return EXIT_OK


def _two_step(api, E, ask, out):
    devices = list(api.trusted_devices or [])
    if not devices:
        out.line("Apple asks for a code but lists no trusted devices. Run login again later.")
        return EXIT_FAIL
    out.line("Where should Apple send the code?")
    for index, device in enumerate(devices):
        label = device.get("deviceName") or ("text message to %s" % device.get("phoneNumber", "your phone"))
        out.line("  %d: %s" % (index, label))
    choice = (ask("Number [0]: ") or "0").strip()
    try:
        device = devices[int(choice)]
    except (ValueError, IndexError):
        device = devices[0]
    if not api.send_verification_code(device):
        out.line("Apple could not send the code. Run login again.")
        return EXIT_FAIL
    for attempt in range(3):
        code = _ask_code(ask, "Enter the code: ")
        if api.validate_verification_code(device, code):
            return EXIT_OK
        left = 2 - attempt
        if left:
            out.line("That code did not work. %d %s left." % (left, "try" if left == 1 else "tries"))
    out.line("That code did not work. Run login again to get a new code.")
    return EXIT_FAIL


def _finish_login(api, name, out, wait_minutes):
    state = web_access_state(api)
    if state is not None and state.get("isWebAccessAllowed") is False:
        out.line(ADP_MESSAGE)
        return EXIT_BLOCKED
    if state and state.get("isICDRSDisabled"):
        out.line("Advanced Data Protection is on. Your iPhone may ask you to allow access "
                 "to your iCloud data. Tap Allow.")
    try:
        acquire_photos(api, out, wait_minutes * 60)
    except AccessBlocked:
        out.line(ADP_MESSAGE)
        return EXIT_BLOCKED
    except ApprovalNeeded:
        out.line("No approval arrived in time. Run login again and tap Allow on your iPhone "
                 "when it asks.")
        return EXIT_PAUSED
    except HomecloudError as exc:
        out.line(str(exc))
        return exc.exit_code
    out.line("")
    out.line("Signed in as %s. iCloud Photos is ready to read." % mask_id(name))
    out.line("Your password was not saved anywhere.")
    out.line(SESSION_NOTE.format(folder=session_dir()))
    out.line("Next, count your library:  python3 scripts/icloud.py scan")
    return EXIT_OK


def cmd_login(args, service_factory=None, ask=None, ask_secret=None, out=None):
    out = out or OUT
    interactive = ask is None
    ask = ask or input
    ask_secret = ask_secret or getpass.getpass
    install_request_guard()
    install_write_blockers()
    quiet_library_logs()
    from pyicloud import exceptions as E
    if service_factory is None:
        from pyicloud import PyiCloudService as service_factory
    wait_minutes = getattr(args, "approval_wait", APPROVAL_WAIT_MINUTES) or APPROVAL_WAIT_MINUTES
    china = bool(getattr(args, "china_mainland", False))
    old_umask = os.umask(0o077)
    try:
        secure_session_dir()
        # A saved session that still works needs no password.
        try:
            api = open_session(service_factory, renew=False)
            name = signed_in_name(api, "")
            out.line("Already signed in as %s. No password needed." % mask_id(name or "your Apple Account"))
            return _finish_login(api, name, out, wait_minutes)
        except SessionExpired:
            pass
        if interactive and not sys.stdin.isatty():
            out.line("Run login in your own Terminal window, so you can type your password there.")
            return EXIT_FAIL
        out.line("Sign in to iCloud")
        out.line("Homecloud only reads from iCloud. It never changes or deletes anything there.")
        out.line("Your password goes only to Apple. It is never saved, shown or logged.")
        out.line("")
        apple_id = (ask("Apple Account (email or phone number): ") or "").strip()
        if not apple_id:
            out.line("No Apple Account given. Run login again.")
            return EXIT_FAIL
        password = ask_secret("Password (nothing shows while you type): ")
        if not password:
            out.line("No password given. Run login again.")
            return EXIT_FAIL
        try:
            api = service_factory(apple_id, password, **_service_kwargs(china))
        except E.PyiCloudAccountLockedException:
            out.line(LOCKED_MESSAGE)
            return EXIT_FAIL
        except E.PyiCloudConnectionException:
            out.line(NO_CONNECTION)
            return EXIT_FAIL
        except E.PyiCloudAcceptTermsException:
            out.line(TERMS_MESSAGE)
            return EXIT_FAIL
        except E.PyiCloudFailedLoginException:
            out.line(WRONG_PASSWORD)
            return EXIT_FAIL
        except E.PyiCloudServiceNotActivatedException:
            out.line(NOT_ACTIVATED)
            return EXIT_FAIL
        except E.PyiCloudException as exc:
            out.line("Apple refused the sign-in: %s" % short_error(exc))
            return EXIT_FAIL
        finally:
            password = None
        try:
            api._password_raw = None
        except Exception:
            pass
        if api.requires_2fa:
            code = _two_factor(api, E, ask, out)
            if code:
                return code
        elif api.requires_2sa:
            code = _two_step(api, E, ask, out)
            if code:
                return code
        if not api.is_trusted_session:
            try:
                api.trust_session()
            except E.PyiCloudException:
                pass
            if not api.is_trusted_session:
                out.line("Apple did not mark this computer as trusted. You may need a code again next time.")
        save_account(apple_id, china)
        return _finish_login(api, signed_in_name(api, apple_id), out, wait_minutes)
    except E.PyiCloudAccountLockedException:
        out.line(LOCKED_MESSAGE)
        return EXIT_FAIL
    except E.PyiCloudConnectionException:
        out.line(NO_CONNECTION)
        return EXIT_FAIL
    except (EOFError, KeyboardInterrupt):
        out.line("")
        out.line("Sign-in cancelled.")
        return EXIT_STOPPED
    finally:
        os.umask(old_umask)
        try:
            secure_session_dir()
        except OSError:
            pass


# --------------------------------------------------------------------------
# download and verify come next
# --------------------------------------------------------------------------

class HttpFetcher(object):
    """Downloads iCloud files. One requests session per worker thread."""

    def __init__(self, user_agent=BROWSER_UA, timeout=(20.0, 120.0)):
        import requests
        self._requests = requests
        self._local = threading.local()
        self.user_agent = user_agent
        self.timeout = timeout

    def _session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._requests.Session()
            session.headers.update({
                "User-Agent": self.user_agent,
                "Origin": "https://www.icloud.com",
                "Referer": "https://www.icloud.com/",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            })
            self._local.session = session
        return session

    def get(self, url, headers):
        check_request("GET", url)
        try:
            response = self._session().get(url, headers=headers, stream=True, timeout=self.timeout)
        except ReadOnlyViolation:
            raise
        except self._requests.exceptions.RequestException as exc:
            raise FetchError("retry", "could not reach iCloud (%s)" % type(exc).__name__)
        return _HttpResponse(response)


class _HttpResponse(object):
    def __init__(self, response):
        self._response = response
        self.status = response.status_code
        self.headers = response.headers

    def iter_chunks(self, size):
        try:
            for chunk in self._response.iter_content(chunk_size=size):
                if chunk:
                    yield chunk
        except Exception as exc:
            raise FetchError("retry", "the connection dropped (%s)" % type(exc).__name__)

    def close(self):
        try:
            self._response.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Manifest and local files
# --------------------------------------------------------------------------

def state_path(root, name):
    return os.path.join(root, STATE_DIR, name)


def read_manifest(root):
    """Rows as dicts, plus lines that could not be read (kept as they are)."""
    rows, bad = [], []
    try:
        fh = open(state_path(root, MANIFEST_NAME), "r", encoding="utf-8")
    except FileNotFoundError:
        return rows, bad
    with fh:
        for line in fh:
            text = line.rstrip("\r\n")
            if not text.strip():
                continue
            try:
                row = json.loads(text)
            except ValueError:
                bad.append(text)
                continue
            if isinstance(row, dict) and isinstance(row.get("path"), str):
                rows.append(row)
            else:
                bad.append(text)
    return rows, bad


def _replace(src, dst):
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))


def _discard(path):
    """Remove one of our own temporary files, if it is there. These only ever live
    inside a .homecloud folder: this never removes a photo or video."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    if STATE_DIR not in parts[:-1]:
        raise RuntimeError("Homecloud only removes its own temporary files, not %s" % path)
    try:
        os.remove(path)
    except OSError:
        pass


def _write_atomic(path, write):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".homecloud-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    except BaseException:
        _discard(tmp)
        raise


def write_manifest(root, rows, bad=()):
    def write(fh):
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        for text in bad:
            fh.write(text + "\n")
    _write_atomic(state_path(root, MANIFEST_NAME), write)


def write_json(path, data):
    _write_atomic(path, lambda fh: fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n"))


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def rel_to_abs(root, rel):
    """DIR + a manifest path, or None if the path could leave DIR."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/") \
            or re.match(r"^[A-Za-z]:", rel):
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return os.path.join(root, *parts)


def sha256_file(path, on_bytes=None):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
            if on_bytes is not None:
                on_bytes(len(block))
    return digest.hexdigest()


def set_mtime(path, timestamp):
    """File date = the date the photo was taken."""
    if timestamp is None:
        return
    try:
        os.utime(path, (timestamp, timestamp))
    except (OSError, OverflowError, ValueError):
        pass


class DirLock(object):
    """Stops two downloads or verifies from writing the same folder at once."""

    def __init__(self, root, optional=False):
        self.path = state_path(root, LOCK_NAME)
        self.optional = optional
        self.fh = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fh = open(self.path, "a+")
        except OSError:
            if self.optional:
                return self
            raise
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise HomecloudError("Another download or verify is already running for this folder. "
                                 "Wait for it to finish, then try again.")
        self.fh = fh
        return self

    def __exit__(self, *exc):
        if self.fh is None:
            return False
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.fh.close()
        self.fh = None
        return False


SYNCED_FOLDER = """{path}
is inside a folder that syncs to {reason}. A synced folder would upload your whole
library to the cloud again, and can turn local files into online-only placeholders.
Pick a folder outside it, for example a new folder in your home folder or on an
external drive. If you are sure, add --allow-synced-folder."""


def synced_folder_reason(path):
    real = os.path.realpath(path).replace("\\", "/")
    low = real.lower() + "/"
    if "/library/mobile documents/" in low:
        return "iCloud Drive"
    if "/library/cloudstorage/" in low:
        return "a cloud storage service"
    for part in low.split("/"):
        if part.startswith("onedrive"):
            return "OneDrive"
        if part in ("dropbox", "google drive"):
            return part.title()
    if sys.platform == "darwin":
        home = os.path.realpath(os.path.expanduser("~")).replace("\\", "/").lower()
        for name in ("Desktop", "Documents"):
            top = home + "/" + name.lower() + "/"
            synced = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/" + name)
            if low.startswith(top) and os.path.isdir(synced):
                return "iCloud Drive (your %s folder syncs to iCloud)" % name
    return None


# --------------------------------------------------------------------------
# File names
# --------------------------------------------------------------------------

_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_RESERVED = set(["CON", "PRN", "AUX", "NUL"] + ["COM%d" % i for i in range(1, 10)]
                + ["LPT%d" % i for i in range(1, 10)])


def clean_filename(name):
    """A file name that is safe on macOS, Windows and Linux."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    if name.startswith("."):
        name = "_" + name[1:]
    if not name:
        name = "unnamed"
    if name.split(".", 1)[0].upper() in _RESERVED:
        name = "_" + name
    if len(name.encode("utf-8")) > 180:
        stem, ext = os.path.splitext(name)
        ext = ext[:12]
        stem = stem.encode("utf-8")[: 180 - len(ext.encode("utf-8"))].decode("utf-8", "ignore")
        name = (stem.rstrip(". ") or "unnamed") + ext
    return name


def name_key(name):
    """macOS and Windows treat IMG_0001.HEIC and img_0001.heic as the same file."""
    return unicodedata.normalize("NFC", name).casefold()


def variant_name(stem, ext, res, is_video):
    if res.variant == "original":
        return stem + (ext or uti_ext(res.uti, ".MOV" if is_video else ".JPG"))
    if res.variant == "alt":
        alt_ext = uti_ext(res.uti, ".RAW")
        if name_key(alt_ext) == name_key(ext or ""):
            return stem + "_alt" + alt_ext
        return stem + alt_ext
    if res.variant == "live":
        return stem + uti_ext(res.uti, ".MOV")
    if res.variant == "edited":
        return stem + "_edited" + uti_ext(res.uti, ".MOV" if is_video else ".JPG")
    if res.variant == "edited_live":
        return stem + "_edited" + uti_ext(res.uti, ".MOV")
    return stem + "_" + res.variant + (ext or "")


class NameBook(object):
    """Which names are taken in each month folder: by the manifest or by files on disk."""

    def __init__(self, root, rows):
        self.root = root
        self.taken = {}
        self.scanned = set()
        for row in rows:
            rel = row.get("path")
            if isinstance(rel, str) and "/" in rel:
                folder, _, base = rel.rpartition("/")
                self.taken.setdefault(folder, set()).add(name_key(base))

    def _scan(self, folder):
        if folder in self.scanned:
            return
        self.scanned.add(folder)
        bucket = self.taken.setdefault(folder, set())
        try:
            for entry in os.listdir(os.path.join(self.root, *folder.split("/"))):
                bucket.add(name_key(entry))
        except OSError:
            pass

    def free(self, folder, names):
        self._scan(folder)
        keys = [name_key(n) for n in names]
        bucket = self.taken.get(folder, set())
        return len(set(keys)) == len(keys) and not any(k in bucket for k in keys)

    def take(self, folder, names):
        self._scan(folder)
        bucket = self.taken.setdefault(folder, set())
        for name in names:
            bucket.add(name_key(name))


def _split_name(asset):
    base = clean_filename(asset.filename)
    stem, ext = os.path.splitext(base)
    if not stem:
        stem, ext = base, ""
    if not ext:
        orig = next((r for r in asset.resources if r.variant == "original"), None)
        ext = uti_ext(orig.uti if orig else "", ".MOV" if asset.is_video else ".JPG")
    return stem, ext


def allocate_names(book, asset, resources, existing_rows):
    """variant -> (path, logical file name). Files of one item share one stem, so a
    photo and its Live Photo video stay a pair: IMG_0001 (2).HEIC and IMG_0001 (2).MOV."""
    original = next((r for r in existing_rows if r.get("variant") == "original"), None)
    if original is None:
        folder = asset.folder
        stem, ext = _split_name(asset)
        for number in range(1, 100000):
            current = stem if number == 1 else "%s (%d)" % (stem, number)
            names = dict((r.variant, variant_name(current, ext, r, asset.is_video)) for r in resources)
            if book.free(folder, names.values()):
                book.take(folder, names.values())
                return dict((r.variant, (folder + "/" + names[r.variant],
                                         variant_name(stem, ext, r, asset.is_video)))
                            for r in resources)
        raise HomecloudError("Too many files named %s in %s" % (asset.filename, folder))
    # New files for an item that is already here go next to its original.
    folder, _, base = original["path"].rpartition("/")
    stem, ext = os.path.splitext(base)
    lstem, lext = os.path.splitext(original.get("filename") or base)
    out = {}
    for res in resources:
        for number in range(1, 100000):
            current = stem if number == 1 else "%s (%d)" % (stem, number)
            name = variant_name(current, ext, res, asset.is_video)
            if book.free(folder, [name]):
                book.take(folder, [name])
                out[res.variant] = (folder + "/" + name, variant_name(lstem, lext, res, asset.is_video))
                break
    return out


# --------------------------------------------------------------------------
# download: plan
# --------------------------------------------------------------------------

class Job(object):
    __slots__ = ("asset", "res", "row", "refreshes", "proactive", "progress")

    def __init__(self, asset, res, row):
        self.asset = asset
        self.res = res
        self.row = row
        self.refreshes = 0
        self.proactive = False
        self.progress = 0


class Plan(object):
    def __init__(self):
        self.jobs = []
        self.keys = set()
        self.total_files = 0
        self.total_bytes = 0
        self.done_files = 0
        self.done_bytes = 0
        self.adopted = 0
        self.new_rows = 0


def new_row(asset, res, path, filename):
    return {
        "asset_id": asset.asset_id,
        "filename": filename,
        "path": path,
        "size": res.size,
        "sha256": None,
        "created": asset.created.isoformat(timespec="seconds") if asset.created else None,
        "kind": res.kind,
        "favorite": asset.favorite,
        "hidden": asset.hidden,
        "albums": sorted(set(asset.albums)),
        "source": SOURCE,
        "status": "missing",
        "variant": res.variant,
        "library": asset.library,
    }


def _update_row(row, asset, res, albums_known):
    row["favorite"] = asset.favorite
    row["hidden"] = asset.hidden
    if albums_known:
        row["albums"] = sorted(set(asset.albums))
    row["kind"] = res.kind
    row["library"] = asset.library
    if asset.created is not None:
        row["created"] = asset.created.isoformat(timespec="seconds")
    if row.get("size") != res.size:
        # iCloud holds a different file now, for example a new edit. Get it again.
        row["size"] = res.size
        if row.get("status") == "done":
            row["status"] = "missing"
            row["sha256"] = None


def _is_complete(root, row, res, asset, plan):
    path = rel_to_abs(root, row.get("path"))
    if path is None:
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if size != res.size:
        if row.get("status") == "done":
            row["status"] = "missing"
            row["sha256"] = None
        return False
    if row.get("status") == "done":
        return True
    digest = sha256_file(path)
    if row.get("sha256") and row["sha256"] != digest:
        # verify found this file damaged. Download it again.
        return False
    # Our own planned path holds a file of the right size: an earlier run moved it
    # into place but stopped before it could record that. Record it now.
    row["sha256"] = digest
    row["status"] = "done"
    row.pop("error", None)
    set_mtime(path, asset.timestamp)
    plan.adopted += 1
    return True


def build_plan(root, assets, rows, want=None, albums_known=True):
    """Match every wanted file to a manifest row. New files get new, unused names."""
    plan = Plan()
    mine = {}
    for row in rows:
        if row.get("source") == SOURCE and isinstance(row.get("asset_id"), str) and row.get("variant"):
            mine[(row["asset_id"], row["variant"])] = row
    book = NameBook(root, rows)
    for asset in assets:
        resources = [r for r in asset.resources if want is None or want(r)]
        if not resources:
            continue
        existing = dict((r.variant, mine.get((asset.asset_id, r.variant))) for r in resources)
        missing = [r for r in resources if existing[r.variant] is None]
        if missing:
            known = [row for row in (mine.get((asset.asset_id, v)) for v in
                                     ("original", "alt", "live", "edited", "edited_live")) if row]
            names = allocate_names(book, asset, missing, known)
            for res in missing:
                path, filename = names[res.variant]
                row = new_row(asset, res, path, filename)
                rows.append(row)
                mine[(asset.asset_id, res.variant)] = row
                existing[res.variant] = row
                plan.new_rows += 1
        for res in resources:
            row = existing[res.variant]
            plan.keys.add((asset.asset_id, res.variant))
            _update_row(row, asset, res, albums_known)
            plan.total_files += 1
            plan.total_bytes += res.size
            if _is_complete(root, row, res, asset, plan):
                plan.done_files += 1
                plan.done_bytes += res.size
            else:
                plan.jobs.append(Job(asset, res, row))
    return plan


def prune_rows(rows, keys):
    """Drop rows for files that were never downloaded and are no longer in iCloud."""
    kept = []
    for row in rows:
        if row.get("source") == SOURCE and row.get("status") != "done" \
                and (row.get("asset_id"), row.get("variant")) not in keys:
            continue
        kept.append(row)
    return kept, len(rows) - len(kept)


def url_expiry(url):
    try:
        value = parse_qs(urlsplit(url).query).get("e")
        if value:
            expiry = float(value[0])
            return expiry / 1000.0 if expiry > 1e11 else expiry
    except (TypeError, ValueError):
        pass
    return None


def _link_expiring(res, now):
    expiry = url_expiry(res.url or "")
    if expiry is not None:
        return expiry - now < URL_REFRESH_MARGIN
    return now - res.url_time > URL_ASSUMED_LIFETIME


# --------------------------------------------------------------------------
# download: engine
# --------------------------------------------------------------------------

class Outcome(object):
    __slots__ = ("status", "sha256", "size", "error", "auth")

    def __init__(self, status, sha256=None, size=0, error="", auth=False):
        self.status = status
        self.sha256 = sha256
        self.size = size
        self.error = error
        self.auth = auth


class Gate(object):
    """A shared pause: when iCloud asks to slow down, every worker waits."""

    def __init__(self):
        self._until = 0.0
        self._lock = threading.Lock()

    def pause(self, seconds):
        with self._lock:
            until = time.monotonic() + max(0.0, seconds)
            if until > self._until + 1:
                self._until = until
                return True
            return False

    def wait(self, stop):
        while not stop.is_set():
            with self._lock:
                left = self._until - time.monotonic()
            if left <= 0:
                return
            stop.wait(min(left, 1.0))


def _range_ok(headers, start):
    try:
        value = headers.get("Content-Range")
    except Exception:
        value = None
    if not value:
        return True
    return str(value).replace("bytes ", "").startswith("%d-" % start)


class Downloader(object):
    """Downloads planned files with a few worker threads.

    The main thread lists, plans, renews download links and writes the manifest.
    Worker threads only fetch file contents and write them to disk.
    """

    def __init__(self, root, cloud, plan, rows, bad, workers, out, retry_base=RETRY_BASE):
        self.root = root
        self.cloud = cloud
        self.client = cloud.client
        self.fetcher = cloud.fetcher
        self.queue = deque(plan.jobs)
        self.rows = rows
        self.bad = bad
        self.workers = workers
        self.out = out
        self.retry_base = retry_base
        self.stop = threading.Event()
        self.gate = Gate()
        self.refresh = []
        self.in_flight = {}
        self.failed = []
        self.files_now = 0
        self.bytes_now = 0
        self.files_done = plan.done_files
        self.bytes_done = plan.done_bytes
        self.total_files = plan.total_files
        self.total_bytes = plan.total_bytes
        self.auth_streak = 0
        self.stop_error = None
        self.interrupted = False
        self.proactive = True
        self.received = 0
        self.received_lock = threading.Lock()
        self.samples = deque()
        self.ticker = Ticker(out)
        self.started = time.monotonic()
        self.last_save = time.monotonic()
        self.save_cost = 0.0
        self.unsaved = 0
        self.partial = os.path.join(root, STATE_DIR, PARTIAL_DIR)
        os.makedirs(self.partial, exist_ok=True)

    # ---- main thread ----------------------------------------------------------
    def run(self):
        pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="homecloud-download")
        try:
            self._loop(pool)
        except KeyboardInterrupt:
            self.interrupted = True
            self.stop.set()
        except (SessionExpired, ApprovalNeeded, AccessBlocked) as exc:
            self.stop_error = exc
        except DiskFull as exc:
            self.stop_error = exc
            self.stop.set()
        finally:
            try:
                self._drain()
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
                self.show(force=True)
                self.out.end_status()
                self.save()
        return self

    def _loop(self, pool):
        while not self.stop.is_set():
            if self.refresh and (len(self.refresh) >= LOOKUP_BATCH or not self.queue
                                 or len(self.in_flight) < self.workers):
                batch, self.refresh = self.refresh[:LOOKUP_BATCH], self.refresh[LOOKUP_BATCH:]
                self._renew_links(batch)
            now = time.time()
            while self.queue and len(self.in_flight) < self.workers * 2:
                job = self.queue.popleft()
                if not job.res.url or (self.proactive and not job.proactive
                                       and _link_expiring(job.res, now)):
                    if job.proactive and not job.res.url:
                        self._fail(job, "iCloud gave no download link for this file")
                        continue
                    job.proactive = True
                    self.refresh.append(job)
                    if len(self.refresh) >= LOOKUP_BATCH:
                        break
                    continue
                self.in_flight[pool.submit(self._work, job)] = job
            if not self.in_flight:
                if not self.queue and not self.refresh:
                    break
                continue
            done, _ = wait(list(self.in_flight), timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                job = self.in_flight.pop(future)
                self._handle(job, self._result(future))
            self._tick()

    def _result(self, future):
        try:
            return future.result()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            return Outcome("failed", error=short_error(exc))

    def _drain(self):
        deadline = None
        while self.in_flight:
            if self.stop.is_set() and deadline is None:
                deadline = time.monotonic() + 15
            if deadline is not None and time.monotonic() > deadline:
                break
            try:
                done, _ = wait(list(self.in_flight), timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    job = self.in_flight.pop(future)
                    self._handle(job, self._result(future), draining=True)
            except KeyboardInterrupt:
                self.interrupted = True
                self.stop.set()
            self.show()

    def _handle(self, job, outcome, draining=False):
        row = job.row
        if outcome.status == "done":
            row["status"] = "done"
            row["sha256"] = outcome.sha256
            row["size"] = outcome.size
            row.pop("error", None)
            self.files_done += 1
            self.bytes_done += outcome.size
            self.files_now += 1
            self.bytes_now += outcome.size
            self.auth_streak = 0
            self.unsaved += 1
        elif outcome.status == "expired":
            if draining:
                return
            if outcome.auth:
                self.auth_streak += 1
            job.refreshes += 1
            if job.refreshes > 3:
                self._fail(job, outcome.error or "the download link kept failing")
            else:
                self.refresh.append(job)
            if self.auth_streak >= 8:
                self.auth_streak = 0
                self._check_access(outcome.error)
        elif outcome.status == "failed":
            self._fail(job, outcome.error)
        elif outcome.status == "disk":
            error = DiskFull("The disk is full. Free some space or pick a bigger drive, then run "
                             "the same command again. Finished files are kept.")
            if draining:
                self.stop_error = self.stop_error or error
                self.stop.set()
            else:
                raise error

    def _check_access(self, reason):
        recover = getattr(self.client, "recover_access", None)
        if recover is not None and recover(CloudError("access", reason or "downloads refused")):
            return
        raise ApprovalNeeded(reason or "iCloud refused the downloads")

    def _fail(self, job, message):
        job.row["status"] = "failed"
        job.row["error"] = message or "unknown error"
        self.failed.append(job.row)
        self.unsaved += 1

    def _renew_links(self, jobs):
        groups = {}
        for job in jobs:
            asset = job.asset
            name = asset.master_id if job.res.holder == "master" else asset.asset_id
            key = (asset.db, json.dumps(asset.zone, sort_keys=True))
            groups.setdefault(key, {}).setdefault(name, []).append(job)
        renewed = []
        for (db, zone_key), by_name in groups.items():
            try:
                found = lookup_records(self.client, db, json.loads(zone_key), list(by_name))
            except CloudError as exc:
                for name_jobs in by_name.values():
                    for job in name_jobs:
                        self._fail(job, "could not get a new download link (%s)" % exc.message)
                continue
            now = time.time()
            for name, name_jobs in by_name.items():
                rec = found.get(name)
                for job in name_jobs:
                    if rec is None or rec.get("serverErrorCode"):
                        code = (rec or {}).get("serverErrorCode") or "NOT_FOUND"
                        self._fail(job, "this item is no longer in iCloud" if code == "NOT_FOUND"
                                   else "iCloud could not look up this item (%s)" % code)
                        continue
                    info = _resource(rec.get("fields") or {}, job.res.field)
                    if not info or not info.get("url"):
                        self._fail(job, "this version of the item is no longer in iCloud")
                        continue
                    if info["size"] != job.res.size or (
                            info.get("checksum") and info["checksum"] != job.res.checksum):
                        # iCloud holds a different file now. Start it again.
                        _discard(self._part_path(job))
                        self.total_bytes += info["size"] - job.res.size
                        job.res.size = info["size"]
                        job.res.checksum = info.get("checksum")
                        job.row["size"] = info["size"]
                    job.res.url = info["url"]
                    job.res.url_time = now
                    renewed.append(job)
        for job in reversed(renewed):
            self.queue.appendleft(job)

    def _tick(self):
        self.show()
        # Save at least every minute, but never spend more than a tenth of the time
        # rewriting a very large manifest.
        if self.unsaved and time.monotonic() - self.last_save >= max(SAVE_EVERY, 10 * self.save_cost):
            self.save()

    def save(self):
        began = time.monotonic()
        try:
            write_manifest(self.root, self.rows, self.bad)
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
                raise DiskFull("The disk is full, so the manifest could not be saved. Free some "
                               "space, then run the same command again.")
            raise
        self.unsaved = 0
        self.last_save = time.monotonic()
        self.save_cost = self.last_save - began

    def show(self, force=False):
        if not self.ticker.due(force):
            return
        in_flight = sum(job.progress for job in list(self.in_flight.values()))
        shown = min(self.total_bytes, self.bytes_done + in_flight)
        if self.files_done >= self.total_files:
            left = "done"
        else:
            left = fmt_eta(self._eta(shown))
        self.out.status("%s / %s files · %s · %s" % (
            fmt_int(self.files_done), fmt_int(self.total_files),
            fmt_pair(shown, self.total_bytes), left))

    def _eta(self, shown):
        now = time.monotonic()
        with self.received_lock:
            received = self.received
        self.samples.append((now, received))
        while len(self.samples) > 2 and now - self.samples[0][0] > 180:
            self.samples.popleft()
        first_time, first_bytes = self.samples[0]
        if now - self.started < 15 or now - first_time < 5 or received <= first_bytes:
            return None
        rate = (received - first_bytes) / (now - first_time)
        return max(0, self.total_bytes - shown) / rate

    # ---- worker threads ------------------------------------------------------
    def _part_path(self, job):
        key = "%s\0%s\0%s" % (job.row["path"], job.res.size, job.res.checksum or "")
        return os.path.join(self.partial, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".part")

    def _work(self, job):
        final = rel_to_abs(self.root, job.row.get("path"))
        if final is None:
            return Outcome("failed", error="the manifest holds an unsafe path for this file")
        part = self._part_path(job)
        attempt = 0
        while True:
            if self.stop.is_set():
                return Outcome("stopped")
            self.gate.wait(self.stop)
            if self.stop.is_set():
                return Outcome("stopped")
            try:
                digest, size = self._fetch(job, part)
                os.makedirs(os.path.dirname(final), exist_ok=True)
                _replace(part, final)
                set_mtime(final, job.asset.timestamp)
                return Outcome("done", sha256=digest, size=size)
            except FetchError as exc:
                job.progress = 0
                if exc.kind == "stopped":
                    return Outcome("stopped")
                if exc.kind in ("expired", "auth"):
                    return Outcome("expired", error=exc.message, auth=exc.kind == "auth")
                attempt += 1
                if exc.kind == "throttle":
                    delay = exc.retry_after if exc.retry_after is not None else \
                        backoff(attempt, self.retry_base * 5, cap=300)
                    if self.gate.pause(delay) and delay >= 5:
                        self.out.line("iCloud asked to slow down. Waiting %d seconds." % int(delay))
                    if attempt > RETRIES * 2:
                        return Outcome("failed", error="iCloud kept asking to slow down")
                    continue
                if attempt > RETRIES:
                    return Outcome("failed", error=exc.message)
                self.stop.wait(backoff(attempt, self.retry_base))
            except OSError as exc:
                job.progress = 0
                if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
                    return Outcome("disk", error="the disk is full")
                attempt += 1
                if attempt > RETRIES:
                    return Outcome("failed", error="could not write the file (%s)" % (exc.strerror or exc))
                self.stop.wait(backoff(attempt, self.retry_base))

    def _fetch(self, job, part):
        expected = job.res.size
        if expected == 0:
            open(part, "wb").close()
            return hashlib.sha256().hexdigest(), 0
        try:
            start = os.path.getsize(part)
        except OSError:
            start = 0
        if start > expected:
            _discard(part)
            start = 0
        digest = hashlib.sha256()
        if start:
            with open(part, "rb") as fh:
                for block in iter(lambda: fh.read(CHUNK), b""):
                    digest.update(block)
        job.progress = start
        if start == expected:
            return digest.hexdigest(), start
        if not job.res.url:
            raise FetchError("expired", "no download link")
        response = self.fetcher.get(job.res.url, {"Range": "bytes=%d-" % start} if start else {})
        try:
            status = response.status
            if status == 206 and start:
                if not _range_ok(response.headers, start):
                    _discard(part)
                    raise FetchError("retry", "iCloud sent the wrong part of the file")
                mode = "ab"
            elif status in (200, 206):
                if start:
                    digest = hashlib.sha256()
                    start = 0
                    job.progress = 0
                mode = "wb"
            elif status in (401, 403):
                raise FetchError("auth", "iCloud refused the download (HTTP %d)" % status)
            elif status in (404, 410):
                raise FetchError("expired", "the download link expired (HTTP %d)" % status)
            elif status == 416:
                _discard(part)
                raise FetchError("retry", "iCloud did not accept the resume point")
            elif status in (429, 503):
                raise FetchError("throttle", "iCloud asked to slow down",
                                 retry_after=parse_retry_after(response.headers))
            else:
                raise FetchError("retry", "iCloud answered HTTP %d" % status)
            with open(part, mode) as fh:
                for chunk in response.iter_chunks(CHUNK):
                    if self.stop.is_set():
                        raise FetchError("stopped")
                    fh.write(chunk)
                    digest.update(chunk)
                    job.progress += len(chunk)
                    with self.received_lock:
                        self.received += len(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            response.close()
        got = os.path.getsize(part)
        if got < expected:
            raise FetchError("retry", "the download stopped early (%s of %s)" % (
                fmt_size(got), fmt_size(expected)))
        if got > expected:
            _discard(part)
            raise FetchError("retry", "iCloud sent more data than expected")
        return digest.hexdigest(), got


# --------------------------------------------------------------------------
# download: command
# --------------------------------------------------------------------------

NOT_ENOUGH_SPACE = """Not enough free space in {path}.
This download needs about {need} (with a small safety margin). The drive has {free} free.
Free some space or pick a bigger drive, then run the same command again."""


def _partial_bytes(root):
    total = 0
    folder = os.path.join(root, STATE_DIR, PARTIAL_DIR)
    try:
        for name in os.listdir(folder):
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    except OSError:
        pass
    return total


def _clean_partial(root):
    """Remove leftover partial downloads (our own temporary files) once nothing needs them."""
    folder = os.path.join(root, STATE_DIR, PARTIAL_DIR)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.endswith(".part"):
            _discard(os.path.join(folder, name))


def _write_last_run(root, lib, plan, args, status, failed=0):
    data = {
        "finished_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": status,
        "filters": {
            "limit": getattr(args, "limit", None),
            "since": str(args.since) if getattr(args, "since", None) else None,
            "until": str(args.until) if getattr(args, "until", None) else None,
            "no_edited": bool(getattr(args, "no_edited", False)),
        },
        "listing_complete": bool(lib.complete),
        "count_mismatch": bool(lib.count_mismatch),
        "problems": list(lib.problems),
        "icloud_counts": dict(("%s:%s" % (s.library, s.zone.get("zoneName")), s.counts) for s in lib.sources),
        "listed": dict(("%s:%s" % (s.library, s.zone.get("zoneName")), s.listed) for s in lib.sources),
        "items": len(lib.assets),
        "files_planned": plan.total_files if plan else 0,
        "files_failed": failed,
    }
    try:
        write_json(state_path(root, LAST_RUN_NAME), data)
    except OSError:
        pass


def _date_filter(since, until):
    if not since and not until:
        return None

    def keep(asset):
        if asset.created is None:
            return False
        day = asset.created.date()
        return (since is None or day >= since) and (until is None or day <= until)
    return keep


def run_download(cloud, args, out, retry_base=RETRY_BASE):
    root = os.path.abspath(os.path.expanduser(args.to))
    reason = synced_folder_reason(root)
    if reason and not getattr(args, "allow_synced_folder", False):
        out.line(SYNCED_FOLDER.format(path=root, reason=reason))
        return EXIT_FAIL
    os.makedirs(os.path.join(root, STATE_DIR), exist_ok=True)
    with DirLock(root):
        return _run_download_locked(cloud, args, out, root, retry_base)


def _run_download_locked(cloud, args, out, root, retry_base):
    started = time.monotonic()
    rows, bad = read_manifest(root)
    if bad:
        out.line("Note: %d lines of the manifest could not be read. They are kept as they are." % len(bad))
    try:
        lib = list_library(cloud.client, out, limit=getattr(args, "limit", None),
                           keep=_date_filter(getattr(args, "since", None), getattr(args, "until", None)))
    except ApprovalNeeded:
        out.end_status()
        out.line(paused_text(cloud))
        out.line("No files were downloaded in this run yet.")
        return EXIT_PAUSED
    albums_known = False
    if getattr(args, "limit", None) and not getattr(args, "no_albums", False):
        out.line("Album names are left out of a run with --limit, to keep it quick. "
                 "A full download adds them.")
    elif not getattr(args, "no_albums", False):
        names, members, warnings = cloud.albums(True, out)
        lib.album_names = names
        lib.warnings.extend(warnings)
        albums_known = not warnings
        for asset in lib.assets:
            asset.albums = sorted(set(members.get(asset.asset_id, [])))
    no_edited = bool(getattr(args, "no_edited", False))
    plan = build_plan(root, lib.assets, rows,
                      (lambda r: r.kind != "edited") if no_edited else None, albums_known)
    filtered = bool(getattr(args, "limit", None) or getattr(args, "since", None)
                    or getattr(args, "until", None) or no_edited)
    if no_edited:
        rows = [row for row in rows if not (row.get("source") == SOURCE and row.get("kind") == "edited"
                                            and row.get("status") != "done")]
    if lib.complete and not filtered and not lib.count_mismatch and not lib.problems:
        rows, removed = prune_rows(rows, plan.keys)
        if removed:
            out.line("%s planned files are no longer in iCloud and were taken off the list." % fmt_int(removed))
    write_manifest(root, rows, bad)
    to_get = len(plan.jobs)
    bytes_to_get = sum(job.res.size for job in plan.jobs)
    out.line("%s items, %s files (%s). %s already here, %s to download (%s)." % (
        fmt_int(len(lib.assets)), fmt_int(plan.total_files), fmt_size(plan.total_bytes),
        fmt_int(plan.done_files), fmt_int(to_get), fmt_size(bytes_to_get)))
    if plan.adopted:
        out.line("Found %s finished files from an earlier run and recorded them." % fmt_int(plan.adopted))
    for warning in lib.warnings:
        out.line("Note: " + warning)
    for problem in lib.problems:
        out.line("Problem: " + problem)
    downloader = None
    if plan.jobs:
        need = max(0, bytes_to_get - _partial_bytes(root))
        margin = max(1e9, 0.02 * need)
        free = shutil.disk_usage(root).free
        if need + margin > free:
            out.line(NOT_ENOUGH_SPACE.format(path=root, need=fmt_size(need + margin), free=fmt_size(free)))
            _write_last_run(root, lib, plan, args, "not-started")
            return EXIT_FAIL
        if cloud.fetcher is None and hasattr(cloud, "make_fetcher"):
            cloud.make_fetcher()
        downloader = Downloader(root, cloud, plan, rows, bad, args.workers, out, retry_base=retry_base)
        out.line("Downloading %d files at a time into %s" % (args.workers, root))
        out.line("You can stop with Ctrl+C. Run the same command again to continue.")
        downloader.run()
        rows = downloader.rows
        if downloader.interrupted:
            out.line("Stopped. %s of %s files are saved. Run the same command again to continue." % (
                fmt_int(downloader.files_done), fmt_int(plan.total_files)))
            _write_last_run(root, lib, plan, args, "stopped")
            return EXIT_STOPPED
        if downloader.stop_error is not None:
            exc = downloader.stop_error
            if isinstance(exc, ApprovalNeeded):
                out.line(paused_text(cloud))
                code = EXIT_PAUSED
            elif isinstance(exc, SessionExpired):
                out.line("Paused: the iCloud session ended. Nothing is lost.")
                out.line(LOGIN_NEEDED)
                code = EXIT_LOGIN
            elif isinstance(exc, AccessBlocked):
                out.line(ADP_MESSAGE)
                code = EXIT_BLOCKED
            else:
                out.line(str(exc))
                code = exc.exit_code
            out.line("%s of %s files are saved." % (fmt_int(downloader.files_done), fmt_int(plan.total_files)))
            _write_last_run(root, lib, plan, args, "paused")
            return code
    failed = [row for row in rows if row.get("source") == SOURCE
              and (row.get("asset_id"), row.get("variant")) in plan.keys and row.get("status") != "done"]
    _write_last_run(root, lib, plan, args, "complete" if not failed else "incomplete", len(failed))
    elapsed = time.monotonic() - started
    if downloader is not None:
        out.line("Downloaded %s files (%s) in %s." % (
            fmt_int(downloader.files_now), fmt_size(downloader.bytes_now), fmt_duration(elapsed)))
    if failed:
        out.line("%s files could not be downloaded:" % fmt_int(len(failed)))
        for row in failed[:50]:
            out.line("  %s: %s" % (row.get("path"), row.get("error") or "not downloaded"))
        if len(failed) > 50:
            out.line("  and %s more, listed in %s" % (fmt_int(len(failed) - 50), state_path(root, MANIFEST_NAME)))
        out.line("Run the same command again to retry them. Finished files are skipped.")
        return EXIT_FAIL
    if lib.problems:
        out.line("The files above are saved, but the library could not be read completely. "
                 "Run the same command again. If the numbers still differ, the problems are listed above.")
        return EXIT_FAIL
    if not filtered and lib.complete:
        _clean_partial(root)
    out.line("Done. All %s files for %s items are in %s" % (
        fmt_int(plan.total_files), fmt_int(len(lib.assets)), root))
    out.line("Next, check every file:  python3 scripts/icloud.py verify --to %s" % _quote(root))
    return EXIT_OK


def _quote(path):
    if os.name == "nt":
        # PowerShell and Git Bash both read double quotes and forward slashes; Git Bash drops
        # unquoted backslashes.
        return '"%s"' % path.replace("\\", "/")
    return '"%s"' % path if (" " in path or "'" in path) else path


def _exit_if_stuck(code):
    """After Ctrl+C a worker can sit in a slow network read. Do not wait for it:
    the manifest is already saved."""
    if any(t.name.startswith("homecloud-download") and t.is_alive() for t in threading.enumerate()):
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(code)


def cmd_download(args, out=None):
    out = out or OUT
    cloud = RealCloud(out, args.approval_wait)
    try:
        out.line("Signed in as %s. Reading your library. This only reads from iCloud." % mask_id(cloud.name))
        if cloud.adp:
            out.line("Advanced Data Protection is on. iCloud may ask you to tap Allow again about "
                     "every hour. Keep your iPhone nearby.")
        cloud.make_fetcher()
        code = run_download(cloud, args, out)
    finally:
        secure_session_dir()
    if code == EXIT_STOPPED:
        _exit_if_stuck(code)
    return code


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

class CheckProgress(object):
    def __init__(self, out, total_files, total_bytes):
        self.out = out
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.files = 0
        self.bytes = 0
        self.started = time.monotonic()
        self.ticker = Ticker(out)

    def add_bytes(self, count):
        self.bytes += count
        self.show()

    def show(self, force=False):
        if not self.ticker.due(force):
            return
        elapsed = time.monotonic() - self.started
        eta = None
        if elapsed > 5 and self.bytes:
            eta = max(0, self.total_bytes - self.bytes) / (self.bytes / elapsed)
        left = "done" if self.files >= self.total_files else fmt_eta(eta)
        self.out.status("Checking %s / %s files · %s · %s" % (
            fmt_int(self.files), fmt_int(self.total_files),
            fmt_pair(min(self.bytes, self.total_bytes), self.total_bytes), left))


def cmd_verify(args, out=None, ask=None, service_factory=None):
    out = out or OUT
    root = os.path.abspath(os.path.expanduser(args.to))
    if not os.path.isfile(state_path(root, MANIFEST_NAME)):
        out.line("No manifest found at %s" % state_path(root, MANIFEST_NAME))
        out.line("NOT VERIFIED: there is no list of files to check in this folder.")
        return EXIT_FAIL
    with DirLock(root, optional=True):
        code, final = _verify(root, args, out)
    offer = (code == EXIT_OK and not getattr(args, "keep_session", False)
             and getattr(args, "source", None) in (None, SOURCE)
             and download_was_complete(root) and saved_session_files())
    if not offer:
        out.line(final)
        return code
    if ask is None and _stdin_is_tty():
        ask = input
    if ask is None:
        # Nobody can answer here, for example when Claude Code runs verify. The key is
        # only needed for the move, so sign out now; --keep-session skips this. Keep
        # the result as the last line.
        out.line("Everything is here. Signing out of iCloud and deleting the session key "
                 "(use --keep-session to keep it).")
        if sign_out(out, service_factory) != EXIT_OK:
            out.line(NOT_SIGNED_OUT)
        out.line(final)
        return code
    out.line(final)
    out.line("")
    try:
        answer = ask(SIGN_OUT_QUESTION)
    except (EOFError, KeyboardInterrupt):
        out.line("")
        answer = "n"
    if (answer or "").strip().lower() in ("", "y", "yes"):
        if sign_out(out, service_factory) != EXIT_OK:
            out.line(NOT_SIGNED_OUT)
    else:
        out.line("The session key stays on this computer. To sign out later, run:  "
                 "python3 scripts/icloud.py logout")
    return code


def _stdin_is_tty():
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def download_was_complete(root):
    """True when the last download read the whole library and got every file."""
    last = read_json(state_path(root, LAST_RUN_NAME)) or {}
    filters = last.get("filters") or {}
    return bool(last.get("status") == "complete" and last.get("listing_complete")
                and not last.get("count_mismatch") and not any(filters.values()))


def _verify(root, args, out):
    rows, bad = read_manifest(root)
    source = getattr(args, "source", None)
    quick = bool(getattr(args, "quick", False))
    selected = [row for row in rows if not source or row.get("source") == source]
    if not selected:
        return EXIT_FAIL, "NOT VERIFIED: the manifest lists no files%s." % (" from " + source if source else "")
    expected_bytes = sum(row["size"] for row in selected
                         if isinstance(row.get("size"), int) and row["size"] > 0)
    progress = CheckProgress(out, len(selected), expected_bytes)
    missing, damaged = [], []
    kinds = {}
    ok_files = ok_bytes = 0
    for row in sorted(selected, key=lambda r: r.get("path") or ""):
        rel = row.get("path")
        size = row.get("size")
        kind = str(row.get("kind") or "other")
        tally = kinds.setdefault(kind, [0, 0, 0])
        tally[0] += 1
        progress.files += 1
        path = rel_to_abs(root, rel)
        if path is None:
            damaged.append((rel, "the manifest holds an unsafe path"))
            row["status"] = "failed"
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            damaged.append((rel, "the manifest holds no valid size"))
            continue
        try:
            actual = os.path.getsize(path) if os.path.isfile(path) else None
        except OSError:
            actual = None
        if actual is None:
            if row.get("status") == "failed":
                why = "download failed: %s" % (row.get("error") or "unknown error")
            elif row.get("status") == "missing":
                why = "not downloaded yet"
            else:
                why = "not on disk"
                row["status"] = "missing"
            missing.append((rel, why))
            continue
        if actual != size:
            damaged.append((rel, "size is %s bytes, expected %s" % (fmt_int(actual), fmt_int(size))))
            row["status"] = "failed"
            row["error"] = "size does not match"
            continue
        if not quick:
            try:
                digest = sha256_file(path, progress.add_bytes)
            except OSError as exc:
                damaged.append((rel, "could not read the file (%s)" % (exc.strerror or exc)))
                continue
            if row.get("sha256") and row["sha256"] != digest:
                damaged.append((rel, "checksum does not match"))
                row["status"] = "failed"
                row["error"] = "checksum does not match"
                continue
            row["sha256"] = digest
        else:
            progress.add_bytes(size)
        row["status"] = "done"
        row.pop("error", None)
        ok_files += 1
        ok_bytes += size
        tally[1] += 1
        tally[2] += size
        progress.show()
    progress.show(force=True)
    out.end_status()
    saved = True
    try:
        write_manifest(root, rows, bad)
    except OSError as exc:
        saved = False
        out.line("Note: could not save the checksums in the manifest (%s)." % (exc.strerror or exc))
    extra = []
    notes = []
    if not source or source == SOURCE:
        last = read_json(state_path(root, LAST_RUN_NAME)) or {}
        filters = last.get("filters") or {}
        used = [flag for flag, key in (("--limit", "limit"), ("--since", "since"),
                                       ("--until", "until"), ("--no-edited", "no_edited"))
                if filters.get(key)]
        if last.get("count_mismatch") or (last and not used and last.get("listing_complete") is False):
            extra.extend(last.get("problems") or ["The last download could not read the whole library."])
        if used:
            notes.append("The last download used %s, so this folder may hold only part of your "
                         "library. Run download without it to get everything." % " and ".join(used))
    report = ["Homecloud verify report, %s" % dt.datetime.now().isoformat(timespec="seconds"),
              "Folder: %s" % root, ""]
    out.line("")
    out.line("Checked %s files in %s%s" % (fmt_int(len(selected)), root,
                                           " (sizes only)" if quick else ""))
    for kind in sorted(kinds):
        total, good, nbytes = kinds[kind]
        line = "  %-11s %s of %s files OK, %s" % (kind, fmt_int(good), fmt_int(total), fmt_size(nbytes))
        out.line(line)
        report.append(line)
    for title, items in (("Missing", missing), ("Damaged", damaged)):
        if not items:
            continue
        out.line("%s (%s):" % (title, fmt_int(len(items))))
        report.append("%s (%d):" % (title, len(items)))
        for index, (rel, why) in enumerate(items):
            text = "  %s  (%s)" % (rel, why)
            report.append(text)
            if index < 1000:
                out.line(text)
        if len(items) > 1000:
            out.line("  and %s more, see the report file" % fmt_int(len(items) - 1000))
    for text in extra:
        out.line("Problem: " + text)
        report.append("Problem: " + text)
    for text in notes:
        out.line("Note: " + text)
        report.append("Note: " + text)
    report_path = state_path(root, REPORT_NAME)
    if saved:
        try:
            _write_atomic(report_path, lambda fh: fh.write("\n".join(report) + "\n"))
        except OSError:
            report_path = None
    else:
        report_path = None
    if missing or damaged or extra or bad:
        if bad:
            out.line("Problem: %d lines of the manifest could not be read." % len(bad))
        parts = []
        if missing:
            parts.append("%s missing" % fmt_int(len(missing)))
        if damaged:
            parts.append("%s damaged" % fmt_int(len(damaged)))
        if extra:
            parts.append("the last download did not read the whole library")
        if bad:
            parts.append("%d unreadable manifest lines" % len(bad))
        where = (" The list is above and in %s." % report_path) if report_path else " The list is above."
        return EXIT_FAIL, "NOT VERIFIED: %s of %s files are OK. %s.%s" % (
            fmt_int(ok_files), fmt_int(len(selected)), ", ".join(parts).capitalize(), where)
    return EXIT_OK, "VERIFIED %s of %s files (%s). Nothing missing." % (
        fmt_int(ok_files), fmt_int(len(selected)), fmt_size(ok_bytes))


# --------------------------------------------------------------------------
# logout
# --------------------------------------------------------------------------

def saved_session_files():
    folder = session_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names if os.path.isfile(os.path.join(folder, n)))


def forget_session():
    """Delete the saved iCloud session on this computer: the files directly in
    ~/.homecloud/session, then that folder. Nothing else, not even other files
    in ~/.homecloud."""
    folder = session_dir()
    if os.path.basename(folder) != "session" or os.path.dirname(folder) != homecloud_home():
        raise RuntimeError("Unexpected session folder: %s" % folder)
    removed = []
    for name in saved_session_files():
        try:
            os.remove(os.path.join(folder, name))
            removed.append(name)
        except FileNotFoundError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass
    return removed


def end_session_at_apple(service_factory=None):
    """Ask Apple to end each saved session without keeping this computer trusted.

    Returns "confirmed", "ended" (it had already ended), "unconfirmed" or
    "no_packages". Deletes nothing: forget_session does that.
    """
    if service_factory is None:
        try:
            if sys.version_info < (3, 10):
                raise ImportError("old python")
            from pyicloud import PyiCloudService as service_factory
        except Exception:
            return "no_packages"
    try:
        install_request_guard()
    except ImportError:
        pass
    quiet_library_logs()
    account = load_account() or {}
    results = []
    with allow_sign_out():
        for apple_id in session_candidates():
            china = bool(account.get("china_mainland")) and apple_id == account.get("apple_id")
            try:
                api = service_factory(apple_id, None, authenticate=False, **_service_kwargs(china))
                if not (api.get_auth_status() or {}).get("authenticated"):
                    results.append("ended")
                    continue
                answer = api.logout(keep_trusted=False, all_sessions=False, clear_local_session=False)
                confirmed = isinstance(answer, dict) and answer.get("remote_logout_confirmed")
                results.append("confirmed" if confirmed else "unconfirmed")
            except ReadOnlyViolation:
                raise
            except Exception:
                results.append("unconfirmed")
    if "unconfirmed" in results:
        return "unconfirmed"
    return "confirmed" if "confirmed" in results else "ended"


def venv_python():
    """The Python of the kit's virtual environment in the home folder, written as README.md's
    Do it by hand section writes it. Git Bash and PowerShell both read the Windows form."""
    if os.name == "nt":
        return '"$HOME/homecloud-venv/Scripts/python.exe"'
    return "~/homecloud-venv/bin/python"


def needs_packages(version, missing):
    """The message for login, scan and download under a Python without the packages."""
    windows = os.name == "nt"
    return NEEDS_PACKAGES.format(
        version=version, missing=missing, python=venv_python(),
        make='py -m venv "$HOME/homecloud-venv"' if windows else "python3 -m venv ~/homecloud-venv",
        plain="py" if windows else "python3", note="\n" + AMPERSAND_NOTE if windows else "")


def sign_out(out, service_factory=None):
    if not saved_session_files():
        out.line("No iCloud session is saved on this computer. There is nothing to sign out.")
        return EXIT_OK
    result = end_session_at_apple(service_factory)
    if result == "no_packages":
        out.line("This Python cannot talk to iCloud: it needs Python 3.10 or newer with the packages "
                 "in scripts/requirements.txt. Nothing was deleted.")
        out.line("Run logout with the Python of the kit's virtual environment, for example:")
        out.line("    %s scripts/icloud.py logout" % venv_python())
        if os.name == "nt":
            out.line(AMPERSAND_NOTE)
        return EXIT_FAIL
    folder = session_dir()
    removed = forget_session()
    if result == "confirmed":
        out.line("Signed out of iCloud. Apple ended the session, and the next sign-in on this "
                 "computer asks for a code again.")
    elif result == "ended":
        out.line("The iCloud session had already ended at Apple.")
    else:
        out.line("Apple did not confirm the sign-out, maybe because it could not be reached. "
                 "The session ends by itself after some time.")
    out.line("Deleted the session key in %s (%d %s). This computer can no longer open your "
             "iCloud account." % (folder, len(removed), "file" if len(removed) == 1 else "files"))
    out.line("To download again later, run login first.")
    return EXIT_OK


def cmd_logout(args, service_factory=None, out=None):
    out = out or OUT
    return sign_out(out, service_factory)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a whole number")
    if value < 1:
        raise argparse.ArgumentTypeError("expected 1 or more")
    return value


def _workers(text):
    value = _positive_int(text)
    if value > MAX_WORKERS:
        raise argparse.ArgumentTypeError("at most %d" % MAX_WORKERS)
    return value


def _date(text):
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date like 2024-05-31")


WORKERS_HELP = ("files downloaded at the same time (default %d, at most %d). Four fills most "
                "home connections and stays well below the rate at which iCloud starts to "
                "refuse requests." % (DEFAULT_WORKERS, MAX_WORKERS))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="icloud.py",
        description="Copy your whole iCloud Photos library to this computer. It only reads "
                    "your library: it never changes or deletes anything in iCloud. logout only "
                    "ends this computer's sign-in.",
        epilog="Exit codes: 0 done, 1 failed, 2 sign in again, 3 web access blocked, "
               "4 paused (run again), 130 stopped. Not made or endorsed by Apple.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="sign in to iCloud; run it in your own Terminal window",
                       description="Sign in to iCloud with your Apple Account, password and "
                                   "code. Reuses a saved session when it still works.")
    p.add_argument("--china-mainland", action="store_true",
                   help="your Apple Account is set to China mainland")
    p.add_argument("--approval-wait", type=_positive_int, default=5, metavar="MIN",
                   help="with Advanced Data Protection on, how long to wait for you to tap "
                        "Allow on your iPhone (default 5)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("scan", help="count your library; reads only",
                       description="Count the photos and videos in iCloud, with sizes per year. "
                                   "Reads only.")
    p.add_argument("--json", metavar="FILE", help="also write the numbers to FILE as JSON "
                                                  "(- prints JSON instead of the summary)")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("download", help="copy every photo and video into DIR",
                       description="Copy originals in full quality, Live Photo videos and "
                                   "edited versions into DIR/YYYY/MM. Run it again to continue "
                                   "after a stop; finished files are skipped.")
    p.add_argument("--to", required=True, metavar="DIR", help="folder for the photos")
    p.add_argument("--limit", type=_positive_int, metavar="N",
                   help="only the first N items, oldest first, to try it out")
    p.add_argument("--workers", type=_workers, default=DEFAULT_WORKERS, metavar="N", help=WORKERS_HELP)
    p.add_argument("--since", type=_date, metavar="YYYY-MM-DD", help="only items taken on or after this day")
    p.add_argument("--until", type=_date, metavar="YYYY-MM-DD", help="only items taken on or before this day")
    p.add_argument("--no-edited", action="store_true",
                   help="skip edited versions; originals and Live Photo videos still download")
    p.add_argument("--no-albums", action="store_true", help="do not read album names (starts faster)")
    p.add_argument("--approval-wait", type=_positive_int, default=APPROVAL_WAIT_MINUTES, metavar="MIN",
                   help="with Advanced Data Protection on, how long to wait for you to tap Allow "
                        "again when an approval runs out (default %d)" % APPROVAL_WAIT_MINUTES)
    p.add_argument("--allow-synced-folder", action="store_true",
                   help="download into a folder that syncs to a cloud service anyway")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("verify", help="check every file listed in DIR/.homecloud/manifest.jsonl",
                       description="Check that every file in the manifest exists with the right "
                                   "size and checksum. Works without internet.")
    p.add_argument("--to", required=True, metavar="DIR", help="the download folder, or a copy of it")
    p.add_argument("--quick", action="store_true", help="check sizes only, skip checksums")
    p.add_argument("--source", choices=("icloud-web", "mac-photos", "usb"),
                   help="check only files from this source")
    p.add_argument("--keep-session", action="store_true",
                   help="do not offer to sign out of iCloud when everything is here, for example "
                        "when you download again soon (Hybrid mode)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("logout", help="sign out of iCloud and delete the session key on this computer",
                       description="End this computer's iCloud session at Apple, without keeping "
                                   "the computer trusted, and delete ~/.homecloud/session. Your "
                                   "photos are not touched. Run login again before the next download.")
    p.set_defaults(func=cmd_logout)
    return parser


def _setup_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main(argv=None):
    _setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_FAIL
    if args.command in ("login", "scan", "download"):
        missing = ""
        if sys.version_info >= (3, 10):
            try:
                import pyicloud  # noqa: F401
                import requests  # noqa: F401
            except Exception:
                missing = ", without the packages"
        if sys.version_info < (3, 10) or missing:
            OUT.line(needs_packages("%d.%d" % sys.version_info[:2], missing))
            return EXIT_FAIL
    try:
        return args.func(args)
    except KeyboardInterrupt:
        OUT.line("")
        OUT.line("Stopped.")
        return EXIT_STOPPED
    except HomecloudError as exc:
        OUT.line(str(exc))
        return exc.exit_code
    except ReadOnlyViolation as exc:
        OUT.line("Stopped for safety: %s" % exc)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
