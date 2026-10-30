#!/usr/bin/env python3
"""Copy every photo and video from the Photos app on a Mac into a Homecloud folder.

Use this if your Mac already syncs iCloud Photos. It reads the Photos library
on this Mac and writes:

    DIR/YYYY/MM/<original file name>       photos and videos, by date taken
    DIR/YYYY/MM/<name>_edited.<ext>        your edited versions
    DIR/YYYY/MM/<name>.mov                 the video part of each Live Photo
    DIR/YYYY/MM/<file>.xmp                 a metadata file next to each photo
    DIR/.homecloud/manifest.jsonl          one line per file (source "mac-photos")

Hidden photos are included. Favorites get a 5 star rating in the .xmp file.
Album names go into the .xmp file as keywords and into the manifest. Each
photo is stored once, even when it is in many albums. Photos in Recently
Deleted and photos from shared albums are left out.

Commands (Python 3.9 or newer, the Python that comes with macOS works):

    python3 scripts/mac/export_photos.py check  [--to DIR]
    python3 scripts/mac/export_photos.py export --to DIR [--limit N]
    python3 scripts/mac/export_photos.py verify --to DIR

Step 1. Install osxphotos, the open source tool that reads the Photos library.
It needs Python 3.10 or newer, so this script runs it as a separate program.
Install it with uv, as the osxphotos README recommends, pinned to the version
this script was checked against:

    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv tool install --python 3.13 "osxphotos==0.77.2"

Step 2. Get the full size originals onto this Mac. Open Photos, choose
Photos > Settings (Preferences on older macOS) > iCloud, and select
"Download Originals to this Mac". Leave Photos open and the Mac awake until
the download finishes. Run "check" to see how many originals are still only
in iCloud. Your Mac needs enough free space for the whole library.

Why this script waits for "Download Originals" instead of using osxphotos
--download-missing. That option makes osxphotos drive the Photos app with
AppleScript, one photo at a time, to fetch each missing original. The
osxphotos documentation says it is slow, needs an internet connection, that
Photos' AppleScript interface can crash Photos, and that it does not fetch all
burst images (only the main one). A file fetched that way also has no copy in
the library to compare against. "Download Originals" is Apple's own sync. It
fills the library once, keeps it filled, and then this script can check every
exported file byte for byte against the original in the library. You can still
pass --download-missing for a few stubborn leftovers, at your own choice.

Step 3. Run "export". It is safe to stop and run again. osxphotos remembers
what it already exported (DIR/.osxphotos_export.db) and only copies new or
changed photos. Every run then checks every file: the size and SHA-256 of the
copy must match the original in the Photos library.

Read-only toward Photos: osxphotos reads a temporary copy of the Photos
database and copies files out of the library. This script never passes the
osxphotos options that change the library (the --add-*-to-album options) and
never changes your iCloud data. The only optional exception is
--download-missing, which asks Photos itself to download originals.

Exit code: 0 when every file is copied and verified, 1 when something failed,
2 when some originals are still only in iCloud. Run again after they download.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

OSXPHOTOS_VERSION = "0.77.2"
SOURCE = "mac-photos"
STAGING_ENV = "HOMECLOUD_OSXPHOTOS_STAGING"
EDITED_SUFFIX = "_edited"
HOOK_FUNCTION = "osxphotos_post_function"
STATE_DIR = ".homecloud"
MANIFEST_NAME = "manifest.jsonl"
STATE_NAME = "mac-photos-state.json"
STAGING_NAME = "mac-photos-staging.jsonl"
REPORT_NAME = "mac-photos-report.json"
SIDECAR_EXTS = {".xmp", ".json", ".aae"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".m2ts", ".mpg", ".mpeg", ".mkv", ".webm"}
_COLLISION_RE = re.compile(r" \(\d+\)$")
INSTALL_HELP = (
    "Install it with:\n"
    "    curl -LsSf https://astral.sh/uv/install.sh | sh\n"
    '    uv tool install --python 3.13 "osxphotos==' + OSXPHOTOS_VERSION + '"\n'
    "Then open a new Terminal window and run this command again."
)
DOWNLOAD_ORIGINALS_HELP = (
    "To get them: open Photos, choose Photos > Settings (Preferences on older macOS) > iCloud,\n"
    'and select "Download Originals to this Mac". Keep Photos open and the Mac awake.\n'
    "Run the check command again later to watch the number go down."
)


# --------------------------------------------------------------------------
# osxphotos post-function. osxphotos imports this file and calls the function
# once per photo, right after it exports that photo. It must never raise,
# because an exception aborts the whole osxphotos export.
# --------------------------------------------------------------------------

def _as_str_list(values):
    return [str(v) for v in (values or [])]


def _photo_sources(photo):
    """Files of this photo inside the Photos library, with their sizes."""
    sources = []
    for role in ("path", "path_edited", "path_live_photo", "path_edited_live_photo", "path_raw"):
        try:
            path = getattr(photo, role)
        except Exception:
            path = None
        if path and os.path.isfile(path):
            try:
                sources.append({"role": role, "path": str(path), "size": os.path.getsize(path)})
            except OSError:
                pass
    return sources


def photo_record(photo, results):
    """Turn one osxphotos PhotoInfo and its ExportResults into a JSON-safe dict."""
    try:
        created = photo.date.isoformat() if photo.date else None
    except Exception:
        created = None
    errors = []
    for item in getattr(results, "error", None) or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            errors.append([str(item[0]), str(item[1])])
        else:
            errors.append([str(item), "error"])
    return {
        "uuid": str(photo.uuid),
        "original_filename": str(photo.original_filename or ""),
        "created": created,
        "favorite": bool(photo.favorite),
        "hidden": bool(photo.hidden),
        "albums": [str(a) for a in (photo.albums or []) if a],
        "ismovie": bool(photo.ismovie),
        "live_photo": bool(photo.live_photo),
        "ismissing": bool(getattr(photo, "ismissing", False)),
        "exported": _as_str_list(getattr(results, "exported", None)),
        "updated": _as_str_list(getattr(results, "updated", None)),
        "skipped": _as_str_list(getattr(results, "skipped", None)),
        "missing": _as_str_list(getattr(results, "missing", None)),
        "error": errors,
        "sources": _photo_sources(photo),
    }


def osxphotos_post_function(photo, results, verbose, **kwargs):
    """Called by osxphotos (--post-function FILE::osxphotos_post_function)."""
    staging = os.environ.get(STAGING_ENV)
    if not staging:
        return None
    try:
        line = json.dumps(photo_record(photo, results), ensure_ascii=False)
    except Exception as exc:  # keep the export going, but leave a trace
        line = json.dumps({"uuid": str(getattr(photo, "uuid", "")), "hook_error": str(exc)})
    try:
        with open(staging, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        try:
            verbose("[error]Homecloud could not write its record: %s" % exc)
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def say(msg=""):
    print(msg, flush=True)


def sha256_file(path, bufsize=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(bufsize)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def state_dir(dest):
    return os.path.join(dest, STATE_DIR)


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                say("Warning: skipping a damaged line %d in %s" % (lineno, path))
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


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json_atomic(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def clean_name(basename):
    """IMG_0001 (1).HEIC -> IMG_0001.HEIC (drop the collision number)."""
    stem, ext = os.path.splitext(basename)
    return _COLLISION_RE.sub("", stem) + ext


def manifest_filename(dest_path, kind, record):
    """The photo's own file name, without the " (1)" osxphotos adds when two photos share a name."""
    original = record.get("original_filename") or ""
    if not original:
        return clean_name(os.path.basename(dest_path))
    ext = os.path.splitext(dest_path)[1]
    return os.path.splitext(original)[0] + (EDITED_SUFFIX if kind == "edited" else "") + ext


def classify(dest_path, record):
    """Manifest kind for one exported file of a photo."""
    stem, ext = os.path.splitext(os.path.basename(dest_path))
    stem = _COLLISION_RE.sub("", stem)
    if stem.endswith(EDITED_SUFFIX):
        return "edited"
    if record.get("ismovie"):
        return "video"
    if ext.lower() in VIDEO_EXTS:
        return "live_video"
    return "photo"


def is_sidecar(path):
    return os.path.splitext(path)[1].lower() in SIDECAR_EXTS


def human_bytes(num):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if num < 1000 or unit == "TB":
            return ("%d %s" % (num, unit)) if unit == "bytes" else ("%.1f %s" % (num, unit))
        num /= 1000.0
    return str(num)


# --------------------------------------------------------------------------
# Running osxphotos
# --------------------------------------------------------------------------

class OsxphotosError(Exception):
    pass


def find_osxphotos(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("osxphotos")
    if found:
        candidates.append(found)
    candidates.append(os.path.expanduser("~/.local/bin/osxphotos"))
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def osxphotos_version(binary):
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OsxphotosError("Could not run osxphotos: %s" % exc)
    match = re.search(r"version\s+([0-9][0-9A-Za-z.\-]*)", proc.stdout + proc.stderr)
    if not match:
        raise OsxphotosError("Could not read the osxphotos version.")
    return match.group(1)


def require_osxphotos(args):
    binary = find_osxphotos(getattr(args, "osxphotos", None))
    if not binary:
        raise OsxphotosError("osxphotos is not installed.\n" + INSTALL_HELP)
    version = osxphotos_version(binary)
    if version != OSXPHOTOS_VERSION:
        raise OsxphotosError(
            "This script was checked with osxphotos %s, but you have %s.\n"
            'Install the checked version with:\n    uv tool install --force --python 3.13 "osxphotos==%s"'
            % (OSXPHOTOS_VERSION, version, OSXPHOTOS_VERSION)
        )
    return binary


def library_args(args):
    return ["--library", args.library] if getattr(args, "library", None) else []


def explain_failure(text):
    low = text.lower()
    if "operation not permitted" in low or "unable to open database" in low or "permission denied" in low:
        return (
            "macOS did not let osxphotos read the Photos library. Open System Settings >\n"
            "Privacy & Security > Photos and give Terminal full access. Quit and reopen Terminal,\n"
            "then run again. If it still fails, also turn on Full Disk Access for Terminal there."
        )
    if "located the following photos library" in low:
        return "osxphotos could not find your Photos library. Open Photos once, or pass --library PATH."
    return ""


QUERY_FIELDS = (
    ("u", "{uuid}"),
    ("p", "{photo.path?Y,N}"),
    ("s", "{photo.syndicated?Y,N}"),
    ("l", "{photo.saved_to_library?Y,N}"),
)


def parse_query_json(text):
    start = text.find("[")
    if start < 0:
        raise ValueError("no JSON list in the osxphotos output")
    data = json.loads(text[start:])
    if not isinstance(data, list):
        raise ValueError("unexpected osxphotos output")
    return data


def summarize_query(rows):
    """Count what osxphotos export will handle, and which originals are only in iCloud.

    osxphotos export skips "Shared with You" items that were never saved to the
    library and have no file, so they are not counted as expected.
    """
    total = expected = missing = 0
    for row in rows:
        total += 1
        no_file = row.get("p") != "Y"
        if row.get("s") == "Y" and row.get("l") != "Y" and no_file:
            continue
        expected += 1
        if no_file:
            missing += 1
    return {"total": total, "expected": expected, "missing": missing}


def library_counts(binary, args):
    """One read of the library: items to export and originals that are only in iCloud."""
    cmd = [binary, "query", "--not-shared", "--mute", "--json"] + library_args(args)
    for name, template in QUERY_FIELDS:
        cmd += ["--field", name, template]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4 * 3600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OsxphotosError("osxphotos query failed: %s" % exc)
    try:
        if proc.returncode != 0:
            raise ValueError("exit code %d" % proc.returncode)
        rows = parse_query_json(proc.stdout)
    except ValueError:
        hint = explain_failure(proc.stdout + proc.stderr)
        raise OsxphotosError("osxphotos could not read the photo list.\n" + (hint or proc.stderr.strip()[-800:]))
    return summarize_query(rows)


def build_export_command(binary, args, dest, staging, report):
    hook = "%s::%s" % (os.path.abspath(__file__), HOOK_FUNCTION)
    cmd = [
        binary, "export", dest,
        "--directory", "{created.year}/{created.mm}",
        "--edited-suffix", EDITED_SUFFIX,
        "--sidecar", "xmp",
        "--favorite-rating",
        "--album-keyword",
        "--not-shared",
        "--update",
        "--retry", "3",
        "--post-function", hook,
        "--report", report,
    ]
    cmd += library_args(args)
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    if args.download_missing:
        cmd += ["--download-missing"]
    return cmd


# --------------------------------------------------------------------------
# Manifest and verification
# --------------------------------------------------------------------------

def load_staging(path):
    """Last record per photo uuid, in export order."""
    records = {}
    hook_errors = []
    for row in read_jsonl(path):
        uuid = row.get("uuid")
        if not uuid:
            continue
        if "hook_error" in row:
            hook_errors.append(row)
            continue
        records[uuid] = row
    return records, hook_errors


class Verifier:
    def __init__(self, dest, cache, quick=False):
        self.dest = dest
        self.cache = cache  # rel path -> [size, mtime_ns, sha256]
        self.quick = quick
        self._source_hashes = {}
        self.disk_only = 0

    def _source_sha(self, path):
        if path not in self._source_hashes:
            self._source_hashes[path] = sha256_file(path)
        return self._source_hashes[path]

    def check(self, abs_path, record):
        """Return (status, size, sha256, problem)."""
        rel = os.path.relpath(abs_path, self.dest)
        try:
            st = os.stat(abs_path)
        except OSError:
            return "failed", 0, None, "file is not in the folder after export"
        cached = self.cache.get(rel)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            return "done", st.st_size, cached[2], None
        if st.st_size == 0:
            return "failed", 0, None, "file is empty"
        digest = sha256_file(abs_path)
        sources = record.get("sources") or []
        same_size = [s for s in sources if s.get("size") == st.st_size and os.path.isfile(s.get("path", ""))]
        if same_size:
            if not self.quick and not any(self._source_sha(s["path"]) == digest for s in same_size):
                return "failed", st.st_size, digest, "content differs from the original in the Photos library"
        elif record.get("ismissing") or not sources:
            # Fetched with --download-missing: there is no library copy to compare with.
            self.disk_only += 1
        else:
            return "failed", st.st_size, digest, "size differs from the original in the Photos library"
        self.cache[rel] = [st.st_size, st.st_mtime_ns, digest]
        return "done", st.st_size, digest, None


def manifest_rows_for(record, dest, verifier):
    rows, problems = [], []
    seen = set()
    base = {
        "asset_id": record["uuid"],
        "created": record.get("created"),
        "favorite": bool(record.get("favorite")),
        "hidden": bool(record.get("hidden")),
        "albums": list(record.get("albums") or []),
        "source": SOURCE,
    }

    def add(path, status, size, digest):
        kind = classify(path, record)
        row = dict(base)
        row.update({
            "filename": manifest_filename(path, kind, record),
            "path": os.path.relpath(path, dest).replace(os.sep, "/"),
            "size": int(size),
            "sha256": digest,
            "kind": kind,
            "status": status,
        })
        # keep the documented key order
        ordered = {k: row[k] for k in ("asset_id", "filename", "path", "size", "sha256", "created",
                                       "kind", "favorite", "hidden", "albums", "source", "status")}
        rows.append(ordered)

    errors = {}
    for path, message in record.get("error") or []:
        errors[path] = message
    for path in (record.get("exported") or []) + (record.get("updated") or []) + (record.get("skipped") or []):
        if path in seen or is_sidecar(path):
            continue
        seen.add(path)
        if path in errors:
            add(path, "failed", 0, None)
            problems.append((path, errors[path]))
            continue
        status, size, digest, problem = verifier.check(path, record)
        add(path, status, size, digest if status == "done" else None)
        if problem:
            problems.append((path, problem))
    for path in record.get("missing") or []:
        if path in seen or is_sidecar(path):
            continue
        seen.add(path)
        add(path, "missing", 0, None)
    for path, message in errors.items():
        if path in seen or is_sidecar(path):
            continue
        seen.add(path)
        add(path, "failed", 0, None)
        problems.append((path, message))
    return rows, problems


def merge_manifest(existing, new_rows, processed_ids):
    kept = [r for r in existing if not (r.get("source") == SOURCE and r.get("asset_id") in processed_ids)]
    return kept + new_rows


def other_sources(rows):
    return sorted({r.get("source") for r in rows if r.get("source") and r.get("source") != SOURCE})


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def check_platform(args):
    if sys.platform != "darwin" and not getattr(args, "library", None):
        say("This script reads the Photos app library, so it runs on a Mac.")
        say("On Windows, use the iCloud download (scripts/icloud.py) or the USB vault (scripts/vault/vault.py).")
        return False
    return True


def print_counts(counts):
    say("Photos and videos to copy: %d" % counts["expected"])
    if counts["missing"]:
        say("Originals that are still only in iCloud: %d" % counts["missing"])
        say(DOWNLOAD_ORIGINALS_HELP)
    else:
        say("Originals that are still only in iCloud: 0. Every original is on this Mac.")


def cmd_check(args):
    if not check_platform(args):
        return 1
    try:
        binary = require_osxphotos(args)
        say("osxphotos %s found at %s" % (OSXPHOTOS_VERSION, binary))
        say("Reading the Photos library. This can take a minute for a big library...")
        counts = library_counts(binary, args)
    except OsxphotosError as exc:
        say(str(exc))
        return 1
    print_counts(counts)
    if args.to:
        target = os.path.abspath(args.to)
        probe = target
        while not os.path.exists(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
        free = shutil.disk_usage(probe).free
        say("Free space where the copy will go (%s): %s" % (probe, human_bytes(free)))
    return 2 if counts["missing"] else 0


def run_export(binary, args, dest, staging, report):
    cmd = build_export_command(binary, args, dest, staging, report)
    env = dict(os.environ)
    env[STAGING_ENV] = staging
    say("Running osxphotos. You can stop it with Ctrl+C and run this command again later.")
    try:
        proc = subprocess.run(cmd, env=env)
    except KeyboardInterrupt:
        say("Stopped. Run the same command again to continue.")
        return 130
    except OSError as exc:
        say("Could not start osxphotos: %s" % exc)
        return 1
    return proc.returncode


def cmd_export(args):
    if not check_platform(args):
        return 1
    dest = os.path.abspath(args.to)
    os.makedirs(state_dir(dest), exist_ok=True)
    try:
        binary = require_osxphotos(args)
        say("Reading the Photos library. This can take a minute for a big library...")
        counts = library_counts(binary, args)
    except OsxphotosError as exc:
        say(str(exc))
        return 1
    print_counts(counts)
    if counts["missing"] and not args.download_missing:
        say("This run copies everything that is on this Mac now. The rest is listed as missing.")

    staging = os.path.join(state_dir(dest), STAGING_NAME)
    if os.path.exists(staging):
        os.unlink(staging)
    report = os.path.join(state_dir(dest), REPORT_NAME)
    code = run_export(binary, args, dest, staging, report)
    if code == 130:
        return 130
    return finish(args, dest, staging, counts, code)


def finish(args, dest, staging, counts, osxphotos_code):
    records, hook_errors = load_staging(staging)
    manifest_path = os.path.join(state_dir(dest), MANIFEST_NAME)
    state_path = os.path.join(state_dir(dest), STATE_NAME)
    cache = load_json(state_path, {})
    verifier = Verifier(dest, cache, quick=args.quick_verify)

    say("Checking every file against the Photos library (size and SHA-256)...")
    new_rows, problems = [], []
    for record in records.values():
        rows, probs = manifest_rows_for(record, dest, verifier)
        new_rows.extend(rows)
        problems.extend(probs)

    existing = read_jsonl(manifest_path)
    merged = merge_manifest(existing, new_rows, set(records))
    write_jsonl_atomic(manifest_path, merged)
    save_json_atomic(state_path, {k: v for k, v in cache.items() if os.path.exists(os.path.join(dest, k))})

    done = sum(1 for r in new_rows if r["status"] == "done")
    missing = sum(1 for r in new_rows if r["status"] == "missing")
    failed = sum(1 for r in new_rows if r["status"] == "failed")
    say("")
    say("Photos and videos processed: %d" % len(records))
    say("Files verified: %d of %d" % (done, len(new_rows)))
    if verifier.disk_only:
        say("Of those, %d came through --download-missing and were checked on disk only." % verifier.disk_only)
    if missing:
        say("Files still only in iCloud (listed as missing): %d" % missing)
        say(DOWNLOAD_ORIGINALS_HELP)
    for path, problem in problems[:20]:
        say("Problem: %s: %s" % (os.path.relpath(path, dest), problem))
    if len(problems) > 20:
        say("... and %d more. See %s" % (len(problems) - 20, manifest_path))
    if failed:
        say("To fix a file listed under Problem, delete that file in %s and run the same command again." % dest)
        say("osxphotos then copies it again from the Photos library.")
    others = other_sources(existing)
    if others:
        say("Note: this folder also has files from %s. Photos that came from both places are stored twice."
            % ", ".join(others))
    say("Manifest: %s" % manifest_path)

    incomplete_run = False
    if osxphotos_code != 0:
        say("osxphotos stopped with an error (exit code %d). Run the same command again." % osxphotos_code)
        incomplete_run = True
    if hook_errors:
        say("Homecloud could not record %d photos. Run the same command again." % len(hook_errors))
        incomplete_run = True
    if not args.limit and len(records) < counts["expected"]:
        say("osxphotos handled %d items but the library has %d. Run the same command again."
            % (len(records), counts["expected"]))
        incomplete_run = True

    if failed or incomplete_run:
        say("Result: not finished. Do not turn off iCloud Photos yet.")
        return 1
    if missing:
        say("Result: everything on this Mac is copied and verified. Files still only in iCloud: %d." % missing)
        return 2
    if args.limit:
        say("Result: the first %d items are copied and verified (test run with --limit)." % args.limit)
        return 0
    say("Result: all %d files are copied and verified." % done)
    return 0


def cmd_verify(args):
    dest = os.path.abspath(args.to)
    manifest_path = os.path.join(state_dir(dest), MANIFEST_NAME)
    rows = [r for r in read_jsonl(manifest_path) if r.get("source") == SOURCE]
    if not rows:
        say("No Mac Photos files in %s yet." % manifest_path)
        return 1
    done = [r for r in rows if r.get("status") == "done"]
    bad = []
    for i, row in enumerate(done, 1):
        path = os.path.join(dest, row["path"])
        try:
            ok = os.path.getsize(path) == row["size"] and sha256_file(path) == row["sha256"]
        except OSError:
            ok = False
        if not ok:
            bad.append(row["path"])
        if i % 1000 == 0:
            say("Checked %d of %d..." % (i, len(done)))
    not_done = len(rows) - len(done)
    say("Files verified: %d of %d" % (len(done) - len(bad), len(rows)))
    for path in bad[:20]:
        say("Changed or missing on disk: %s" % path)
    if not_done:
        say("Files never copied (missing or failed in the last export): %d" % not_done)
    if bad:
        return 1
    return 2 if not_done else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="export_photos.py",
        description=("Copy every photo and video from Photos on this Mac into DIR/YYYY/MM with "
                     "edited versions, Live Photo videos, .xmp files and DIR/.homecloud/manifest.jsonl. "
                     "Reads the Photos library only. Needs osxphotos %s." % OSXPHOTOS_VERSION),
    )
    sub = parser.add_subparsers(dest="command")

    def common(p):
        p.add_argument("--library", metavar="PATH", help="Photos library to read (default: the one Photos uses)")
        p.add_argument("--osxphotos", metavar="PATH", help="path to the osxphotos program, if it is not on PATH")

    p_check = sub.add_parser("check", help="count photos and originals that are still only in iCloud (read-only)")
    p_check.add_argument("--to", metavar="DIR", help="also show free space where the copy will go")
    common(p_check)

    p_export = sub.add_parser("export", help="copy and verify photos into DIR")
    p_export.add_argument("--to", metavar="DIR", required=True, help="folder for the copy")
    p_export.add_argument("--limit", type=int, default=0, metavar="N", help="copy at most N items (for a test run)")
    p_export.add_argument("--download-missing", action="store_true",
                          help="also ask Photos to fetch originals that are only in iCloud (slow, see the file header)")
    p_export.add_argument("--quick-verify", action="store_true",
                          help="compare sizes only, do not re-read the originals in the library")
    common(p_export)

    p_verify = sub.add_parser("verify", help="re-check sizes and SHA-256 of copied files (reads DIR only)")
    p_verify.add_argument("--to", metavar="DIR", required=True, help="folder with the copy")
    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "export":
        return cmd_export(args)
    if args.command == "verify":
        return cmd_verify(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
