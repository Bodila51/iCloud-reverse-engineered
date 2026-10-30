"""Offline tests for export_photos.py. A fake osxphotos program stands in for the real one.

Run: python3 -m unittest discover -s scripts/mac -p "test_*.py" -v

Optional test against the real osxphotos and a real (test) Photos library:
    HOMECLOUD_OSXPHOTOS=/path/to/osxphotos HOMECLOUD_TEST_LIBRARY=/path/to/Some.photoslibrary \
        python3 -m unittest discover -s scripts/mac -p "test_*.py" -v
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "export_photos.py")
sys.path.insert(0, HERE)
import export_photos  # noqa: E402

FAKE_OSXPHOTOS = r'''#!@PYTHON@
"""Fake osxphotos for tests. Mimics the parts of osxphotos 0.77.2 that export_photos.py uses."""
import datetime, importlib.util, json, os, sys


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def load_library(argv):
    lib = opt(argv, "--library")
    with open(os.path.join(lib, "library.json")) as fh:
        photos = json.load(fh)["photos"]
    for p in photos:
        for role in ("path", "path_edited", "path_live_photo", "cloud_file"):
            if p.get(role):
                p[role] = os.path.join(lib, p[role])
    return photos


class Photo:
    def __init__(self, p):
        self._p = p
        self.uuid = p["uuid"]
        self.original_filename = p["original_filename"]
        self.date = datetime.datetime.fromisoformat(p["date"])
        self.favorite = p.get("favorite", False)
        self.hidden = p.get("hidden", False)
        self.ismovie = p.get("ismovie", False)
        self.live_photo = bool(p.get("path_live_photo"))
        self.ismissing = not p.get("path")
        self.path = p.get("path")
        self.path_edited = p.get("path_edited")
        self.path_live_photo = p.get("path_live_photo")
        self.path_edited_live_photo = None
        self.path_raw = None

    @property
    def albums(self):
        if self.uuid in os.environ.get("FAKE_OSXPHOTOS_BREAK_ALBUMS", "").split(","):
            raise RuntimeError("album lookup failed")
        return self._p.get("albums", [])


class Results:
    def __init__(self):
        self.exported, self.new, self.updated, self.skipped = [], [], [], []
        self.missing, self.error, self.sidecar_xmp_written = [], [], []


def query(argv):
    if os.environ.get("FAKE_OSXPHOTOS_QUERY_FAIL"):
        sys.stderr.write(os.environ["FAKE_OSXPHOTOS_QUERY_FAIL"] + "\n")
        return 1
    rows = []
    for p in load_library(argv):
        if p.get("shared") and "--not-shared" in argv:
            continue
        rows.append({"u": p["uuid"], "p": "Y" if p.get("path") else "N",
                     "s": "Y" if p.get("syndicated") else "N",
                     "l": "Y" if p.get("saved_to_library", True) else "N"})
    print(json.dumps(rows, indent=2))
    return 0


def free_name(folder, name, db):
    taken = {e["name"].lower() for e in db.values() if e.get("folder") == folder}
    stem, ext = os.path.splitext(name)
    candidate, n = name, 0
    while candidate.lower() in taken or os.path.exists(os.path.join(folder, candidate)):
        n += 1
        candidate = "%s (%d)%s" % (stem, n, ext)
    return candidate


def export(argv):
    dest = argv[1]
    photos = load_library(argv)
    hook_file, hook_name = opt(argv, "--post-function").split("::")
    spec = importlib.util.spec_from_file_location("homecloud_hook", hook_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hook = getattr(module, hook_name)
    limit = int(opt(argv, "--limit", "0"))
    suffix = opt(argv, "--edited-suffix", "_edited")
    db_path = os.path.join(dest, ".osxphotos_export.db")
    db = json.load(open(db_path)) if os.path.exists(db_path) else {}
    damage = json.loads(os.environ.get("FAKE_OSXPHOTOS_DAMAGE", "{}"))
    count = 0
    for p in photos:
        if p.get("shared") and "--not-shared" in argv:
            continue
        if p.get("syndicated") and not p.get("saved_to_library", True) and not p.get("path"):
            continue  # osxphotos leaves out unsaved "Shared with You" items without a file
        if limit and count >= limit:
            break
        count += 1
        photo, res = Photo(p), Results()
        folder = os.path.join(dest, "%04d" % photo.date.year, "%02d" % photo.date.month)
        os.makedirs(folder, exist_ok=True)
        entry = db.setdefault(p["uuid"], {})
        if "name" not in entry:
            entry["name"], entry["folder"] = free_name(folder, p["original_filename"], db), folder
        name = entry["name"]
        stem = os.path.splitext(name)[0]
        src = p.get("path")
        if not src and "--download-missing" in argv and p.get("cloud_file"):
            src = p["cloud_file"]  # Photos fetched it from iCloud
        targets = []
        if src:
            targets.append((src, name))
            if p.get("path_live_photo"):
                targets.append((p["path_live_photo"], stem + ".mov"))
            if p.get("path_edited"):
                targets.append((p["path_edited"], stem + suffix + os.path.splitext(p["path_edited"])[1]))
        else:
            res.missing.append(os.path.join(folder, name))
        for source, target_name in targets:
            target = os.path.join(folder, target_name)
            sig = [os.path.getsize(source), os.path.getmtime(source)]
            if os.path.exists(target) and entry.get("files", {}).get(target) == sig:
                res.skipped.append(target)
                continue
            with open(source, "rb") as fh:
                data = fh.read()
            how = damage.get(p["uuid"])
            if how == "flip":
                data = data[:-1] + bytes([data[-1] ^ 0xFF])
            elif how == "truncate":
                data = data[:-1]
            with open(target, "wb") as fh:
                fh.write(data)
            entry.setdefault("files", {})[target] = sig
            res.exported.append(target)
            res.new.append(target)
        if targets:
            xmp = os.path.join(folder, name + ".xmp")
            with open(xmp, "w") as fh:
                fh.write("<x:xmpmeta/>")
            res.sidecar_xmp_written.append(xmp)
            res.exported.append(xmp)  # not what osxphotos does, but the script must skip sidecars anyway
        hook(photo, res, lambda *a, **k: None)
    with open(db_path, "w") as fh:
        json.dump(db, fh)
    with open(opt(argv, "--report"), "w") as fh:
        fh.write("[]")
    return int(os.environ.get("FAKE_OSXPHOTOS_EXIT", "0"))


def main():
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_OSXPHOTOS_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(argv) + "\n")
    if argv == ["--version"]:
        print("osxphotos, version " + os.environ.get("FAKE_OSXPHOTOS_VERSION", "0.77.2"))
        return 0
    if argv[0] == "query":
        return query(argv)
    if argv[0] == "export":
        return export(argv)
    return 2


sys.exit(main())
'''

LIBRARY = [
    {"uuid": "U1", "original_filename": "IMG_0001.HEIC", "date": "2024-05-17T10:00:00+02:00",
     "favorite": True, "albums": ["Trip", "Family"],
     "path": "originals/U1.heic", "path_live_photo": "originals/U1_3.mov", "path_edited": "renders/U1_1_201_a.jpeg"},
    {"uuid": "U2", "original_filename": "IMG_0002.MOV", "date": "2024-06-01T09:00:00+00:00",
     "hidden": True, "ismovie": True, "path": "originals/U2.mov"},
    {"uuid": "U3", "original_filename": "IMG_0003.HEIC", "date": "2024-06-02T08:00:00+00:00",
     "path": None, "cloud_file": "cloud/U3.heic"},
    {"uuid": "U4", "original_filename": "IMG_0004.JPG", "date": "2024-06-03T08:00:00+00:00",
     "shared": True, "path": "shared/U4.jpg"},
    {"uuid": "U5", "original_filename": "IMG_0005.JPG", "date": "2024-06-04T08:00:00+00:00",
     "syndicated": True, "saved_to_library": False, "path": None},
    {"uuid": "U6", "original_filename": "IMG_0001.HEIC", "date": "2024-05-20T08:00:00+00:00",
     "path": "originals/U6.heic"},
]

CONTENT = {
    "originals/U1.heic": b"HEIC-U1" * 1000,
    "originals/U1_3.mov": b"LIVE-U1" * 700,
    "renders/U1_1_201_a.jpeg": b"EDIT-U1" * 600,
    "originals/U2.mov": b"VIDEO-U2" * 900,
    "cloud/U3.heic": b"CLOUD-U3" * 500,
    "shared/U4.jpg": b"SHARED-U4" * 100,
    "originals/U6.heic": b"HEIC-U6" * 400,
}


def snapshot(folder):
    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, folder)] = (os.path.getsize(path), hashlib.sha256(fh.read()).hexdigest())
    return out


@unittest.skipIf(os.name == "nt", "the fake osxphotos is a script with a #! line")
class ExportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mac-export-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "Fake.photoslibrary")
        self.dest = os.path.join(self.tmp, "Homecloud")
        for rel, data in CONTENT.items():
            path = os.path.join(self.lib, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        with open(os.path.join(self.lib, "library.json"), "w") as fh:
            json.dump({"photos": LIBRARY}, fh)
        self.fake = os.path.join(self.tmp, "bin", "osxphotos")
        os.makedirs(os.path.dirname(self.fake))
        with open(self.fake, "w") as fh:
            fh.write(FAKE_OSXPHOTOS.replace("@PYTHON@", sys.executable))
        os.chmod(self.fake, 0o755)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        patcher = mock.patch.dict(os.environ, {"FAKE_OSXPHOTOS_LOG": self.log})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out):
            code = export_photos.main(list(argv))
        return code, out.getvalue()

    def export(self, *extra, env=None):
        return self.run_main("export", "--to", self.dest, "--library", self.lib, "--osxphotos", self.fake,
                             *extra, env=env)

    def manifest(self):
        return export_photos.read_jsonl(os.path.join(self.dest, ".homecloud", "manifest.jsonl"))

    def calls(self, command):
        with open(self.log) as fh:
            return [c for c in (json.loads(line) for line in fh) if c and c[0] == command]


class TestExport(ExportTestCase):
    def test_first_export_then_update(self):
        before = snapshot(self.lib)
        code, out = self.export()
        self.assertEqual(code, 2, out)  # one original is still only in iCloud
        self.assertIn("Photos and videos to copy: 4", out)
        self.assertIn("Originals that are still only in iCloud: 1", out)
        self.assertIn('"Download Originals to this Mac"', out)
        self.assertIn("Files verified: 5 of 6", out)
        self.assertIn("Files still only in iCloud: 1.", out)

        rows = {r["path"]: r for r in self.manifest()}
        self.assertEqual(sorted(rows), [
            "2024/05/IMG_0001 (1).HEIC",
            "2024/05/IMG_0001.HEIC",
            "2024/05/IMG_0001.mov",
            "2024/05/IMG_0001_edited.jpeg",
            "2024/06/IMG_0002.MOV",
            "2024/06/IMG_0003.HEIC",
        ])
        main = rows["2024/05/IMG_0001.HEIC"]
        self.assertEqual(list(main), ["asset_id", "filename", "path", "size", "sha256", "created", "kind",
                                      "favorite", "hidden", "albums", "source", "status"])
        self.assertEqual((main["asset_id"], main["kind"], main["status"], main["source"]),
                         ("U1", "photo", "done", "mac-photos"))
        self.assertTrue(main["favorite"])
        self.assertEqual(main["albums"], ["Trip", "Family"])
        self.assertEqual(main["created"], "2024-05-17T10:00:00+02:00")
        self.assertEqual(main["sha256"], hashlib.sha256(CONTENT["originals/U1.heic"]).hexdigest())
        self.assertEqual(rows["2024/05/IMG_0001.mov"]["kind"], "live_video")
        self.assertEqual(rows["2024/05/IMG_0001.mov"]["filename"], "IMG_0001.mov")
        self.assertEqual(rows["2024/05/IMG_0001_edited.jpeg"]["kind"], "edited")
        self.assertEqual(rows["2024/06/IMG_0002.MOV"]["kind"], "video")
        self.assertTrue(rows["2024/06/IMG_0002.MOV"]["hidden"])
        self.assertEqual(rows["2024/06/IMG_0003.HEIC"]["status"], "missing")
        self.assertIsNone(rows["2024/06/IMG_0003.HEIC"]["sha256"])
        clash = rows["2024/05/IMG_0001 (1).HEIC"]
        self.assertEqual((clash["asset_id"], clash["filename"]), ("U6", "IMG_0001.HEIC"))
        for rel, row in rows.items():
            if row["status"] == "done":
                with open(os.path.join(self.dest, rel), "rb") as fh:
                    self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), row["sha256"], rel)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "2024", "05", "IMG_0001.HEIC.xmp")))

        # the options osxphotos got: one copy per photo by date, sidecars, nothing that changes the library
        export_call = self.calls("export")[0]
        flags = " ".join(export_call)
        for expected in ("--directory {created.year}/{created.mm}", "--edited-suffix _edited", "--sidecar xmp",
                         "--favorite-rating", "--album-keyword", "--not-shared", "--update",
                         "--library " + self.lib):
            self.assertIn(expected, flags)
        hook = export_call[export_call.index("--post-function") + 1]
        self.assertEqual(hook, SCRIPT + "::osxphotos_post_function")
        for arg in export_call:
            self.assertFalse(arg.startswith(("--add-", "--exiftool", "--convert-to-jpeg", "--cleanup",
                                             "--download-missing")), arg)
            self.assertNotIn(arg, ("--album", "--folder"))  # filters would leave photos out
        self.assertNotIn("{folder_album}", flags)
        self.assertEqual(snapshot(self.lib), before, "the Photos library must not change")

        # second run: osxphotos skips everything, the script trusts its cache and hashes nothing
        with mock.patch.object(export_photos, "sha256_file", wraps=export_photos.sha256_file) as hasher:
            code, out = self.export()
        self.assertEqual(code, 2, out)
        self.assertEqual(hasher.call_count, 0)
        self.assertIn("Files verified: 5 of 6", out)
        self.assertEqual(len(self.manifest()), 6)

        # verify reads the folder only
        code, out = self.run_main("verify", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("Files verified: 5 of 6", out)
        self.assertIn("Files never copied (missing or failed in the last export): 1", out)
        with open(os.path.join(self.dest, "2024", "06", "IMG_0002.MOV"), "ab") as fh:
            fh.write(b"!")
        code, out = self.run_main("verify", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertIn("Changed or missing on disk: 2024/06/IMG_0002.MOV", out)

    def test_bad_copies_fail_and_can_be_fixed(self):
        code, out = self.export(env={"FAKE_OSXPHOTOS_DAMAGE": json.dumps({"U2": "flip", "U6": "truncate"})})
        self.assertEqual(code, 1, out)
        self.assertIn("IMG_0002.MOV: content differs from the original in the Photos library", out)
        self.assertIn("IMG_0001 (1).HEIC: size differs from the original in the Photos library", out)
        self.assertIn("Result: not finished. Do not turn off iCloud Photos yet.", out)
        self.assertIn("delete that file", out)
        failed = sorted(r["path"] for r in self.manifest() if r["status"] == "failed")
        self.assertEqual(failed, ["2024/05/IMG_0001 (1).HEIC", "2024/06/IMG_0002.MOV"])
        self.assertTrue(all(r["sha256"] is None for r in self.manifest() if r["status"] == "failed"))

        # a run without deleting them still fails: the bad files stay bad
        code, out = self.export()
        self.assertEqual(code, 1, out)
        # delete them as the message says, run again: osxphotos copies them again
        os.unlink(os.path.join(self.dest, "2024", "06", "IMG_0002.MOV"))
        os.unlink(os.path.join(self.dest, "2024", "05", "IMG_0001 (1).HEIC"))
        code, out = self.export()
        self.assertEqual(code, 2, out)
        self.assertEqual([r for r in self.manifest() if r["status"] == "failed"], [])
        self.assertIn("Files verified: 5 of 6", out)

    def test_download_missing_is_checked_on_disk_only(self):
        code, out = self.export("--download-missing")
        self.assertEqual(code, 0, out)
        self.assertIn("--download-missing", self.calls("export")[0])
        self.assertIn("Of those, 1 came through --download-missing and were checked on disk only.", out)
        self.assertIn("Result: all 6 files are copied and verified.", out)

    def test_limit(self):
        code, out = self.export("--limit", "1")
        self.assertEqual(code, 0, out)
        call = self.calls("export")[0]
        self.assertEqual(call[call.index("--limit") + 1], "1")
        self.assertEqual(sorted(r["path"] for r in self.manifest()),
                         ["2024/05/IMG_0001.HEIC", "2024/05/IMG_0001.mov", "2024/05/IMG_0001_edited.jpeg"])
        self.assertIn("Result: the first 1 items are copied and verified (test run with --limit).", out)

    def test_other_sources_are_kept(self):
        os.makedirs(os.path.join(self.dest, ".homecloud"))
        other = {"asset_id": "cloud-1", "filename": "IMG_9.JPG", "path": "2020/01/IMG_9.JPG", "size": 3,
                 "sha256": None, "created": None, "kind": "photo", "favorite": False, "hidden": False,
                 "albums": [], "source": "icloud-web", "status": "missing"}
        export_photos.write_jsonl_atomic(os.path.join(self.dest, ".homecloud", "manifest.jsonl"), [other])
        code, out = self.export()
        self.assertEqual(code, 2, out)
        self.assertIn(other, self.manifest())
        self.assertIn("Note: this folder also has files from icloud-web.", out)
        code, _out = self.export()
        self.assertEqual(sum(1 for r in self.manifest() if r["source"] == "icloud-web"), 1)

    def test_osxphotos_error_means_not_finished(self):
        code, out = self.export(env={"FAKE_OSXPHOTOS_EXIT": "1"})
        self.assertEqual(code, 1, out)
        self.assertIn("osxphotos stopped with an error (exit code 1)", out)
        self.assertIn("Do not turn off iCloud Photos yet.", out)

    def test_record_error_means_not_finished(self):
        code, out = self.export(env={"FAKE_OSXPHOTOS_BREAK_ALBUMS": "U2"})
        self.assertEqual(code, 1, out)
        self.assertIn("Homecloud could not record 1 photos.", out)
        self.assertIn("osxphotos handled 3 items but the library has 4.", out)


class TestCheck(ExportTestCase):
    def test_counts_and_free_space(self):
        code, out = self.run_main("check", "--to", self.dest, "--library", self.lib, "--osxphotos", self.fake)
        self.assertEqual(code, 2, out)
        self.assertIn("Photos and videos to copy: 4", out)
        self.assertIn("Originals that are still only in iCloud: 1", out)
        self.assertIn("Free space where the copy will go", out)
        self.assertEqual(self.calls("export"), [])
        query = self.calls("query")[0]
        self.assertIn("--not-shared", query)
        self.assertFalse(os.path.exists(self.dest))

    def test_all_originals_here(self):
        lib = [p for p in LIBRARY if p["uuid"] != "U3"]
        with open(os.path.join(self.lib, "library.json"), "w") as fh:
            json.dump({"photos": lib}, fh)
        code, out = self.run_main("check", "--library", self.lib, "--osxphotos", self.fake)
        self.assertEqual(code, 0, out)
        self.assertIn("Originals that are still only in iCloud: 0. Every original is on this Mac.", out)

    def test_wrong_osxphotos_version(self):
        code, out = self.run_main("check", "--library", self.lib, "--osxphotos", self.fake,
                                  env={"FAKE_OSXPHOTOS_VERSION": "0.76.0"})
        self.assertEqual(code, 1, out)
        self.assertIn("checked with osxphotos 0.77.2, but you have 0.76.0", out)
        self.assertIn('uv tool install --force --python 3.13 "osxphotos==0.77.2"', out)

    def test_osxphotos_missing(self):
        with mock.patch.object(export_photos.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"HOME": self.tmp}):
            code, out = self.run_main("check", "--library", self.lib, "--osxphotos", "/nowhere/osxphotos")
        self.assertEqual(code, 1, out)
        self.assertIn("osxphotos is not installed.", out)
        self.assertIn('uv tool install --python 3.13 "osxphotos==0.77.2"', out)

    def test_full_disk_access_hint(self):
        code, out = self.run_main("check", "--library", self.lib, "--osxphotos", self.fake,
                                  env={"FAKE_OSXPHOTOS_QUERY_FAIL": "sqlite3.OperationalError: unable to open "
                                                                    "database file"})
        self.assertEqual(code, 1, out)
        self.assertIn("Full Disk Access", out)

    def test_needs_a_mac(self):
        with mock.patch.object(export_photos.sys, "platform", "win32"):
            code, out = self.run_main("check")
        self.assertEqual(code, 1, out)
        self.assertIn("runs on a Mac", out)


class TestPostFunction(unittest.TestCase):
    def photo(self, **over):
        values = dict(uuid="P1", original_filename="IMG_1.HEIC", date=None, favorite=True, hidden=False,
                      albums=["A"], ismovie=False, live_photo=False, ismissing=False, path=None, path_edited=None,
                      path_live_photo=None, path_edited_live_photo=None, path_raw=None)
        values.update(over)
        return types.SimpleNamespace(**values)

    def results(self):
        return types.SimpleNamespace(exported=["/x/2024/01/IMG_1.HEIC"], updated=[], skipped=[], missing=[],
                                     error=[("/x/2024/01/IMG_2.HEIC", "boom")])

    def test_no_staging_file_does_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(export_photos.STAGING_ENV, None)
            self.assertIsNone(export_photos.osxphotos_post_function(self.photo(), self.results(), print))

    def test_writes_one_line_per_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "s.jsonl")
            with mock.patch.dict(os.environ, {export_photos.STAGING_ENV: staging}):
                export_photos.osxphotos_post_function(self.photo(), self.results(), print)
                export_photos.osxphotos_post_function(self.photo(uuid="P2"), self.results(), print)
            rows = export_photos.read_jsonl(staging)
        self.assertEqual([r["uuid"] for r in rows], ["P1", "P2"])
        self.assertEqual(rows[0]["exported"], ["/x/2024/01/IMG_1.HEIC"])
        self.assertEqual(rows[0]["error"], [["/x/2024/01/IMG_2.HEIC", "boom"]])
        self.assertTrue(rows[0]["favorite"])

    def test_never_raises(self):
        class Broken:
            uuid = "P3"

            def __getattr__(self, name):
                raise RuntimeError("broken photo")
        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "s.jsonl")
            with mock.patch.dict(os.environ, {export_photos.STAGING_ENV: staging}):
                self.assertIsNone(export_photos.osxphotos_post_function(Broken(), self.results(), print))
            rows = export_photos.read_jsonl(staging)
            self.assertEqual(rows[0]["uuid"], "P3")
            self.assertIn("hook_error", rows[0])
            messages = []
            with mock.patch.dict(os.environ, {export_photos.STAGING_ENV: tmp}):  # a folder cannot be appended to
                export_photos.osxphotos_post_function(self.photo(), self.results(), messages.append)
            self.assertTrue(messages and messages[0].startswith("[error]"))


class TestHelpers(unittest.TestCase):
    def test_classify_and_names(self):
        rec = {"ismovie": False, "original_filename": "IMG_0001.HEIC"}
        self.assertEqual(export_photos.classify("/d/IMG_0001.HEIC", rec), "photo")
        self.assertEqual(export_photos.classify("/d/IMG_0001 (1).mov", rec), "live_video")
        self.assertEqual(export_photos.classify("/d/IMG_0001_edited.jpeg", rec), "edited")
        self.assertEqual(export_photos.classify("/d/IMG_0001 (1)_edited.mov", rec), "edited")
        self.assertEqual(export_photos.classify("/d/IMG_0002.MOV", {"ismovie": True}), "video")
        self.assertEqual(export_photos.manifest_filename("/d/IMG_0001 (2).HEIC", "photo", rec), "IMG_0001.HEIC")
        self.assertEqual(export_photos.manifest_filename("/d/IMG_0001 (2)_edited.jpeg", "edited", rec),
                         "IMG_0001_edited.jpeg")
        party = {"original_filename": "Party (1999).jpg"}
        self.assertEqual(export_photos.manifest_filename("/d/Party (1999).jpg", "photo", party), "Party (1999).jpg")
        self.assertEqual(export_photos.clean_name("IMG_0001 (1).HEIC"), "IMG_0001.HEIC")

    def test_summarize_query(self):
        rows = [{"u": "a", "p": "Y", "s": "N", "l": "Y"}, {"u": "b", "p": "N", "s": "N", "l": "Y"},
                {"u": "c", "p": "N", "s": "Y", "l": "N"}, {"u": "d", "p": "Y", "s": "Y", "l": "N"}]
        self.assertEqual(export_photos.summarize_query(rows), {"total": 4, "expected": 3, "missing": 1})
        self.assertEqual(export_photos.parse_query_json('warning: x\n[{"u": "a"}]'), [{"u": "a"}])
        with self.assertRaises(ValueError):
            export_photos.parse_query_json("no photos library found")

    def test_build_export_command_never_changes_photos(self):
        args = export_photos.build_parser().parse_args(["export", "--to", "/tmp/x"])
        cmd = export_photos.build_export_command("osxphotos", args, "/tmp/x", "/tmp/s", "/tmp/r")
        self.assertFalse([a for a in cmd if a.startswith("--add-")])
        self.assertNotIn("--download-missing", cmd)

    def test_help_runs(self):
        for extra in ([], ["check"], ["export"], ["verify"]):
            proc = subprocess.run([sys.executable, SCRIPT] + extra + ["--help"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Reads the Photos library only", subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True).stdout)


REAL_OSXPHOTOS = os.environ.get("HOMECLOUD_OSXPHOTOS")
REAL_LIBRARY = os.environ.get("HOMECLOUD_TEST_LIBRARY")


@unittest.skipUnless(REAL_OSXPHOTOS and REAL_LIBRARY, "set HOMECLOUD_OSXPHOTOS and HOMECLOUD_TEST_LIBRARY")
class TestRealOsxphotos(unittest.TestCase):
    """Runs the real osxphotos on a real library. Use a test library, it takes a while on a big one."""

    def run_script(self, *argv):
        proc = subprocess.run([sys.executable, SCRIPT] + list(argv), capture_output=True, text=True, timeout=3600)
        return proc.returncode, proc.stdout + proc.stderr

    def test_export_twice_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Homecloud")
            before = snapshot(REAL_LIBRARY)
            common = ["--library", REAL_LIBRARY, "--osxphotos", REAL_OSXPHOTOS]
            code, out = self.run_script("export", "--to", dest, *common)
            self.assertIn(code, (0, 2), out)
            rows = export_photos.read_jsonl(os.path.join(dest, ".homecloud", "manifest.jsonl"))
            self.assertTrue(rows, out)
            self.assertEqual([r for r in rows if r["status"] == "failed"], [], out)
            for row in rows:
                if row["status"] == "done":
                    self.assertEqual(export_photos.sha256_file(os.path.join(dest, row["path"])), row["sha256"])
            self.assertEqual(len({(r["asset_id"], r["path"]) for r in rows}), len(rows))
            code2, out2 = self.run_script("export", "--to", dest, *common)
            self.assertEqual(code2, code, out2)
            rows2 = export_photos.read_jsonl(os.path.join(dest, ".homecloud", "manifest.jsonl"))
            self.assertEqual(sorted(r["path"] for r in rows2), sorted(r["path"] for r in rows))
            code3, out3 = self.run_script("verify", "--to", dest)
            self.assertIn(code3, (0, 2), out3)
            self.assertEqual(snapshot(REAL_LIBRARY), before, "the Photos library must not change")


if __name__ == "__main__":
    unittest.main()
