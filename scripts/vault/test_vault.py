"""Offline tests for vault.py. No phone needed: a fake phone stands in for pymobiledevice3.

Run: python3 -m unittest discover -s scripts/vault -p "test_*.py" -v
"""

import asyncio
import contextlib
import datetime as dt
import hashlib
import inspect
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

UTC = dt.timezone.utc


def core_data(year, month, day, hour=12):
    return (dt.datetime(year, month, day, hour, tzinfo=UTC) - vault.CORE_DATA_EPOCH).total_seconds()


class AfcFileNotFoundError(Exception):
    """Same class name as pymobiledevice3's, which vault.is_not_found looks for."""


class AfcException(Exception):
    pass


class FakeAfc:
    """A phone file system that can only be read. Any write method is missing on purpose."""

    def __init__(self, files, corrupt=(), hash_supported=True):
        self.files = {}  # path -> (bytes, mtime, birth)
        for path, value in files.items():
            if isinstance(value, tuple):
                self.files[path] = value
            else:
                ts = dt.datetime(2022, 1, 15, 9, 30).timestamp()
                self.files[path] = (value, ts, ts)
        self.corrupt = set(corrupt)
        self.hash_supported = hash_supported
        self.handles = {}
        self.next_handle = 1
        self.opened = []
        self.modes = []

    def _dirs(self):
        dirs = {"/"}
        for path in self.files:
            parts = path.strip("/").split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/" + "/".join(parts[:i]))
        return dirs

    async def listdir(self, path):
        path = path.rstrip("/") or "/"
        if path not in self._dirs():
            raise AfcFileNotFoundError(path)
        names = set()
        prefix = path.rstrip("/") + "/"
        for p in list(self.files) + list(self._dirs()):
            if p.startswith(prefix) and p != path:
                names.add(p[len(prefix):].split("/")[0])
        return sorted(names)

    async def stat(self, path):
        if path in self.files:
            data, mtime, birth = self.files[path]
            return {"st_ifmt": "S_IFREG", "st_size": len(data),
                    "st_mtime": dt.datetime.fromtimestamp(mtime), "st_birthtime": dt.datetime.fromtimestamp(birth)}
        if path in self._dirs():
            now = dt.datetime(2024, 1, 1)
            return {"st_ifmt": "S_IFDIR", "st_size": 0, "st_mtime": now, "st_birthtime": now}
        raise AfcFileNotFoundError(path)

    async def fopen(self, path, mode="r"):
        self.modes.append(mode)
        if mode != "r":
            raise AssertionError("the vault must only open phone files for reading")
        if path not in self.files:
            raise AfcFileNotFoundError(path)
        handle = self.next_handle
        self.next_handle += 1
        data = self.files[path][0]
        if path in self.corrupt:
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        self.handles[handle] = [data, 0]
        self.opened.append(path)
        return handle

    async def fread(self, handle, size):
        data, pos = self.handles[handle]
        chunk = data[pos:pos + size]
        self.handles[handle][1] = pos + len(chunk)
        return chunk

    async def fclose(self, handle):
        del self.handles[handle]

    async def get_file_hash(self, path):
        if not self.hash_supported:
            raise AfcException("Opcode: GET_FILE_HASH failed with status: OP_NOT_SUPPORTED")
        if path not in self.files:
            raise AfcFileNotFoundError(path)
        return hashlib.sha1(self.files[path][0]).digest()


class FakeSession:
    def __init__(self, afc, udid="00008110-001A2B3C4D5E6F70"):
        self.afc = afc
        self.info = {"udid": udid, "name": "Test iPhone", "ios": "26.1"}

    async def __aenter__(self):
        return vault.ReadOnlyAfc(self.afc)

    async def __aexit__(self, *exc):
        return False


def make_photos_db(folder, rows, albums=(), wal=True):
    """Build a small Photos.sqlite like the phone's. Returns {path on phone: bytes}."""
    path = os.path.join(folder, "Photos.sqlite")
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT, ZDIRECTORY TEXT, ZFILENAME TEXT,
            ZDATECREATED REAL, ZKIND INTEGER, ZKINDSUBTYPE INTEGER, ZTRASHEDSTATE INTEGER, ZHIDDEN INTEGER,
            ZFAVORITE INTEGER, ZCLOUDBATCHPUBLISHDATE REAL);
        CREATE TABLE ZADDITIONALASSETATTRIBUTES (Z_PK INTEGER PRIMARY KEY, ZASSET INTEGER,
            ZORIGINALFILENAME TEXT, ZORIGINALFILESIZE INTEGER, ZTIMEZONEOFFSET INTEGER);
        CREATE TABLE ZGENERICALBUM (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT, ZKIND INTEGER, ZTRASHEDSTATE INTEGER);
        CREATE TABLE Z_30ASSETS (Z_30ALBUMS INTEGER, Z_3ASSETS INTEGER, Z_FOK_3ASSETS INTEGER);
    """)
    for pk, r in enumerate(rows, 1):
        con.execute("INSERT INTO ZASSET VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (pk, r["uuid"], r["dir"], r["file"], r["created"], r.get("kind", 0), r.get("subtype", 0),
                     r.get("trashed", 0), r.get("hidden", 0), r.get("favorite", 0), r.get("shared")))
        con.execute("INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (?,?,?,?,?)",
                    (pk, pk, r.get("original"), r.get("size"), r.get("tz", 0)))
    for apk, (title, members) in enumerate(albums, 1):
        con.execute("INSERT INTO ZGENERICALBUM VALUES (?,?,?,?)", (apk, title, 2, 0))
        for uuid in members:
            pk = [i for i, r in enumerate(rows, 1) if r["uuid"] == uuid][0]
            con.execute("INSERT INTO Z_30ASSETS VALUES (?,?,?)", (apk, pk, 0))
    con.execute("INSERT INTO ZGENERICALBUM VALUES (?,?,?,?)", (99, "Old trip", 2, 1))  # a deleted album
    con.commit()
    out = {}
    with open(path, "rb") as fh:
        out["/PhotoData/Photos.sqlite"] = fh.read()
    if wal and os.path.exists(path + "-wal"):
        with open(path + "-wal", "rb") as fh:
            out["/PhotoData/Photos.sqlite-wal"] = fh.read()
    con.close()  # after reading, so the rows are still only in the -wal file
    return out


def standard_phone(tmp, with_db=True, extra=None):
    heic1 = b"HEIC-0001" * 1000
    mov1 = b"MOV-0001" * 800
    edit1 = b"EDIT-0001" * 500
    mov2 = b"VIDEO-0002" * 900
    cpl = b"CPL-JPEG" * 700
    files = {
        "/DCIM/100APPLE/IMG_0001.HEIC": heic1,
        "/DCIM/100APPLE/IMG_0001.MOV": mov1,
        "/PhotoData/Mutations/DCIM/100APPLE/IMG_0001/Adjustments/FullSizeRender.jpg": edit1,
        "/PhotoData/Mutations/DCIM/100APPLE/IMG_0001/Adjustments/Adjustments.plist": b"<plist/>",
        "/DCIM/100APPLE/IMG_0002.MOV": mov2,
        "/DCIM/100APPLE/IMG_0003.JPG": b"TRASHED" * 100,
        "/DCIM/100APPLE/IMG_0003.MOV": b"TRASHED-MOV" * 100,
        "/DCIM/100APPLE/IMG_0004.HEIC": b"small",
        "/PhotoData/CPLAssets/group1/7A8B.JPG": cpl,
        "/DCIM/100APPLE/IMG_0006.PNG": b"PNG" * 300,
        "/DCIM/.MISC/Info.plist": b"<plist/>",
    }
    if with_db:
        rows = [
            {"uuid": "A1", "dir": "DCIM/100APPLE", "file": "IMG_0001.HEIC", "created": core_data(2024, 5, 17, 10),
             "subtype": 2, "favorite": 1, "original": "IMG_0001.HEIC", "size": len(heic1), "tz": 7200},
            {"uuid": "A2", "dir": "DCIM/100APPLE", "file": "IMG_0002.MOV", "created": core_data(2024, 6, 1),
             "kind": 1, "hidden": 1, "original": "IMG_0002.MOV", "size": len(mov2)},
            {"uuid": "A3", "dir": "DCIM/100APPLE", "file": "IMG_0003.JPG", "created": core_data(2024, 6, 2),
             "trashed": 1, "original": "IMG_0003.JPG", "size": 700},
            {"uuid": "A4", "dir": "DCIM/100APPLE", "file": "IMG_0004.HEIC", "created": core_data(2024, 6, 3),
             "original": "IMG_0004.HEIC", "size": 100000},
            {"uuid": "A5", "dir": "DCIM/100APPLE", "file": "IMG_0005.HEIC", "created": core_data(2024, 6, 4),
             "original": "IMG_0005.HEIC", "size": 200000},
            {"uuid": "A6", "dir": "CPLAssets/group1", "file": "7A8B.JPG", "created": core_data(2023, 12, 24),
             "original": "IMG_9000.JPG", "size": len(cpl)},
            {"uuid": "A7", "dir": "PhotoCloudSharingData/1234/100CLOUD", "file": "IMG_0007.JPG",
             "created": core_data(2024, 6, 5), "shared": 1.0, "original": "IMG_0007.JPG", "size": 1000},
            {"uuid": "A8", "dir": "Syndication/x", "file": "S.JPG", "created": core_data(2024, 6, 6),
             "original": "S.JPG", "size": 1000},
        ]
        files.update(make_photos_db(tmp, rows, albums=[("Trip", ["A1", "A6"])]))
    files.update(extra or {})
    return files


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vault-test-")
        self.dest = os.path.join(self.tmp, "Vault")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, afc, *argv):
        out = io.StringIO()
        with mock.patch.object(vault, "open_phone", lambda udid=None: FakeSession(afc)), \
                contextlib.redirect_stdout(out):
            code = vault.main(list(argv))
        return code, out.getvalue()

    def manifest(self):
        return vault.read_jsonl(os.path.join(self.dest, ".homecloud", "manifest.jsonl"))

    def done_paths(self):
        return sorted(r["path"] for r in self.manifest() if r["status"] == "done")


class TestCopyWithPhotoList(VaultTestCase):
    def test_first_copy_then_incremental(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir))
        self.assertIn("/PhotoData/Photos.sqlite-wal", afc.files, "test DB should keep rows in the -wal file")

        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)  # two photos are not on the phone in full size
        self.assertEqual(self.done_paths(), [
            "2022/01/IMG_0006.PNG",
            "2023/12/IMG_9000.JPG",
            "2024/05/IMG_0001.HEIC",
            "2024/05/IMG_0001.MOV",
            "2024/05/IMG_0001_edited.jpg",
            "2024/06/IMG_0002.MOV",
        ])
        rows = {r["path"]: r for r in self.manifest()}
        heic = rows["2024/05/IMG_0001.HEIC"]
        self.assertEqual(heic["asset_id"], "A1")
        self.assertEqual(heic["kind"], "photo")
        self.assertTrue(heic["favorite"])
        self.assertEqual(heic["albums"], ["Trip"])
        self.assertEqual(heic["created"], "2024-05-17T12:00:00+02:00")
        self.assertEqual(heic["source"], "usb")
        self.assertEqual(heic["sha256"], hashlib.sha256(afc.files["/DCIM/100APPLE/IMG_0001.HEIC"][0]).hexdigest())
        self.assertEqual(rows["2024/05/IMG_0001.MOV"]["kind"], "live_video")
        self.assertEqual(rows["2024/05/IMG_0001_edited.jpg"]["kind"], "edited")
        self.assertEqual(rows["2024/06/IMG_0002.MOV"]["kind"], "video")
        self.assertTrue(rows["2024/06/IMG_0002.MOV"]["hidden"])
        self.assertEqual(rows["2023/12/IMG_9000.JPG"]["filename"], "IMG_9000.JPG")
        self.assertEqual(list(heic.keys()), ["asset_id", "filename", "path", "size", "sha256", "created", "kind",
                                             "favorite", "hidden", "albums", "source", "status"])
        missing = sorted(r["filename"] for r in self.manifest() if r["status"] == "missing")
        self.assertEqual(missing, ["IMG_0004.HEIC", "IMG_0005.HEIC"])
        for rel in self.done_paths():
            with open(os.path.join(self.dest, rel), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), rows[rel]["sha256"])
        # trashed photo and its Live Photo video, shared and syndicated items are not copied
        self.assertNotIn("/DCIM/100APPLE/IMG_0003.JPG", afc.opened)
        self.assertNotIn("/DCIM/100APPLE/IMG_0003.MOV", afc.opened)
        self.assertNotIn("/DCIM/100APPLE/IMG_0004.HEIC", afc.opened)
        self.assertEqual(set(afc.modes), {"r"})
        self.assertIn("Safe to free up 4 photos on the phone.", out)
        self.assertIn("2 photos on the phone are NOT in the vault", out)
        self.assertIn("New files copied and verified: 6 of 6", out)
        with open(os.path.join(self.dest, ".homecloud", "usb-not-in-vault.txt"), encoding="utf-8") as fh:
            listing = fh.read()
        self.assertIn("IMG_0004.HEIC", listing)
        self.assertIn("only a small copy", listing)
        self.assertFalse(os.path.exists(os.path.join(self.dest, ".homecloud", "tmp-usb")))

        # second run: nothing new is read from the phone, the result is the same
        afc.opened.clear()
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertEqual([p for p in afc.opened if p.startswith("/DCIM")], [])
        self.assertIn("New files copied and verified: 0 of 0", out)
        self.assertIn("Safe to free up 4 photos on the phone.", out)
        self.assertEqual(len([r for r in self.manifest() if r["status"] == "done"]), 6)
        self.assertEqual(len([r for r in self.manifest() if r["status"] == "missing"]), 2)

        # verify reads the vault only
        code, out = self.run_cmd(afc, "verify", "--to", self.dest)
        self.assertEqual(code, 0, out)
        self.assertIn("Files verified: 6 of 6", out)
        with open(os.path.join(self.dest, "2024", "06", "IMG_0002.MOV"), "ab") as fh:
            fh.write(b"x")
        code, out = self.run_cmd(afc, "verify", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertIn("Changed or missing on disk: 2024/06/IMG_0002.MOV", out)

    def test_check_copies_nothing(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir))
        code, out = self.run_cmd(afc, "check", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("Photos and videos on the phone that can be copied: 4 (6 files)", out)
        self.assertIn("Files not in the vault yet: 6", out)
        self.assertIn("Skipped, in Recently Deleted: 1", out)
        self.assertIn("Skipped, not in the camera roll (shared albums, photos synced from a computer): 2", out)
        self.assertIn("Not on this phone in full size: 2 of 5", out)
        self.assertIn("Optimize iPhone Storage", out)
        self.assertEqual([p for p in afc.opened if p.startswith("/DCIM")], [])
        self.assertFalse(os.path.exists(self.dest))

    def test_corrupt_transfer_marks_nothing_safe(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        files = standard_phone(dbdir)
        afc = FakeAfc(files, corrupt={"/DCIM/100APPLE/IMG_0002.MOV"})
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertNotIn("Safe to free up", out)
        self.assertIn("content differs from the phone (SHA-1 check)", out)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "2024", "06", "IMG_0002.MOV")))
        failed = [r for r in self.manifest() if r["status"] == "failed"]
        self.assertEqual([r["filename"] for r in failed], ["IMG_0002.MOV"])

        # the next good run copies it and drops the failed line
        afc = FakeAfc(files)
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("Safe to free up 4 photos on the phone.", out)
        self.assertEqual([r for r in self.manifest() if r["status"] == "failed"], [])
        self.assertIn("2024/06/IMG_0002.MOV", self.done_paths())

    def test_size_mismatch_fails(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir))
        original = afc.fread

        async def short_read(handle, size):
            data = await original(handle, size)
            return data[:-1] if len(data) > 5000 else data
        afc.fread = short_read
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertNotIn("Safe to free up", out)

    def test_stopped_copy_leaves_no_half_file(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        files = standard_phone(dbdir)
        afc = FakeAfc(files)
        original = afc.fread
        calls = {"n": 0}

        async def stalls(handle, size):
            calls["n"] += 1
            if calls["n"] > 3:  # the phone stops answering in the middle of the copy
                raise asyncio.TimeoutError()
            return await original(handle, min(size, 2000))
        afc.fread = stalls
        with mock.patch.object(vault, "CHUNK", 2000):
            code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertIn("The iPhone stopped answering", out)
        self.assertIn("Nothing on the phone was changed.", out)
        for folder in ("2022", "2023", "2024"):
            self.assertFalse(os.path.exists(os.path.join(self.dest, folder)), folder)
        self.assertEqual(self.done_paths(), [])
        # the next run starts the file over and finishes
        code, out = self.run_cmd(FakeAfc(files), "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("Safe to free up 4 photos on the phone.", out)
        self.assertEqual(os.listdir(os.path.join(self.dest, ".homecloud")).count("tmp-usb"), 0)

    def test_phone_without_hash_support_reads_twice(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir), hash_supported=False)
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("cannot compute file hashes", out)
        self.assertEqual(afc.opened.count("/DCIM/100APPLE/IMG_0002.MOV"), 2)
        self.assertIn("Safe to free up 4 photos on the phone.", out)

    def test_limit_never_splits_a_live_photo(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir))
        code, out = self.run_cmd(afc, "copy", "--to", self.dest, "--limit", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.done_paths(), ["2024/05/IMG_0001.HEIC", "2024/05/IMG_0001.MOV",
                                             "2024/05/IMG_0001_edited.jpg"])
        self.assertIn("Test run with --limit", out)
        self.assertNotIn("Safe to free up", out)

    def test_name_clash_keeps_live_photo_names_together(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        afc = FakeAfc(standard_phone(dbdir))
        os.makedirs(os.path.join(self.dest, "2024", "05"))
        with open(os.path.join(self.dest, "2024", "05", "IMG_0001.HEIC"), "wb") as fh:
            fh.write(b"a different photo with the same name")
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        names = [p for p in self.done_paths() if p.startswith("2024/05/")]
        self.assertEqual(names, ["2024/05/IMG_0001 (2).HEIC", "2024/05/IMG_0001 (2).MOV",
                                 "2024/05/IMG_0001_edited (2).jpg"])
        rows = {r["path"]: r for r in self.manifest()}
        self.assertEqual(rows["2024/05/IMG_0001 (2).HEIC"]["filename"], "IMG_0001.HEIC")

    def test_same_bytes_already_in_vault_are_not_stored_twice(self):
        dbdir = os.path.join(self.tmp, "db")
        os.makedirs(dbdir)
        files = standard_phone(dbdir)
        content = files["/DCIM/100APPLE/IMG_0002.MOV"]
        os.makedirs(os.path.join(self.dest, "2024", "06"))
        os.makedirs(os.path.join(self.dest, ".homecloud"))
        with open(os.path.join(self.dest, "2024", "06", "FROM_ICLOUD.MOV"), "wb") as fh:
            fh.write(content)
        vault.write_jsonl_atomic(os.path.join(self.dest, ".homecloud", "manifest.jsonl"), [{
            "asset_id": "cloud-1", "filename": "IMG_0002.MOV", "path": "2024/06/FROM_ICLOUD.MOV",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "created": None,
            "kind": "video", "favorite": False, "hidden": False, "albums": [], "source": "icloud-web",
            "status": "done"}])
        code, out = self.run_cmd(FakeAfc(files), "copy", "--to", self.dest)
        self.assertEqual(code, 2, out)
        self.assertIn("1 were already in the vault under another name", out)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "2024", "06", "IMG_0002.MOV")))
        self.assertIn("Safe to free up 4 photos on the phone.", out)
        self.assertEqual(sum(1 for r in self.manifest() if r["source"] == "icloud-web"), 1)


class TestCopyWithoutPhotoList(VaultTestCase):
    def test_fallback_groups_live_photos_by_name(self):
        ts = dt.datetime(2025, 3, 9, 8, 0).timestamp()
        afc = FakeAfc({
            "/DCIM/101APPLE/IMG_0001.HEIC": (b"H" * 5000, ts, ts),
            "/DCIM/101APPLE/IMG_0001.MOV": (b"M" * 4000, ts + 1, ts + 1),
            "/DCIM/101APPLE/IMG_0002.JPG": (b"J" * 3000, ts + 60, ts + 60),
            "/DCIM/100APPLE/IMG_0001.JPG": (b"OLD" * 900, ts - 86400 * 400, ts - 86400 * 400),
        })
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 0, out)
        self.assertIn("could not open the photo list", out)
        self.assertIn("Safe to free up 3 photos on the phone.", out)
        self.assertIn("Check before you delete: the vault has 3 photos and 0 videos from this phone.", out)
        self.assertNotIn("CPLAssets", out)
        self.assertEqual(self.done_paths(), ["2024/02/IMG_0001.JPG", "2025/03/IMG_0001.HEIC",
                                             "2025/03/IMG_0001.MOV", "2025/03/IMG_0002.JPG"])
        rows = {r["path"]: r for r in self.manifest()}
        self.assertEqual(rows["2025/03/IMG_0001.MOV"]["kind"], "live_video")
        self.assertEqual(rows["2025/03/IMG_0001.MOV"]["asset_id"], rows["2025/03/IMG_0001.HEIC"]["asset_id"])
        self.assertTrue(rows["2025/03/IMG_0001.HEIC"]["asset_id"].startswith("usb:"))

    def test_unreadable_camera_roll_is_an_error(self):
        afc = FakeAfc({"/PhotoData/other.txt": b"x"})
        code, out = self.run_cmd(afc, "copy", "--to", self.dest)
        self.assertEqual(code, 1, out)
        self.assertIn("Could not read the camera roll folder", out)
        self.assertIn("Nothing on the phone was changed.", out)


class TestCameraRollEdits(VaultTestCase):
    """Some phones may show an edit as IMG_E1234.JPG next to IMG_1234.HEIC."""

    def phone(self, with_db):
        files = {
            "/DCIM/100APPLE/IMG_0008.HEIC": b"ORIGINAL" * 400,
            "/DCIM/100APPLE/IMG_E0008.JPG": b"EDITED" * 300,
            "/DCIM/100APPLE/IMG_0008.AAE": b"<plist/>",
            "/DCIM/100APPLE/IMG_E0042.JPG": b"LONELY-EDIT" * 100,
        }
        if with_db:
            dbdir = os.path.join(self.tmp, "db")
            os.makedirs(dbdir)
            files.update(make_photos_db(dbdir, [
                {"uuid": "B8", "dir": "DCIM/100APPLE", "file": "IMG_0008.HEIC", "created": core_data(2024, 7, 1),
                 "original": "IMG_0008.HEIC", "size": 3200},
            ]))
        return FakeAfc(files)

    def check(self, with_db):
        code, out = self.run_cmd(self.phone(with_db), "copy", "--to", self.dest)
        self.assertEqual(code, 0, out)
        rows = {r["path"].split("/", 2)[2]: r for r in self.manifest()}
        self.assertEqual(sorted(rows), ["IMG_0008.HEIC", "IMG_0008_edited.JPG", "IMG_E0042.JPG"])
        self.assertEqual(rows["IMG_0008_edited.JPG"]["kind"], "edited")
        self.assertEqual(rows["IMG_0008_edited.JPG"]["asset_id"], rows["IMG_0008.HEIC"]["asset_id"])
        self.assertEqual(rows["IMG_E0042.JPG"]["kind"], "photo")  # no original next to it: kept as its own photo
        self.assertIn("Safe to free up 2 photos on the phone.", out)

    def test_with_photo_list(self):
        self.check(with_db=True)

    def test_without_photo_list(self):
        self.check(with_db=False)

    def test_icloud_hint_without_photo_list(self):
        afc = FakeAfc({"/DCIM/100APPLE/IMG_0001.HEIC": b"x" * 100, "/PhotoData/CPLAssets/g1/AB.JPG": b"y" * 100})
        code, out = self.run_cmd(afc, "check")
        self.assertEqual(code, 0, out)
        self.assertIn("Files in PhotoData/CPLAssets, the folder iCloud Photos uses: 1.", out)
        self.assertIn("Optimize iPhone Storage", out)


class TestHelpers(unittest.TestCase):
    def test_pick_names(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertEqual(vault.pick_names(tmp, ["IMG_1.HEIC", "IMG_1.MOV"]), ["IMG_1.HEIC", "IMG_1.MOV"])
            open(os.path.join(tmp, "img_1.mov"), "w").close()
            self.assertEqual(vault.pick_names(tmp, ["IMG_1.HEIC", "IMG_1.MOV"]),
                             ["IMG_1 (2).HEIC", "IMG_1 (2).MOV"])
        finally:
            shutil.rmtree(tmp)

    def test_safe_name(self):
        self.assertEqual(vault.safe_name('a:b*c?.jpg'), "a_b_c_.jpg")
        self.assertEqual(vault.safe_name("../x.jpg"), "_x.jpg")
        self.assertEqual(vault.safe_name(""), "unnamed")

    def test_core_data_time(self):
        when = vault.core_data_time(core_data(2024, 5, 17, 10), 7200)
        self.assertEqual(when.isoformat(), "2024-05-17T12:00:00+02:00")
        self.assertIsNone(vault.core_data_time(None, 0))

    def test_photo_list_folders(self):
        def asset(directory, filename="IMG_1.HEIC"):
            a = vault.DbAsset()
            a.directory, a.filename = directory, filename
            return a
        self.assertEqual(vault.db_candidates(asset("DCIM/100APPLE")),
                         ["/DCIM/100APPLE/IMG_1.HEIC", "/PhotoData/DCIM/100APPLE/IMG_1.HEIC"])
        self.assertEqual(vault.db_candidates(asset("/var/mobile/Media/DCIM/100APPLE"))[0], "/DCIM/100APPLE/IMG_1.HEIC")
        self.assertEqual(vault.db_candidates(asset("PhotoData/CPLAssets/g1")), ["/PhotoData/CPLAssets/g1/IMG_1.HEIC"])
        self.assertTrue(vault.in_camera_roll(asset("DCIM/100APPLE")))
        self.assertTrue(vault.in_camera_roll(asset("CPLAssets/group12")))
        self.assertTrue(vault.in_camera_roll(asset("/private/var/mobile/Media/PhotoData/CPLAssets/g1")))
        self.assertFalse(vault.in_camera_roll(asset("PhotoCloudSharingData/123/100CLOUD")))
        self.assertFalse(vault.in_camera_roll(asset("DCIMX")))

    def test_edits_by_owner(self):
        f = vault.DeviceFile("/PhotoData/Mutations/DCIM/100APPLE/IMG_0009/Adjustments/FullSizeRender.heic", 5, 0, 0)
        other = vault.DeviceFile("/PhotoData/Mutations/DCIM/100APPLE/IMG_0009/Adjustments/Adjustments.plist", 5, 0, 0)
        owners = vault.edits_by_owner([f, other])
        self.assertEqual(owners[("/dcim/100apple", "img_0009")], [f])

    def test_help_runs_on_this_python(self):
        proc = subprocess.run([sys.executable, os.path.join(HERE, "vault.py"), "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Never deletes or changes anything on the phone", proc.stdout)


def fake_pymobiledevice3(create):
    """Install stand-in pymobiledevice3 modules. Returns the exceptions module."""
    exc = types.ModuleType("pymobiledevice3.exceptions")
    for name in ("PairingError", "LockdownError", "MuxException"):
        setattr(exc, name, type(name, (Exception,), {}))
    for name in ("PasswordRequiredError", "PairingDialogResponsePendingError", "UserDeniedPairingError",
                 "InvalidHostIDError"):
        setattr(exc, name, type(name, (exc.PairingError,), {}))
    exc.PasscodeRequiredError = type("PasscodeRequiredError", (exc.LockdownError,), {})
    exc.ConnectionFailedToUsbmuxdError = type("ConnectionFailedToUsbmuxdError", (exc.MuxException,), {})
    for name in ("NoDeviceConnectedError", "DeviceNotFoundError", "FatalPairingError"):
        setattr(exc, name, type(name, (Exception,), {}))
    lockdown = types.ModuleType("pymobiledevice3.lockdown")
    lockdown.create_using_usbmux = create
    afc_mod = types.ModuleType("pymobiledevice3.services.afc")

    class AfcService:
        def __init__(self, lockdown):
            self.lockdown = lockdown

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    afc_mod.AfcService = AfcService
    pkg = types.ModuleType("pymobiledevice3")
    pkg.exceptions = exc
    services = types.ModuleType("pymobiledevice3.services")
    modules = {"pymobiledevice3": pkg, "pymobiledevice3.exceptions": exc, "pymobiledevice3.lockdown": lockdown,
               "pymobiledevice3.services": services, "pymobiledevice3.services.afc": afc_mod}
    return exc, modules


class TestPhoneSession(unittest.TestCase):
    def run_session(self, create):
        holder = {}

        async def wrapped(**kwargs):
            return await create(holder["exc"], **kwargs)
        exc, modules = fake_pymobiledevice3(wrapped)
        holder["exc"] = exc
        out = io.StringIO()

        async def no_sleep(_seconds):
            return None

        async def go():
            session = vault.PhoneSession(pair_timeout=30)
            async with session as afc:
                return session.info, afc
        with mock.patch.dict(sys.modules, modules), mock.patch("asyncio.sleep", no_sleep), \
                contextlib.redirect_stdout(out):
            try:
                result = asyncio.run(go())
            except vault.PhoneError as err:
                return None, str(err), out.getvalue()
        return result, None, out.getvalue()

    def test_waits_for_unlock_then_connects(self):
        calls = []

        async def create(exc, **kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise exc.PasswordRequiredError()
            return types.SimpleNamespace(udid="UDID-1", all_values={"DeviceName": "Ann's iPhone",
                                                                    "ProductVersion": "26.1"},
                                         close=lambda: asyncio.sleep(0))
        result, error, out = self.run_session(create)
        self.assertIsNone(error)
        info, afc = result
        self.assertEqual(info, {"udid": "UDID-1", "name": "Ann's iPhone", "ios": "26.1"})
        self.assertIsInstance(afc, vault.ReadOnlyAfc)
        self.assertEqual(calls[0]["connection_type"], "USB")
        self.assertTrue(calls[0]["autopair"])
        self.assertIn("Trust This Computer", out)
        self.assertIn("Waiting for you to unlock", out)

    def test_error_messages(self):
        cases = [
            ("UserDeniedPairingError", "darwin", "Don't Trust"),
            ("PairingDialogResponsePendingError", "darwin", "Nobody answered"),
            ("ConnectionFailedToUsbmuxdError", "win32", "Install the Apple Devices app from the Microsoft Store"),
            ("ConnectionFailedToUsbmuxdError", "darwin", "Restart the Mac"),
            ("NoDeviceConnectedError", "darwin", "No iPhone found"),
            ("NoDeviceConnectedError", "win32", "open the Apple Devices app"),
        ]
        for name, platform, text in cases:
            async def create(exc, _name=name, **kwargs):
                raise getattr(exc, _name)()
            with mock.patch.object(vault, "current_platform", lambda _p=platform: _p):
                result, error, _out = self.run_session(create)
            self.assertIsNone(result)
            self.assertIn(text, error, (name, platform))

    def test_other_pymobiledevice3_version_gets_a_note(self):
        async def create(exc, **kwargs):
            raise exc.NoDeviceConnectedError()
        with mock.patch.object(vault, "installed_version", lambda _name: "99.0.0"):
            _result, _error, out = self.run_session(create)
        self.assertIn("checked with pymobiledevice3 11.49.0, and you have 99.0.0", out)


try:
    import pymobiledevice3  # noqa: F401
    HAVE_PMD3 = True
except ImportError:
    HAVE_PMD3 = False


@unittest.skipUnless(HAVE_PMD3, "pymobiledevice3 is not installed for this Python")
class TestRealPymobiledevice3Api(unittest.TestCase):
    """Checks the names this script uses against the installed pymobiledevice3."""

    def test_version_is_the_pinned_one(self):
        from importlib import metadata
        self.assertEqual(metadata.version("pymobiledevice3"), vault.PYMOBILEDEVICE3_VERSION)

    def test_api_names(self):
        from pymobiledevice3 import exceptions as pexc
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AFC_FOPEN_TEXTUAL_MODES, AfcFopenMode, AfcService
        for name in ("PasswordRequiredError", "PasscodeRequiredError", "PairingDialogResponsePendingError",
                     "UserDeniedPairingError", "ConnectionFailedToUsbmuxdError", "NoDeviceConnectedError",
                     "DeviceNotFoundError", "FatalPairingError", "InvalidHostIDError", "PairingError",
                     "AfcFileNotFoundError"):
            self.assertTrue(hasattr(pexc, name), name)
        params = inspect.signature(create_using_usbmux).parameters
        for name in ("serial", "connection_type", "autopair", "pair_timeout"):
            self.assertIn(name, params)
        for name in ("listdir", "stat", "fopen", "fread", "fclose", "get_file_hash", "__aenter__", "__aexit__"):
            self.assertTrue(hasattr(AfcService, name), name)
        self.assertEqual(AFC_FOPEN_TEXTUAL_MODES["r"], AfcFopenMode.RDONLY)
        self.assertIn("lockdown", inspect.signature(AfcService.__init__).parameters)

    def test_no_usbmuxd_gives_a_plain_message(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.dict(os.environ, {"USBMUXD_SOCKET_ADDRESS": "127.0.0.1:1"}):
            code = vault.main(["check"])
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertTrue("usbmuxd" in text or "Apple Mobile Device Service" in text or "No iPhone found" in text, text)
        self.assertIn("Nothing on the phone was changed.", text)


if __name__ == "__main__":
    unittest.main()
