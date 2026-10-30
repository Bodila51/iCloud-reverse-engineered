#!/usr/bin/env python3
"""Offline vault: copy new photos and videos from an iPhone over a USB cable.

No app on the phone and no cloud. Once a month you plug the iPhone into your
computer and run:

    python scripts/vault/vault.py check --to DIR     (reads only, copies nothing)
    python scripts/vault/vault.py copy  --to DIR     (copies and verifies new files)
    python scripts/vault/vault.py verify --to DIR    (re-checks the copies, no phone needed)

It writes DIR/YYYY/MM/<file name> and adds one line per file to
DIR/.homecloud/manifest.jsonl with source "usb". Only files that are not in
the vault yet are copied. Each file is first copied under a temporary name
into DIR/.homecloud/tmp-usb and checked three ways: the size matches the
phone, the phone computes a SHA-1 of its file and it must match the copy on
disk, and the SHA-256 of the copy goes into the manifest. Only then is it
renamed into place, so a stopped run never leaves a half copied file in the
vault. A stopped file starts over on the next run. Only after all that does
it print "Safe to free up N photos on the phone".

It never deletes or changes anything on the phone. It opens files on the phone
for reading only. The one change on the phone is the trust entry the iPhone
makes itself when you tap Trust.

It uses pymobiledevice3 (https://github.com/doronz88/pymobiledevice3), an open
source tool by doronz88 and contributors, under the GPL 3.0 license. It is not
bundled with this kit. You install it yourself from PyPI, as below.

Setup on macOS (the system Python 3.9 works):

    python3 -m venv ~/homecloud-venv
    ~/homecloud-venv/bin/python -m pip install --upgrade pip
    ~/homecloud-venv/bin/python -m pip install -r scripts/vault/requirements.txt
    ~/homecloud-venv/bin/python scripts/vault/vault.py check --to DIR

Setup on Windows. Install Python from python.org. Install the "Apple Devices"
app from the Microsoft Store, or iTunes from apple.com. The pymobiledevice3
docs say Windows needs the Apple Mobile Device Service, which iTunes brings.
They prefer iTunes because the Store app does not find phones over Wi-Fi.
This script only uses the cable, where the Store app should be enough. If
the script cannot reach the phone with the Apple Devices app, install iTunes.
Then:

    py -m venv "$env:USERPROFILE\\homecloud-venv"
    & "$env:USERPROFILE\\homecloud-venv\\Scripts\\python.exe" -m pip install -r scripts\\vault\\requirements.txt
    & "$env:USERPROFILE\\homecloud-venv\\Scripts\\python.exe" scripts\\vault\\vault.py check --to DIR

The first time, unlock the iPhone. It asks "Trust This Computer?". Tap Trust and
enter your passcode. pymobiledevice3 waits for that answer and saves the
pairing on this computer for the next runs. On a Mac that already trusts the
phone (for example from Finder), that pairing is reused.

What you get over this route, and its limits:

* HEIC stays HEIC. The script copies the files as the phone stores them,
  usually .HEIC photos and HEVC .MOV videos, and the SHA-1 check proves each
  copy equals the stored file. The Settings > Photos > "Transfer to Mac or PC"
  setting converts files for imports in Photos, Image Capture and Windows. It
  does not apply to this route.
* A Live Photo is two files with the same number in the same folder, for
  example IMG_1234.HEIC and IMG_1234.MOV. Both are copied and both are needed.
  IMG numbers restart after 9999 and on a new phone, so the folder matters.
* Edits you made on the phone are kept apart from the original, under
  PhotoData/Mutations/.../Adjustments/FullSizeRender.*. Other import tools
  show them as IMG_E1234 files. The script copies an edit it finds in either
  place as <name>_edited.<ext>. The original is always copied.
* Optimize iPhone Storage: if iCloud Photos is on with this setting, the
  phone keeps only small copies of many photos and the full size originals
  stay in iCloud. A cable only sees what is stored on the phone. Apple's
  support article on moving photos to a PC says to download the originals to
  the iPhone first. The script reads the phone's photo list
  (PhotoData/Photos.sqlite), compares it with the files, and reports photos
  that are only small copies or only in iCloud. They are not counted as safe.
  Fix: Settings > [your name] > iCloud > Photos > "Download and Keep
  Originals" (needs free space on the phone), or use the iCloud download.
  If the phone does not let the script read its photo list, the script says
  so, copies every photo file it finds, and asks you to compare the count
  with the Photos app before you delete anything.
* If iCloud Photos is on, deleting a photo on the phone also deletes it from
  iCloud and your other devices. Use this vault with iCloud Photos turned off.
* Photos in Recently Deleted, shared albums and photos synced from a computer
  are skipped when the photo list is readable.

Exit code: 0 when everything on the phone is in the vault and verified, 1 when
something failed (nothing was marked safe), 2 when the copy worked but some
photos are not on the phone in full size.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import sys
import tempfile
import time

PYMOBILEDEVICE3_VERSION = "11.49.0"
SOURCE = "usb"
STATE_DIR = ".homecloud"
MANIFEST_NAME = "manifest.jsonl"
STATE_NAME = "usb-state.jsonl"
NOT_IN_VAULT_NAME = "usb-not-in-vault.txt"
TMP_NAME = "tmp-usb"
CHUNK = 4 * 1024 * 1024  # pymobiledevice3 AFC reads at most 4 MB per request
OP_TIMEOUT = 120.0  # seconds for one AFC request
HASH_TIMEOUT = 1800.0  # the phone hashes a whole file, big videos take a while
STAT_CONCURRENCY = 16
PAIR_TIMEOUT = 180.0
ROOTS = ("/DCIM", "/PhotoData/CPLAssets")
MUTATIONS_ROOT = "/PhotoData/Mutations"
DB_PATH = "/PhotoData/Photos.sqlite"
PHOTO_EXTS = {".heic", ".heif", ".hif", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".dng",
              ".webp", ".avif", ".bmp", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}
MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS
CORE_DATA_EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
SMALL_COPY_RATIO = 0.8
INSTALL_HELP = (
    "pymobiledevice3 is not installed for this Python. Set it up with:\n"
    "    python3 -m venv ~/homecloud-venv\n"
    "    ~/homecloud-venv/bin/python -m pip install -r scripts/vault/requirements.txt\n"
    "and run this script with ~/homecloud-venv/bin/python (on Windows, in PowerShell: & \"$env:USERPROFILE\\homecloud-venv\\Scripts\\python.exe\")."
)


class PhoneError(Exception):
    """A problem with the phone connection, explained in plain words."""


def say(msg=""):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------

class DeviceFile:
    __slots__ = ("path", "size", "mtime", "birth")

    def __init__(self, path, size, mtime, birth):
        self.path = path  # absolute path inside the phone's media area, posix style
        self.size = int(size)
        self.mtime = float(mtime)
        self.birth = float(birth)

    @property
    def name(self):
        return posixpath.basename(self.path)

    @property
    def ext(self):
        return posixpath.splitext(self.path)[1].lower()

    @property
    def raw_ext(self):
        """Extension as the phone spells it, used for names in the vault."""
        return posixpath.splitext(self.path)[1]

    def key(self, udid):
        """Identity used to spot files that are already in the vault."""
        return "%s|%s|%d|%d" % (udid, self.path, self.size, int(self.mtime))


class Asset:
    """One item in the phone's Photos app: a photo or video and its extra files."""

    def __init__(self, asset_id, created, favorite=False, hidden=False, albums=None,
                 is_video=False, original_name=None, from_db=False):
        self.asset_id = asset_id
        self.created = created  # aware datetime, local time of capture when known
        self.favorite = favorite
        self.hidden = hidden
        self.albums = list(albums or [])
        self.is_video = is_video
        self.original_name = original_name
        self.from_db = from_db
        self.files = []  # list of (DeviceFile, kind, target file name)

    def add(self, dfile, kind, name):
        self.files.append((dfile, kind, name))


class Plan:
    def __init__(self):
        self.assets = []
        self.not_available = []  # (asset_id, name, created, reason)
        self.trashed = 0
        self.outside = 0  # photo list items outside the camera roll (shared albums, synced from a computer)
        self.db_total = 0  # photo list items in the camera roll, not counting Recently Deleted
        self.cloud_files = 0  # files in PhotoData/CPLAssets, the folder iCloud Photos uses
        self.db_used = False
        self.db_note = ""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def hash_file(path):
    """Return (size, sha256 hex, sha1 bytes) of a local file."""
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            sha256.update(chunk)
            sha1.update(chunk)
    return size, sha256.hexdigest(), sha1.digest()


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                say("Warning: skipping a damaged line in %s" % path)
    return rows


def write_jsonl_atomic(path, rows):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def local_dt(timestamp):
    return dt.datetime.fromtimestamp(timestamp).astimezone()


def manifest_row(asset, name, rel_path, size, sha256, kind, status):
    return {
        "asset_id": asset.asset_id,
        "filename": name,
        "path": rel_path,
        "size": int(size),
        "sha256": sha256,
        "created": asset.created.isoformat() if asset.created else None,
        "kind": kind,
        "favorite": bool(asset.favorite),
        "hidden": bool(asset.hidden),
        "albums": list(asset.albums),
        "source": SOURCE,
        "status": status,
    }


def month_folder(created):
    return "%04d/%02d" % (created.year, created.month)


def safe_name(name):
    """Keep a file name from the phone usable on Windows and macOS."""
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "unnamed"


def pick_names(folder, names):
    """Names for a group of files that do not clash with files already in folder.

    A clash gets " (2)", " (3)" and so on, the same number for the whole group,
    so a Live Photo keeps matching names (IMG_0001 (2).HEIC and IMG_0001 (2).MOV).
    """
    try:
        taken = {n.lower() for n in os.listdir(folder)}
    except OSError:
        taken = set()
    n = 1
    while True:
        out = []
        for name in names:
            stem, ext = os.path.splitext(name)
            out.append(name if n == 1 else "%s (%d)%s" % (stem, n, ext))
        if not any(o.lower() in taken for o in out) and len({o.lower() for o in out}) == len(out):
            return out
        n += 1


# --------------------------------------------------------------------------
# The phone's photo list (PhotoData/Photos.sqlite), read from a copy
# --------------------------------------------------------------------------

class DbAsset:
    __slots__ = ("pk", "uuid", "directory", "filename", "created", "is_video", "trashed", "hidden",
                 "favorite", "original_name", "original_size", "albums")


def core_data_time(value, offset_seconds):
    if value is None:
        return None
    try:
        when = CORE_DATA_EPOCH + dt.timedelta(seconds=float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if offset_seconds is not None:
        try:
            return when.astimezone(dt.timezone(dt.timedelta(seconds=int(offset_seconds))))
        except (TypeError, ValueError, OverflowError):
            pass
    return when.astimezone()


def _columns(con, table):
    return {row[1] for row in con.execute("PRAGMA table_info(%s)" % table)}


def _album_titles(con, tables):
    """asset Z_PK -> sorted album titles, for plain user albums. Best effort."""
    if "ZGENERICALBUM" not in tables:
        return {}
    album_cols = _columns(con, "ZGENERICALBUM")
    if not {"Z_PK", "ZTITLE"} <= album_cols:
        return {}
    for table in sorted(tables):
        m = re.match(r"^Z_(\d+)ASSETS$", table)
        if not m:
            continue
        cols = _columns(con, table)
        album_col = "Z_%sALBUMS" % m.group(1)
        asset_cols = [c for c in cols if re.match(r"^Z_\d+ASSETS$", c)]
        if album_col not in cols or len(asset_cols) != 1:
            continue
        where = ["A.ZTITLE IS NOT NULL"]
        if "ZKIND" in album_cols:
            where.append("A.ZKIND = 2")
        if "ZTRASHEDSTATE" in album_cols:
            where.append("COALESCE(A.ZTRASHEDSTATE, 0) = 0")
        sql = "SELECT J.%s, A.ZTITLE FROM %s J JOIN ZGENERICALBUM A ON A.Z_PK = J.%s WHERE %s" % (
            asset_cols[0], table, album_col, " AND ".join(where))
        result = {}
        for asset_pk, title in con.execute(sql):
            result.setdefault(asset_pk, set()).add(title)
        return {k: sorted(v) for k, v in result.items()}
    return {}


def read_photos_db(path):
    """Parse a copy of the phone's Photos.sqlite. Returns a list of DbAsset."""
    con = sqlite3.connect(path)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        asset_table = "ZASSET" if "ZASSET" in tables else ("ZGENERICASSET" if "ZGENERICASSET" in tables else None)
        if not asset_table:
            raise ValueError("no asset table")
        cols = _columns(con, asset_table)
        if not {"Z_PK", "ZUUID", "ZDIRECTORY", "ZFILENAME", "ZDATECREATED", "ZKIND"} <= cols:
            raise ValueError("unknown asset table layout")

        def col(name):
            return "T.%s" % name if name in cols else "NULL"

        attr_join, attr_name, attr_size, attr_tz = "", "NULL", "NULL", "NULL"
        if "ZADDITIONALASSETATTRIBUTES" in tables:
            acols = _columns(con, "ZADDITIONALASSETATTRIBUTES")
            if "ZASSET" in acols:
                attr_join = "LEFT JOIN ZADDITIONALASSETATTRIBUTES X ON X.ZASSET = T.Z_PK"
                attr_name = "X.ZORIGINALFILENAME" if "ZORIGINALFILENAME" in acols else "NULL"
                attr_size = "X.ZORIGINALFILESIZE" if "ZORIGINALFILESIZE" in acols else "NULL"
                attr_tz = "X.ZTIMEZONEOFFSET" if "ZTIMEZONEOFFSET" in acols else "NULL"
        sql = ("SELECT T.Z_PK, T.ZUUID, T.ZDIRECTORY, T.ZFILENAME, T.ZDATECREATED, T.ZKIND, %s, %s, %s, "
               "%s, %s, %s FROM %s T %s" % (
                   col("ZTRASHEDSTATE"), col("ZHIDDEN"), col("ZFAVORITE"),
                   attr_name, attr_size, attr_tz, asset_table, attr_join))
        try:
            albums = _album_titles(con, tables)
        except sqlite3.Error:
            albums = {}
        out = []
        for row in con.execute(sql):
            a = DbAsset()
            (a.pk, a.uuid, a.directory, a.filename, created, kind, trashed, hidden, favorite,
             a.original_name, a.original_size, tz) = row
            if not a.uuid or not a.directory or not a.filename:
                continue
            a.created = core_data_time(created, tz)
            a.is_video = kind == 1
            a.trashed = bool(trashed)
            a.hidden = bool(hidden)
            a.favorite = bool(favorite)
            a.albums = albums.get(a.pk, [])
            out.append(a)
        return out
    finally:
        con.close()


def db_relative_dir(directory):
    """ZDIRECTORY as a path below the phone's media folder, which is the root the script sees."""
    d = "/" + directory.replace("\\", "/").strip("/")
    low = d.lower()
    for prefix in ("/private/var/mobile/media", "/var/mobile/media"):
        if low == prefix or low.startswith(prefix + "/"):
            d = d[len(prefix):]
            break
    return d.strip("/")


def in_camera_roll(asset):
    """Photos in DCIM, or in PhotoData/CPLAssets (originals iCloud Photos put on the phone).

    Shared albums, "Shared with You" and photos synced from a computer live in other folders.
    """
    d = db_relative_dir(asset.directory).upper() + "/"
    return d.startswith(("DCIM/", "CPLASSETS/", "PHOTODATA/CPLASSETS/"))


def db_candidates(asset):
    rel = db_relative_dir(asset.directory) + "/" + asset.filename
    if rel.upper().startswith("PHOTODATA/"):
        return ["/" + rel]
    return ["/" + rel, "/PhotoData/" + rel]


# --------------------------------------------------------------------------
# Planning: which files belong to which photo, and which are new
# --------------------------------------------------------------------------

def _stem_key(path):
    folder, name = posixpath.split(path)
    return (folder.lower(), posixpath.splitext(name)[0].lower())


def edits_by_owner(mutation_files):
    """FullSizeRender files under PhotoData/Mutations, keyed by their original's (folder, stem)."""
    owners = {}
    prefix = MUTATIONS_ROOT + "/"
    for f in mutation_files:
        if not f.path.startswith(prefix):
            continue
        parts = f.path[len(prefix):].split("/")
        # <dir...>/<original stem>/Adjustments/FullSizeRender.<ext>
        if len(parts) < 4 or parts[-2] != "Adjustments" or not parts[-1].lower().startswith("fullsizerender."):
            continue
        if f.ext not in MEDIA_EXTS:
            continue
        owner_dir = "/".join(parts[:-3])
        stem = parts[-3]
        for base in ("/" + owner_dir, "/PhotoData/" + owner_dir):
            owners.setdefault((base.lower(), stem.lower()), []).append(f)
    return owners


_CAMERA_EDIT_RE = re.compile(r"^IMG_E(\d+)$", re.IGNORECASE)


def camera_roll_edits(media, db_paths, by_stem):
    """IMG_E1234.JPG next to IMG_1234.HEIC: an edit, as other import tools name it.

    Returns {(folder, stem) of the original: [edit files]}, only when the original is in the same folder.
    """
    owners = {}
    for f in media:
        if f.path.lower() in db_paths:
            continue
        folder, name = posixpath.split(f.path)
        m = _CAMERA_EDIT_RE.match(posixpath.splitext(name)[0])
        if not m:
            continue
        key = (folder.lower(), "img_" + m.group(1))
        if key in by_stem:
            owners.setdefault(key, []).append(f)
    return owners


def build_plan(files, mutation_files, db_assets, udid):
    plan = Plan()
    media = [f for f in files if f.ext in MEDIA_EXTS]
    by_path = {f.path.lower(): f for f in media}
    plan.cloud_files = sum(1 for f in media if f.path.lower().startswith("/photodata/cplassets/"))
    by_stem = {}
    for f in media:
        by_stem.setdefault(_stem_key(f.path), []).append(f)
    used = set()
    db_paths = set()
    for a in db_assets or []:
        for cand in db_candidates(a):
            db_paths.add(cand.lower())
    edits = edits_by_owner(mutation_files)
    for key, found in camera_roll_edits(media, db_paths, by_stem).items():
        edits.setdefault(key, []).extend(found)
        used.update(f.path.lower() for f in found)

    def attach_extras(asset, primary, stem_name):
        key = _stem_key(primary.path)
        if not asset.is_video:
            for other in by_stem.get(key, []):
                low = other.path.lower()
                if other is primary or other.ext not in VIDEO_EXTS or low in used or low in db_paths:
                    continue
                used.add(low)
                asset.add(other, "live_video", stem_name + other.raw_ext)
        for edit in edits.get(key, []):
            asset.add(edit, "edited", "%s_edited%s" % (stem_name, edit.raw_ext))

    def claim_siblings(paths):
        """Files next to a skipped photo (its Live Photo video) are skipped with it."""
        for path in paths:
            for other in by_stem.get(_stem_key(path), []):
                if other.path.lower() not in db_paths:
                    used.add(other.path.lower())

    if db_assets is not None:
        plan.db_used = True
        for a in db_assets:
            if not in_camera_roll(a):
                plan.outside += 1
                continue
            if a.trashed:
                plan.trashed += 1
                for cand in db_candidates(a):
                    used.add(cand.lower())
                claim_siblings(db_candidates(a))
                continue
            plan.db_total += 1
            name = safe_name(a.original_name or a.filename)
            primary = None
            for cand in db_candidates(a):
                primary = by_path.get(cand.lower())
                if primary:
                    break
            if primary is None:
                plan.not_available.append((a.uuid, name, a.created, "only in iCloud, not on this phone"))
                claim_siblings(db_candidates(a))
                continue
            if a.original_size and primary.size < SMALL_COPY_RATIO * float(a.original_size):
                plan.not_available.append((a.uuid, name, a.created, "only a small copy is on this phone"))
                used.add(primary.path.lower())
                claim_siblings([primary.path])
                continue
            used.add(primary.path.lower())
            created = a.created or local_dt(primary.birth or primary.mtime)
            asset = Asset(a.uuid, created, a.favorite, a.hidden, a.albums, a.is_video, name, from_db=True)
            stem_name = os.path.splitext(name)[0]
            asset.add(primary, "video" if a.is_video else "photo", name)
            attach_extras(asset, primary, stem_name)
            plan.assets.append(asset)
        # leftover media in the camera roll that the photo list does not name
        leftovers = [f for f in media if f.path.lower() not in used and f.path.lower() not in db_paths]
    else:
        leftovers = list(media)

    groups = {}
    for f in leftovers:
        if f.path.lower() in used:
            continue
        groups.setdefault(_stem_key(f.path), []).append(f)
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda f: (f.ext in VIDEO_EXTS, f.path))
        primary = members[0]
        is_video = primary.ext in VIDEO_EXTS
        when = min(f.birth or f.mtime for f in members)
        asset_id = "usb:%s:%s:%d" % (udid[:12], primary.path, int(when))
        asset = Asset(asset_id, local_dt(primary.birth or primary.mtime), is_video=is_video,
                      original_name=primary.name)
        asset.add(primary, "video" if is_video else "photo", safe_name(primary.name))
        used.add(primary.path.lower())
        stem_name = os.path.splitext(safe_name(primary.name))[0]
        for other in members[1:]:
            used.add(other.path.lower())
            kind = "live_video" if (not is_video and other.ext in VIDEO_EXTS) else ("video" if other.ext in VIDEO_EXTS else "photo")
            asset.add(other, kind, safe_name(other.name))
        for edit in edits.get(key, []):
            asset.add(edit, "edited", "%s_edited%s" % (stem_name, edit.raw_ext))
        plan.assets.append(asset)
    return plan


# --------------------------------------------------------------------------
# Vault state on disk
# --------------------------------------------------------------------------

class Vault:
    def __init__(self, dest):
        self.dest = dest
        self.meta = os.path.join(dest, STATE_DIR)
        self.manifest_path = os.path.join(self.meta, MANIFEST_NAME)
        self.state_path = os.path.join(self.meta, STATE_NAME)
        self.tmp = os.path.join(self.meta, TMP_NAME)
        self.rows = read_jsonl(self.manifest_path)
        self.by_sha = {}
        for row in self.rows:
            if row.get("status") == "done" and row.get("sha256"):
                self.by_sha.setdefault(row["sha256"], row["path"])
        self.known = {}  # device key -> rel path
        for row in read_jsonl(self.state_path):
            if row.get("key") and row.get("path"):
                self.known[row["key"]] = row["path"]

    def has(self, key):
        rel = self.known.get(key)
        return bool(rel) and os.path.isfile(os.path.join(self.dest, rel))

    def prepare(self):
        os.makedirs(self.tmp, exist_ok=True)
        for name in os.listdir(self.tmp):
            try:
                os.unlink(os.path.join(self.tmp, name))
            except OSError:
                pass

    def append(self, manifest_rows, state_rows):
        os.makedirs(self.meta, exist_ok=True)
        if manifest_rows:
            with open(self.manifest_path, "a", encoding="utf-8") as fh:
                for row in manifest_rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.rows.extend(manifest_rows)
            for row in manifest_rows:
                if row.get("status") == "done":
                    self.by_sha.setdefault(row["sha256"], row["path"])
        if state_rows:
            with open(self.state_path, "a", encoding="utf-8") as fh:
                for row in state_rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            for row in state_rows:
                self.known[row["key"]] = row["path"]

    def finish(self, open_rows, phone_asset_ids):
        """Rewrite the manifest once: drop this phone's old missing/failed lines, add the current ones."""
        rows = read_jsonl(self.manifest_path)
        kept = [r for r in rows if not (r.get("source") == SOURCE and r.get("status") in ("missing", "failed")
                                       and r.get("asset_id") in phone_asset_ids)]
        write_jsonl_atomic(self.manifest_path, kept + open_rows)
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Phone access (pymobiledevice3), read-only
# --------------------------------------------------------------------------

class ReadOnlyAfc:
    """The only file operations this script sends to the phone. None of them can change it."""

    def __init__(self, afc):
        self._afc = afc
        self.hash_supported = True

    async def _call(self, coro, timeout=OP_TIMEOUT):
        try:
            return await asyncio.wait_for(coro, timeout)
        except asyncio.TimeoutError:
            raise PhoneError("The iPhone stopped answering. Unplug it, plug it back in, and run again.")

    async def listdir(self, path):
        return await self._call(self._afc.listdir(path))

    async def stat(self, path):
        return await self._call(self._afc.stat(path))

    async def open_read(self, path):
        return await self._call(self._afc.fopen(path, "r"))

    async def read(self, handle, size):
        return await self._call(self._afc.fread(handle, size))

    async def close(self, handle):
        return await self._call(self._afc.fclose(handle))

    async def sha1(self, path):
        """SHA-1 the phone computes itself, or None if this phone cannot do it."""
        if not self.hash_supported:
            return None
        try:
            return await self._call(self._afc.get_file_hash(path), HASH_TIMEOUT)
        except PhoneError:
            raise
        except Exception as exc:
            if is_not_found(exc):
                raise
            self.hash_supported = False
            say("This phone cannot compute file hashes. The script reads each file twice instead.")
            return None


def is_not_found(exc):
    return type(exc).__name__ in ("AfcFileNotFoundError", "FileNotFoundError")


async def list_tree(afc, root):
    """All regular files under root, with sizes and times. Missing root gives []."""
    sem = asyncio.Semaphore(STAT_CONCURRENCY)
    files = []
    pending = [root]

    async def stat_one(path):
        async with sem:
            try:
                return path, await afc.stat(path)
            except PhoneError:
                raise
            except Exception as exc:
                return path, exc

    while pending:
        folder = pending.pop()
        try:
            names = await afc.listdir(folder)
        except PhoneError:
            raise
        except Exception as exc:
            if folder == root == "/DCIM":
                raise PhoneError("Could not read the camera roll folder on the phone (%s).\n"
                                 "Unlock the iPhone and run again." % exc)
            if folder != root and not is_not_found(exc):
                say("Warning: could not list %s on the phone (%s)" % (folder, exc))
            continue
        paths = [posixpath.join(folder, n) for n in names if n not in (".", "..", "")]
        for path, info in await asyncio.gather(*(stat_one(p) for p in paths)):
            if isinstance(info, Exception):
                say("Warning: could not read details of %s (%s)" % (path, info))
                continue
            kind = info.get("st_ifmt")
            if kind == "S_IFDIR":
                pending.append(path)
            elif kind == "S_IFREG":
                files.append(DeviceFile(path, info.get("st_size", 0), _ts(info.get("st_mtime")),
                                        _ts(info.get("st_birthtime"))))
    files.sort(key=lambda f: f.path)
    return files


def _ts(value):
    if value is None:
        return 0.0
    if isinstance(value, dt.datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def pull_to_file(afc, dfile, local_path):
    """Copy one phone file to local_path. Returns the number of bytes written."""
    handle = await afc.open_read(dfile.path)
    written = 0
    try:
        with open(local_path, "wb") as out:
            remaining = dfile.size
            while remaining > 0:
                chunk = await afc.read(handle, min(CHUNK, remaining))
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
            out.flush()
            os.fsync(out.fileno())
    finally:
        await afc.close(handle)
    return written


async def phone_sha256(afc, dfile):
    """Second read from the phone, for phones that cannot hash files themselves."""
    handle = await afc.open_read(dfile.path)
    digest = hashlib.sha256()
    try:
        remaining = dfile.size
        while remaining > 0:
            chunk = await afc.read(handle, min(CHUNK, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    finally:
        await afc.close(handle)
    return digest.hexdigest()


async def copy_and_verify(afc, dfile, tmp_path):
    """Copy a file and check it. Returns (sha256, problem). problem is None when it is verified."""
    written = await pull_to_file(afc, dfile, tmp_path)
    size, sha256, sha1 = hash_file(tmp_path)  # read back from the disk, not from memory
    if written != dfile.size or size != dfile.size:
        return sha256, "size on disk %d, on the phone %d" % (size, dfile.size)
    phone_sha1 = await afc.sha1(dfile.path)
    if phone_sha1 is not None:
        if bytes(phone_sha1) != sha1:
            return sha256, "content differs from the phone (SHA-1 check)"
    elif await phone_sha256(afc, dfile) != sha256:
        return sha256, "content differs from the phone (second read)"
    return sha256, None


async def load_db(afc, workdir):
    """Copy Photos.sqlite (and its -wal) from the phone and read it. Returns (assets or None, note)."""
    try:
        main = await afc.stat(DB_PATH)
    except PhoneError:
        raise
    except Exception as exc:
        return None, "could not open the photo list on the phone (%s)" % exc
    local = os.path.join(workdir, "Photos.sqlite")
    try:
        for suffix in ("", "-wal"):
            remote = DB_PATH + suffix
            try:
                info = main if not suffix else await afc.stat(remote)
            except PhoneError:
                raise
            except Exception:
                continue
            dfile = DeviceFile(remote, info.get("st_size", 0), 0, 0)
            await pull_to_file(afc, dfile, local + suffix)
        return read_photos_db(local), ""
    except PhoneError:
        raise
    except Exception as exc:
        return None, "could not read the photo list on the phone (%s)" % exc


def current_platform():
    return sys.platform


def usbmux_help():
    """What to do when the USB service that talks to iPhones is not there."""
    platform = current_platform()
    if platform.startswith("win"):
        return ("Windows needs the Apple Mobile Device Service to reach an iPhone over USB, and it is not running.\n"
                "Install the Apple Devices app from the Microsoft Store, or iTunes from apple.com.\n"
                "Open it once and check that it shows your iPhone, then run this again.\n"
                "If it is already installed, restart the computer and try again.")
    if platform == "darwin":
        return ("Could not reach usbmuxd, the macOS service that talks to iPhones over USB.\n"
                "Restart the Mac and try again.")
    return ("Could not reach usbmuxd, the service that talks to iPhones over USB.\n"
            "Install usbmuxd with your system's package manager and try again.")


def no_phone_help():
    text = "No iPhone found on a USB cable. Plug it in, unlock it, and run again."
    if current_platform().startswith("win"):
        text += "\nOn Windows, open the Apple Devices app (or iTunes) and check that it shows your iPhone."
    return text


def installed_version(package):
    try:
        from importlib import metadata
        return metadata.version(package)
    except Exception:
        return None


class PhoneSession:
    """Connect over USB, pair if needed, and open the file service (AFC)."""

    def __init__(self, udid=None, pair_timeout=PAIR_TIMEOUT):
        self.udid = udid
        self.pair_timeout = pair_timeout
        self.lockdown = None
        self.service = None
        self.info = {}

    async def __aenter__(self):
        try:
            from pymobiledevice3 import exceptions as pexc
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.afc import AfcService
        except ImportError:
            raise PhoneError(INSTALL_HELP)
        version = installed_version("pymobiledevice3")
        if version and version != PYMOBILEDEVICE3_VERSION:
            say("Note: this script was checked with pymobiledevice3 %s, and you have %s. If something fails, "
                "install the checked version with:\n    python -m pip install \"pymobiledevice3==%s\""
                % (PYMOBILEDEVICE3_VERSION, version, PYMOBILEDEVICE3_VERSION))
        say("Unlock your iPhone and keep it unlocked. If it asks \"Trust This Computer?\", tap Trust "
            "and enter your passcode.")
        deadline = time.monotonic() + self.pair_timeout
        told_unlock = False
        while True:
            try:
                self.lockdown = await create_using_usbmux(
                    serial=self.udid, connection_type="USB", autopair=True,
                    pair_timeout=max(5.0, deadline - time.monotonic()))
                break
            except (pexc.PasswordRequiredError, pexc.PasscodeRequiredError):
                if time.monotonic() > deadline:
                    raise PhoneError("The iPhone stayed locked. Unlock it and run again.")
                if not told_unlock:
                    say("Waiting for you to unlock the iPhone...")
                    told_unlock = True
                await asyncio.sleep(2)
            except pexc.PairingDialogResponsePendingError:
                raise PhoneError("Nobody answered \"Trust This Computer?\" on the iPhone. Run again and tap Trust.")
            except pexc.UserDeniedPairingError:
                raise PhoneError("The iPhone answered Don't Trust. Unplug it, plug it back in, and tap Trust.\n"
                                 "If the question does not come back, see Apple's support article about the "
                                 "\"Trust This Computer\" alert.")
            except pexc.ConnectionFailedToUsbmuxdError:
                raise PhoneError(usbmux_help())
            except (pexc.NoDeviceConnectedError, pexc.DeviceNotFoundError):
                raise PhoneError(no_phone_help())
            except (pexc.FatalPairingError, pexc.InvalidHostIDError, pexc.PairingError) as exc:
                raise PhoneError("Pairing with the iPhone failed (%s). Unplug it, plug it back in, and tap Trust."
                                 % exc)
        values = getattr(self.lockdown, "all_values", None) or {}
        self.info = {
            "udid": getattr(self.lockdown, "udid", None) or values.get("UniqueDeviceID") or self.udid or "unknown",
            "name": values.get("DeviceName", "iPhone"),
            "ios": values.get("ProductVersion", "?"),
        }
        self.service = AfcService(lockdown=self.lockdown)
        await self.service.__aenter__()
        return ReadOnlyAfc(self.service)

    async def __aexit__(self, exc_type, exc, tb):
        if self.service is not None:
            try:
                await self.service.__aexit__(exc_type, exc, tb)
            except Exception:
                pass
        if self.lockdown is not None:
            try:
                await self.lockdown.close()
            except Exception:
                pass
        return False


def open_phone(udid=None):
    """Seam for tests: returns an async context manager that yields a ReadOnlyAfc."""
    return PhoneSession(udid)


# --------------------------------------------------------------------------
# The work
# --------------------------------------------------------------------------

async def scan_phone(afc, udid, workdir):
    say("Reading the phone's photo list...")
    db_assets, note = await load_db(afc, workdir)
    say("Listing photo files on the phone. This takes a minute for a big library...")
    files = []
    for root in ROOTS:
        files.extend(await list_tree(afc, root))
    mutations = await list_tree(afc, MUTATIONS_ROOT)
    plan = build_plan(files, mutations, db_assets, udid)
    plan.db_note = note
    return plan


def summarize(plan, vault, udid):
    total_files = sum(len(a.files) for a in plan.assets)
    new_files = sum(1 for a in plan.assets for f, _k, _n in a.files if not (vault and vault.has(f.key(udid))))
    return total_files, new_files


def print_scan(info, plan, vault):
    say("Phone: %s, iOS %s" % (info.get("name"), info.get("ios")))
    total_files, new_files = summarize(plan, vault, info["udid"])
    say("Photos and videos on the phone that can be copied: %d (%d files)" % (len(plan.assets), total_files))
    if vault is not None:
        say("Files not in the vault yet: %d" % new_files)
    if plan.trashed:
        say("Skipped, in Recently Deleted: %d" % plan.trashed)
    if plan.outside:
        say("Skipped, not in the camera roll (shared albums, photos synced from a computer): %d" % plan.outside)
    if not plan.db_used:
        say("Note: %s." % (plan.db_note or "the phone's photo list was not read"))
        say("The script copies every photo file it finds, but it cannot tell which photos are only in "
            "iCloud or only small copies.")
        if plan.cloud_files:
            say("Files in PhotoData/CPLAssets, the folder iCloud Photos uses: %d. So iCloud Photos may be on."
                % plan.cloud_files)
            say("With Optimize iPhone Storage, many full size photos are then not on the phone at all.")
    if plan.not_available:
        say("Not on this phone in full size: %d of %d" % (len(plan.not_available), plan.db_total))
        say("This happens when iCloud Photos is on with Optimize iPhone Storage, or while originals are still "
            "downloading. These stay out of the vault.")
        say("To get them: Settings > [your name] > iCloud > Photos > \"Download and Keep Originals\", then run "
            "again. Or use the iCloud download.")


async def run_check(args):
    vault = Vault(os.path.abspath(args.to)) if args.to else None
    workdir = tempfile.mkdtemp(prefix="homecloud-vault-")
    try:
        session = open_phone(args.udid)
        async with session as afc:
            plan = await scan_phone(afc, session.info["udid"], workdir)
            print_scan(session.info, plan, vault)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if args.to:
        target = os.path.abspath(args.to)
        probe = target
        while not os.path.exists(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
        say("Free space at %s: %.1f GB" % (probe, shutil.disk_usage(probe).free / 1e9))
    return 2 if plan.not_available else 0


async def run_copy(args):
    dest = os.path.abspath(args.to)
    vault = Vault(dest)
    vault.prepare()
    workdir = tempfile.mkdtemp(prefix="homecloud-vault-")
    session = open_phone(args.udid)
    copied = deduped = failed = 0
    problems = []
    open_rows = []
    try:
        async with session as afc:
            udid = session.info["udid"]
            plan = await scan_phone(afc, udid, workdir)
            print_scan(session.info, plan, vault)
            todo = select_todo(plan, vault, udid, args.limit)
            names_for = {}
            say("Copying %d new files..." % len(todo))
            done_bytes = 0
            total_bytes = sum(f.size for _a, f, _k, _n in todo) or 1
            for index, (asset, dfile, kind, name) in enumerate(todo, 1):
                tmp_path = os.path.join(vault.tmp, "%06d%s" % (index, dfile.ext))
                try:
                    sha256, problem = await copy_and_verify(afc, dfile, tmp_path)
                except PhoneError:
                    raise
                except Exception as exc:
                    sha256, problem = None, "could not copy (%s)" % exc
                if problem:
                    failed += 1
                    problems.append((dfile.path, problem))
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    open_rows.append(manifest_row(asset, name, "%s/%s" % (month_folder(asset.created), name),
                                                  dfile.size, None, kind, "failed"))
                    continue
                existing = vault.by_sha.get(sha256)
                if existing and os.path.isfile(os.path.join(dest, existing)):
                    os.unlink(tmp_path)
                    vault.append([], [{"key": dfile.key(udid), "path": existing, "sha256": sha256}])
                    deduped += 1
                else:
                    rel = place(dest, asset, name, tmp_path, names_for, todo)
                    row = manifest_row(asset, name, rel, dfile.size, sha256, kind, "done")
                    vault.append([row], [{"key": dfile.key(udid), "path": rel, "sha256": sha256}])
                    copied += 1
                done_bytes += dfile.size
                if index % 25 == 0 or index == len(todo):
                    say("  %d of %d files (%d%%)" % (index, len(todo), 100 * done_bytes // total_bytes))
            for uuid, name, created, _reason in plan.not_available:
                missing_asset = Asset(uuid, created)
                folder = month_folder(created) if created else "unknown"
                open_rows.append(manifest_row(missing_asset, name, "%s/%s" % (folder, name), 0, None,
                                              "photo", "missing"))
            vault.finish(open_rows, {a.asset_id for a in plan.assets} | {n[0] for n in plan.not_available})
            write_not_in_vault(vault, plan)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return report(plan, vault, udid, copied, deduped, failed, problems, len(todo), args.limit)


def select_todo(plan, vault, udid, limit):
    """New files to copy. With a limit, whole photos only, so a Live Photo is never split."""
    todo = []
    for asset in plan.assets:
        new = [(asset, f, k, n) for f, k, n in asset.files if not vault.has(f.key(udid))]
        if not new:
            continue
        if limit and todo and len(todo) + len(new) > limit:
            break
        todo.extend(new)
    return todo


def place(dest, asset, name, tmp_path, names_for, todo):
    """Move a verified file from the temp folder to DIR/YYYY/MM.

    The names for all new files of one photo are chosen together, the first time
    one of them is placed, so a Live Photo keeps matching names.
    """
    folder_rel = month_folder(asset.created)
    folder = os.path.join(dest, *folder_rel.split("/"))
    os.makedirs(folder, exist_ok=True)
    key = id(asset)
    if key not in names_for:
        group = [n for a, _f, _k, n in todo if a is asset]
        names_for[key] = dict(zip(group, pick_names(folder, group)))
    final = names_for[key].get(name) or pick_names(folder, [name])[0]
    target = os.path.join(folder, final)
    if os.path.exists(target):
        final = pick_names(folder, [name])[0]
        target = os.path.join(folder, final)
    os.replace(tmp_path, target)
    return folder_rel + "/" + final


def write_not_in_vault(vault, plan):
    path = os.path.join(vault.meta, NOT_IN_VAULT_NAME)
    if not plan.not_available:
        if os.path.exists(path):
            os.unlink(path)
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Photos on the phone that are NOT in the vault. Do not delete these from the phone.\n")
        for _uuid, name, created, reason in sorted(plan.not_available, key=lambda r: (str(r[2]), r[1])):
            fh.write("%s  %s  (%s)\n" % (created.strftime("%Y-%m-%d %H:%M") if created else "unknown date",
                                         name, reason))


def report(plan, vault, udid, copied, deduped, failed, problems, attempted, limit):
    say("")
    say("New files copied and verified: %d of %d" % (copied + deduped, attempted))
    if deduped:
        say("Of those, %d were already in the vault under another name and were not stored twice." % deduped)
    for path, problem in problems[:20]:
        say("Problem: %s: %s" % (path, problem))
    if failed:
        say("Result: %d files failed. Nothing is marked safe. Run the same command again." % failed)
        return 1
    safe = sum(1 for a in plan.assets if all(vault.has(f.key(udid)) for f, _k, _n in a.files))
    missing_now = len(plan.assets) - safe
    if limit and missing_now:
        say("Test run with --limit: %d photos are in the vault, %d still need copying." % (safe, missing_now))
        return 0
    if missing_now:
        say("Result: %d photos are not fully copied yet. Run the same command again." % missing_now)
        return 1
    say("")
    say("Safe to free up %d photos on the phone." % safe)
    say("Before you delete anything: make a second copy of %s on another drive." % vault.dest)
    say("If iCloud Photos is on, deleting on the phone also deletes in iCloud and on your other devices.")
    if plan.not_available:
        say("Warning: %d photos on the phone are NOT in the vault (only small copies or only in iCloud)."
            % len(plan.not_available))
        say("Do not delete those. The list is in %s" % os.path.join(vault.meta, NOT_IN_VAULT_NAME))
        return 2
    if not plan.db_used:
        videos = sum(1 for a in plan.assets if a.is_video)
        say("Check before you delete: the vault has %d photos and %d videos from this phone."
            % (len(plan.assets) - videos, videos))
        say("On the iPhone, open Photos and scroll to the end of the library to see its count.")
        say("If the phone shows more, the rest are only in iCloud or only small copies. Do not delete those.")
    return 0


def run_verify(args):
    dest = os.path.abspath(args.to)
    rows = [r for r in read_jsonl(os.path.join(dest, STATE_DIR, MANIFEST_NAME)) if r.get("source") == SOURCE]
    done = [r for r in rows if r.get("status") == "done"]
    if not rows:
        say("No USB vault files in %s yet." % dest)
        return 1
    bad = []
    for i, row in enumerate(done, 1):
        path = os.path.join(dest, *row["path"].split("/"))
        try:
            size, sha256, _sha1 = hash_file(path)
            ok = size == row["size"] and sha256 == row["sha256"]
        except OSError:
            ok = False
        if not ok:
            bad.append(row["path"])
        if i % 1000 == 0:
            say("Checked %d of %d..." % (i, len(done)))
    say("Files verified: %d of %d" % (len(done) - len(bad), len(done)))
    for path in bad[:20]:
        say("Changed or missing on disk: %s" % path)
    open_count = len(rows) - len(done)
    if open_count:
        say("Lines for photos that are not in the vault (missing or failed): %d" % open_count)
    return 1 if bad else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="vault.py",
        description=("Offline vault: copy new photos and videos from an iPhone on a USB cable into "
                     "DIR/YYYY/MM, verify size and checksums, and record them in DIR/.homecloud/manifest.jsonl. "
                     "Never deletes or changes anything on the phone. Needs pymobiledevice3 %s."
                     % PYMOBILEDEVICE3_VERSION),
    )
    sub = parser.add_subparsers(dest="command")
    p_check = sub.add_parser("check", help="connect and count photos, copies nothing")
    p_check.add_argument("--to", metavar="DIR", help="also count files that are not in this vault yet")
    p_check.add_argument("--udid", help="which iPhone, when more than one is plugged in")
    p_copy = sub.add_parser("copy", help="copy and verify new photos and videos into DIR")
    p_copy.add_argument("--to", metavar="DIR", required=True, help="vault folder")
    p_copy.add_argument("--limit", type=int, default=0, metavar="N", help="copy at most N new files (test run)")
    p_copy.add_argument("--udid", help="which iPhone, when more than one is plugged in")
    p_verify = sub.add_parser("verify", help="re-check sizes and SHA-256 of the vault files (no phone needed)")
    p_verify.add_argument("--to", metavar="DIR", required=True, help="vault folder")
    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify":
        return run_verify(args)
    if args.command not in ("check", "copy"):
        parser.print_help()
        return 1
    try:
        return asyncio.run(run_check(args) if args.command == "check" else run_copy(args))
    except PhoneError as exc:
        say(str(exc))
        say("Nothing on the phone was changed.")
        return 1
    except KeyboardInterrupt:
        say("Stopped. Nothing on the phone was changed. Run the same command again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
