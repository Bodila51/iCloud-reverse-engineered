"""Offline tests for scripts/icloud.py. No network: a fake iCloud stands in for Apple.

Run from the kit folder:  python3 -m pytest tests
"""

import ast
import calendar
import collections
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import fake_icloud as F
import icloud

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "icloud.py")

try:
    import pyicloud  # noqa: F401
    from pyicloud import exceptions as PE
    HAVE_PYICLOUD = sys.version_info >= (3, 10)
except Exception:
    PE = None
    HAVE_PYICLOUD = False

try:
    import requests  # noqa: F401
    HAVE_REQUESTS = True
except Exception:
    HAVE_REQUESTS = False

needs_pyicloud = pytest.mark.skipif(not HAVE_PYICLOUD, reason="needs pyicloud (Python 3.10+)")
needs_requests = pytest.mark.skipif(not HAVE_REQUESTS, reason="needs requests")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def new_out():
    return icloud.Out(io.StringIO())


def text_of(out):
    return out.stream.getvalue()


def download(bundle, root, *extra):
    args = icloud.build_parser().parse_args(["download", "--to", str(root)] + list(extra))
    out = new_out()
    rc = icloud.run_download(bundle, args, out, retry_base=0.001)
    return rc, text_of(out)


def verify(root, *extra):
    args = icloud.build_parser().parse_args(["verify", "--to", str(root)] + list(extra))
    out = new_out()
    rc = icloud.cmd_verify(args, out)
    return rc, text_of(out)


def manifest(root):
    rows, bad = icloud.read_manifest(str(root))
    assert not bad
    return rows


def by_path(root):
    return dict((row["path"], row) for row in manifest(root))


def fetched(fetcher):
    """How often each file was requested from the start (resumed requests not counted)."""
    return collections.Counter((a, f) for a, f, start in fetcher.requests if start == 0)


def all_files(cloud):
    return [(p.asset_id, field) for p in cloud.photos for _, field, _ in p.fields()]


def last_line(text):
    return [line for line in text.strip().splitlines() if line.strip()][-1]


def utc_ts(day):
    return calendar.timegm(day.utctimetuple())


class LapsingCloud(F.FakeICloud):
    """A fake iCloud whose CloudKit calls start to fail at the Nth call to one path,
    the way an expired approval or session looks."""

    def __init__(self, photos, fail_path, fail_at, error, times=None, **kwargs):
        F.FakeICloud.__init__(self, photos, **kwargs)
        self.fail_path = fail_path
        self.fail_at = fail_at
        self.error = error
        self.times = times
        self.seen = 0
        self.failed = 0
        self.armed = True

    def post_json(self, db, path, payload):
        if self.armed and path == self.fail_path:
            self.seen += 1
            if self.seen >= self.fail_at and (self.times is None or self.failed < self.times):
                self.failed += 1
                raise self.error
        return F.FakeICloud.post_json(self, db, path, payload)


class StrictCloud(F.FakeICloud):
    """Fails the test on any attribute this script has no business using,
    for example delete, modify, upload or save calls."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.__dict__.setdefault("violations", []).append("attribute " + name)
        raise AssertionError("icloud.py used %r on the iCloud service" % name)


class StrictFetcher(F.FakeFetcher):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError("icloud.py used %r on the file fetcher" % name)


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def test_scan_counts_everything(tmp_path):
    storage = {"used_bytes": 51_200_000_000, "total_bytes": 200_000_000_000,
               "photos_bytes": 40_100_000_000, "photos_label": "Photos and Videos"}
    cloud = F.FakeICloud(F.sample_library(), storage=storage)
    out = new_out()
    json_path = tmp_path / "scan.json"
    rc = icloud.run_scan(F.make_bundle(cloud), out, str(json_path), "someone@example.com")
    text = text_of(out)
    assert rc == 0, text
    data = json.loads(json_path.read_text())
    assert data["total_assets"] == 8
    assert data["photos"] == 6
    assert data["videos"] == 2
    assert data["live_photos"] == 2
    assert data["edited"] == 3
    assert data["hidden"] == 1
    assert data["favorites"] == 1
    assert data["shared_library_items"] == 2
    assert data["albums"] == 2
    assert data["download_files"] == 15
    assert data["recently_deleted"] == 2
    assert data["complete"] is True
    assert sorted(data["by_year"]) == ["2019", "2020", "2021", "2022"]
    assert all(info["assets"] == 2 for info in data["by_year"].values())
    assert data["original_bytes"] == sum(info["original_bytes"] for info in data["by_year"].values())
    assert data["icloud_storage"]["photos_bytes"] == 40_100_000_000
    assert "Photos and Videos" in text and "51.2 GB" in text and "of 200.0 GB" in text
    assert "Nothing was changed in iCloud." in text
    assert "so***@example.com" in text and "someone@example.com" not in text
    assert cloud.violations == []


def test_scan_json_to_stdout_is_clean():
    cloud = F.FakeICloud(F.sample_library())
    progress, data_out = new_out(), new_out()
    rc = icloud.run_scan(F.make_bundle(cloud), progress, "-", "", data_out)
    assert rc == 0
    assert json.loads(text_of(data_out))["total_assets"] == 8
    assert "Reading your library" in text_of(progress) or "Read 8 items" in text_of(progress)


def test_listing_follows_pages_and_finds_masters_on_other_pages():
    photos = [F.FakePhoto("P%03d" % i, "IMG_%04d.HEIC" % i, F.day(2018, 1 + i % 12, 1 + i % 28, i % 24))
              for i in range(250)]
    cloud = F.FakeICloud(photos, split_masters=True)
    out = new_out()
    lib = icloud.list_library(F.make_bundle(cloud).client, out)
    assert len(lib.assets) == 250
    assert len(set(a.asset_id for a in lib.assets)) == 250
    assert all(a.filename.startswith("IMG_") for a in lib.assets)
    assert ("private", "records/lookup") in cloud.calls  # masters left off a page were looked up
    assert not lib.problems and lib.complete


def test_scan_reports_when_icloud_counts_more_than_it_lists(tmp_path):
    class Undercount(F.FakeICloud):
        def post_json(self, db, path, payload):
            data = F.FakeICloud.post_json(self, db, path, payload)
            if path == "internal/records/query/batch":
                data["batch"][0]["records"][0]["fields"]["itemCount"]["value"] += 3
            return data

    cloud = Undercount(F.sample_library())
    out = new_out()
    rc = icloud.run_scan(F.make_bundle(cloud), out)
    assert rc == 1
    assert "but only" in text_of(out)

    # A download from such a listing must not let verify say "Nothing missing".
    root = tmp_path / "photos"
    rc, text = download(F.make_bundle(cloud), root)
    assert rc == 1, text
    rc, text = verify(root)
    assert rc == 1
    assert last_line(text).startswith("NOT VERIFIED")


def test_a_part_of_the_library_that_cannot_be_read_blocks_verify(tmp_path):
    """If the Shared Library's hidden items cannot be listed, the download says so and
    verify does not claim that nothing is missing."""
    class SharedHiddenFails(F.FakeICloud):
        def post_json(self, db, path, payload):
            if path == "records/query" and payload["query"]["recordType"] == icloud.LIST_HIDDEN \
                    and payload["zoneID"]["zoneName"].startswith("SharedSync-"):
                raise icloud.CloudError("fatal", "BAD_REQUEST: Unknown index")
            return F.FakeICloud.post_json(self, db, path, payload)

    cloud = SharedHiddenFails(F.sample_library())
    root = tmp_path / "photos"
    rc, text = download(F.make_bundle(cloud), root)
    assert rc == 1 and "Could not read the hidden items of the Shared Library" in text
    rc, text = verify(root)
    assert rc == 1
    assert "Could not read the hidden items of the Shared Library" in text
    assert last_line(text).startswith("NOT VERIFIED: 15 of 15 files are OK.")


# --------------------------------------------------------------------------
# download and verify
# --------------------------------------------------------------------------

def test_download_everything_then_verify(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    root = tmp_path / "photos"
    rc, text = download(bundle, root)
    assert rc == 0, text
    rows = by_path(root)
    assert sorted(rows) == sorted([
        "2019/05/IMG_0001.HEIC", "2019/05/IMG_0001.MOV",
        "2019/05/IMG_0002.HEIC", "2019/05/IMG_0002_edited.JPG",
        "2020/01/IMG_0003.MOV", "2020/01/IMG_0003_edited.MOV",
        "2020/01/IMG_0004.JPG",
        "2021/07/IMG_0005.HEIC", "2021/07/IMG_0005.MOV",
        "2021/07/IMG_0005_edited.JPG", "2021/07/IMG_0005_edited.MOV",
        "2021/07/IMG_0006.JPG", "2021/07/IMG_0006.DNG",
        "2022/03/IMG_7001.HEIC", "2022/03/IMG_7002.MOV",
    ])
    keys = ["asset_id", "filename", "path", "size", "sha256", "created", "kind", "favorite",
            "hidden", "albums", "source", "status"]
    for path, row in rows.items():
        assert all(k in row for k in keys), row
        assert row["source"] == "icloud-web" and row["status"] == "done"
        data = (root / path).read_bytes()
        field = {"original": "resOriginal", "alt": "resOriginalAlt"}.get(row["variant"])
        assert len(data) == row["size"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
        if field:
            assert data == cloud.content(row["asset_id"], field)
    assert rows["2019/05/IMG_0001.MOV"]["kind"] == "live_video"
    assert rows["2019/05/IMG_0002_edited.JPG"]["kind"] == "edited"
    assert rows["2020/01/IMG_0003.MOV"]["kind"] == "video"
    assert rows["2020/01/IMG_0004.JPG"]["hidden"] is True
    assert rows["2019/05/IMG_0001.HEIC"]["favorite"] is True
    assert rows["2019/05/IMG_0002.HEIC"]["albums"] == ["Best", "Trip"]
    assert rows["2022/03/IMG_7001.HEIC"]["library"] == "shared"
    assert rows["2019/05/IMG_0001.HEIC"]["created"] == "2019-05-01T14:00:00+02:00"
    assert rows["2019/05/IMG_0001.HEIC"]["filename"] == "IMG_0001.HEIC"
    # Progress lines look like "1,234 / 12,345 files · 45.6 / 120.0 GB · about 1 h 12 min left".
    assert re.search(r"^15 / 15 files · [\d.]+ / [\d.]+ KB · done$", text, re.M), text
    assert cloud.violations == []

    rc, text = verify(root)
    assert rc == 0, text
    assert last_line(text).startswith("VERIFIED 15 of 15 files (")
    assert last_line(text).endswith("Nothing missing.")
    assert "live_video" in text and "edited" in text


def test_file_dates_are_capture_dates(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    rc, text = download(F.make_bundle(cloud), root)
    assert rc == 0, text
    taken = dict((p.asset_id, p.taken) for p in cloud.photos)
    for path, row in by_path(root).items():
        assert abs(os.path.getmtime(str(root / path)) - utc_ts(taken[row["asset_id"]])) < 1, path


def test_resume_downloads_only_what_is_missing(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    root = tmp_path / "photos"
    rc, text = download(bundle, root, "--limit", "3")
    assert rc == 0, text
    assert len(manifest(root)) == 6  # A1, A2, A3: two files each
    rc, text = verify(root)
    assert rc == 0, text
    assert "VERIFIED 6 of 6 files" in text and "--limit" in text

    assert by_path(root)["2019/05/IMG_0002.HEIC"]["albums"] == []  # --limit skips album names

    rc, text = download(bundle, root)
    assert rc == 0, text
    counts = fetched(bundle.fetcher)
    assert sorted(counts) == sorted(all_files(cloud))
    assert set(counts.values()) == {1}, counts  # nothing was fetched twice
    assert by_path(root)["2019/05/IMG_0002.HEIC"]["albums"] == ["Best", "Trip"]  # the full run adds them

    before = len(bundle.fetcher.requests)
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert len(bundle.fetcher.requests) == before
    assert "15 already here, 0 to download" in text


def test_resume_replaces_a_file_with_the_wrong_size(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    root = tmp_path / "photos"
    assert download(bundle, root)[0] == 0
    target = root / "2021/07/IMG_0005.MOV"
    target.write_bytes(target.read_bytes()[:10])
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert target.read_bytes() == cloud.content("A5", "resOriginalVidCompl")
    assert fetched(bundle.fetcher)[("A5", "resOriginalVidCompl")] == 2
    assert verify(root)[0] == 0


def test_name_collisions_get_numbered_and_unknown_files_stay_untouched(tmp_path):
    photos = [
        F.FakePhoto("P1", "IMG_0001.HEIC", F.day(2019, 5, 1), live=True),
        F.FakePhoto("P2", "IMG_0001.HEIC", F.day(2019, 5, 20), live=True),
        F.FakePhoto("P3", "img_0001.heic", F.day(2019, 5, 25)),
        F.FakePhoto("P4", "IMG_0001.HEIC", F.day(2019, 6, 1)),
    ]
    cloud = F.FakeICloud(photos)
    bundle = F.make_bundle(cloud)
    root = tmp_path / "photos"
    (root / "2019" / "05").mkdir(parents=True)
    stranger = root / "2019" / "05" / "IMG_0001.HEIC"
    stranger.write_bytes(b"a file that was already here")

    rc, text = download(bundle, root)
    assert rc == 0, text
    paths = dict(((row["asset_id"], row["variant"]), row["path"]) for row in manifest(root))
    assert paths[("P1", "original")] == "2019/05/IMG_0001 (2).HEIC"
    assert paths[("P1", "live")] == "2019/05/IMG_0001 (2).MOV"
    assert paths[("P2", "original")] == "2019/05/IMG_0001 (3).HEIC"
    assert paths[("P2", "live")] == "2019/05/IMG_0001 (3).MOV"
    assert paths[("P3", "original")] == "2019/05/img_0001 (4).heic"
    assert paths[("P4", "original")] == "2019/06/IMG_0001.HEIC"
    assert stranger.read_bytes() == b"a file that was already here"
    assert "2019/05/IMG_0001.HEIC" not in by_path(root)
    rows = by_path(root)
    assert rows["2019/05/IMG_0001 (2).HEIC"]["filename"] == "IMG_0001.HEIC"
    for (asset_id, variant), path in paths.items():
        field = "resOriginal" if variant == "original" else "resOriginalVidCompl"
        assert (root / path).read_bytes() == cloud.content(asset_id, field)

    # A second run keeps every name and fetches nothing.
    before = len(bundle.fetcher.requests)
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert len(bundle.fetcher.requests) == before
    assert dict(((r["asset_id"], r["variant"]), r["path"]) for r in manifest(root)) == paths
    assert verify(root)[0] == 0


def test_clean_filename_is_safe_everywhere():
    assert icloud.clean_filename('a/b\\c:d*e?"f<g>h|i.JPG') == "a_b_c_d_e__f_g_h_i.JPG"
    assert icloud.clean_filename("CON.jpg") == "_CON.jpg"
    assert icloud.clean_filename(".hidden.jpg") == "_hidden.jpg"
    assert icloud.clean_filename("photo. ") == "photo"
    assert icloud.clean_filename("") == "unnamed"
    long_name = "x" * 300 + ".HEIC"
    cleaned = icloud.clean_filename(long_name)
    assert cleaned.endswith(".HEIC") and len(cleaned.encode("utf-8")) <= 180
    assert icloud.rel_to_abs("/r", "../etc/passwd") is None
    assert icloud.rel_to_abs("/r", "/etc/passwd") is None
    assert icloud.rel_to_abs("/r", "C:/x") is None
    assert icloud.rel_to_abs("/r", "2020/01/a.jpg") == os.path.join("/r", "2020", "01", "a.jpg")


def test_verify_lists_missing_and_damaged_files_then_download_repairs_them(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    root = tmp_path / "photos"
    assert download(bundle, root)[0] == 0
    (root / "2019/05/IMG_0001.HEIC").unlink()                       # missing
    damaged = root / "2019/05/IMG_0002.HEIC"
    damaged.write_bytes(damaged.read_bytes()[:-5])                  # wrong size
    flipped = root / "2020/01/IMG_0003.MOV"
    data = bytearray(flipped.read_bytes())
    data[0] ^= 0xFF
    flipped.write_bytes(bytes(data))                                # same size, wrong bytes

    rc, text = verify(root)
    assert rc == 1
    assert "2019/05/IMG_0001.HEIC  (not on disk)" in text
    assert re.search(r"2019/05/IMG_0002\.HEIC  \(size is [\d,]+ bytes, expected [\d,]+\)", text)
    assert "2020/01/IMG_0003.MOV  (checksum does not match)" in text
    assert last_line(text).startswith("NOT VERIFIED: 12 of 15 files are OK.")
    report = (root / ".homecloud" / "verify-report.txt").read_text()
    assert "IMG_0001.HEIC" in report and "IMG_0002.HEIC" in report and "IMG_0003.MOV" in report

    before = fetched(bundle.fetcher)
    rc, text = download(bundle, root)
    assert rc == 0, text
    again = fetched(bundle.fetcher) - before
    assert sorted(again) == sorted([("A1", "resOriginal"), ("A2", "resOriginal"), ("A3", "resOriginal")])
    rc, text = verify(root)
    assert rc == 0, text
    assert "VERIFIED 15 of 15 files" in last_line(text)


def test_verify_without_manifest_fails(tmp_path):
    rc, text = verify(tmp_path)
    assert rc == 1
    assert last_line(text).startswith("NOT VERIFIED")


def test_verify_quick_and_source_filter(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    assert download(F.make_bundle(cloud), root)[0] == 0
    rows, bad = icloud.read_manifest(str(root))
    (root / "2018").mkdir()
    (root / "2018" / "MAC_0001.JPG").write_bytes(b"from the mac")
    rows.append({"asset_id": "X", "filename": "MAC_0001.JPG", "path": "2018/MAC_0001.JPG", "size": 12,
                 "sha256": None, "created": None, "kind": "photo", "favorite": False, "hidden": False,
                 "albums": [], "source": "mac-photos", "status": "done"})
    icloud.write_manifest(str(root), rows)
    rc, text = verify(root, "--source", "icloud-web", "--quick")
    assert rc == 0 and "VERIFIED 15 of 15 files" in last_line(text)
    rc, text = verify(root)
    assert rc == 0 and "VERIFIED 16 of 16 files" in last_line(text)
    # download keeps rows from other sources as they are
    assert download(F.make_bundle(cloud), root)[0] == 0
    assert by_path(root)["2018/MAC_0001.JPG"]["source"] == "mac-photos"


def test_interrupted_download_resumes_where_it_stopped(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    bundle.fetcher.plans[("A2", "resOriginal")] = ["interrupt"]
    root = tmp_path / "photos"
    rc, text = download(bundle, root, "--workers", "1")
    assert rc == icloud.EXIT_STOPPED, text
    assert "Run the same command again to continue" in text
    rows = dict(((r["asset_id"], r["variant"]), r) for r in manifest(root))
    assert rows[("A1", "original")]["status"] == "done"
    assert rows[("A2", "original")]["status"] != "done"
    assert not (root / "2019/05/IMG_0002.HEIC").exists()
    partial = root / ".homecloud" / "partial"
    assert any((partial / name).stat().st_size > 0 for name in os.listdir(str(partial)))

    rc, text = download(bundle, root)
    assert rc == 0, text
    resumed = [start for a, f, start in bundle.fetcher.requests if (a, f) == ("A2", "resOriginal")]
    assert resumed[0] == 0 and resumed[-1] > 0  # the second request asked only for the rest
    assert (root / "2019/05/IMG_0002.HEIC").read_bytes() == cloud.content("A2", "resOriginal")
    assert fetched(bundle.fetcher)[("A1", "resOriginal")] == 1
    assert os.listdir(str(root / ".homecloud" / "partial")) == []
    assert verify(root)[0] == 0


def test_approval_lapse_pauses_and_the_next_run_resumes(tmp_path, monkeypatch):
    """Advanced Data Protection: an approval lasts about an hour. When it runs out
    mid-download, the run pauses cleanly and the next run goes on from the manifest."""
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    lapse = icloud.CloudError("access", "ACCESS_DENIED: approval expired", code="ACCESS_DENIED")
    cloud = LapsingCloud(F.sample_library(), "records/lookup", 3, lapse, url_lifetime=300)
    asked = []
    bundle = F.make_bundle(cloud, recover_access=lambda exc: asked.append(exc) or False, adp=True)
    root = tmp_path / "photos"

    rc, text = download(bundle, root)
    assert rc == icloud.EXIT_PAUSED, text
    assert "Paused: iCloud needs your approval again." in text
    assert "Approve the request on your iPhone" in text
    assert asked, "the run should first try to get a new approval"
    rows = manifest(root)
    done = [r for r in rows if r["status"] == "done"]
    assert 0 < len(done) < 15
    for row in done:
        assert (root / row["path"]).stat().st_size == row["size"]
    last = json.loads((root / ".homecloud" / "last-download.json").read_text())
    assert last["status"] == "paused"

    cloud.armed = False  # the user tapped Allow
    rc, text = download(bundle, root)
    assert rc == 0, text
    counts = fetched(bundle.fetcher)
    assert sorted(counts) == sorted(all_files(cloud))
    assert set(counts.values()) == {1}, counts  # finished files were not fetched again
    assert verify(root)[0] == 0


def test_approval_given_while_waiting_lets_the_run_go_on(tmp_path, monkeypatch):
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    lapse = icloud.CloudError("access", "ACCESS_DENIED", code="ACCESS_DENIED")
    cloud = LapsingCloud(F.sample_library(), "records/lookup", 3, lapse, times=1, url_lifetime=300)
    asked = []
    bundle = F.make_bundle(cloud, recover_access=lambda exc: asked.append(exc) or True, adp=True)
    rc, text = download(bundle, tmp_path / "photos")
    assert rc == 0, text
    assert len(asked) == 1
    assert verify(tmp_path / "photos")[0] == 0


def test_approval_lapse_while_listing_pauses(tmp_path):
    lapse = icloud.CloudError("access", "ACCESS_DENIED", code="ACCESS_DENIED")
    cloud = LapsingCloud(F.sample_library(), "records/query", 2, lapse)
    rc, text = download(F.make_bundle(cloud, adp=True), tmp_path / "photos")
    assert rc == icloud.EXIT_PAUSED
    assert "needs your approval again" in text
    out = new_out()
    assert icloud.run_scan(F.make_bundle(cloud, adp=True), out) == icloud.EXIT_PAUSED
    assert "needs your approval again" in text_of(out)


def test_web_access_turned_off_mid_run_says_how_to_allow_it(tmp_path, monkeypatch):
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    off = icloud.CloudError("adp_off", "private db access disabled for this account")
    cloud = LapsingCloud(F.sample_library(), "records/lookup", 2, off, url_lifetime=300)
    rc, text = download(F.make_bundle(cloud, adp=True), tmp_path / "photos")
    assert rc == icloud.EXIT_BLOCKED, text
    assert "Allow Data Access" in text and "Access iCloud Data on the Web" in text


def test_session_expiry_pauses_with_login_message(tmp_path, monkeypatch):
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    expired = icloud.CloudError("auth", "AUTHENTICATION_FAILED", status=401)
    cloud = LapsingCloud(F.sample_library(), "records/lookup", 3, expired, url_lifetime=300)
    bundle = F.make_bundle(cloud, recover_auth=lambda: False)
    root = tmp_path / "photos"
    rc, text = download(bundle, root)
    assert rc == icloud.EXIT_LOGIN, text
    assert "python3 scripts/icloud.py login" in text
    assert any(r["status"] == "done" for r in manifest(root))
    cloud.armed = False
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert set(fetched(bundle.fetcher).values()) == {1}


def test_session_renewed_quietly_when_possible(tmp_path, monkeypatch):
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    expired = icloud.CloudError("auth", "AUTHENTICATION_FAILED", status=401)
    cloud = LapsingCloud(F.sample_library(), "records/lookup", 3, expired, times=1, url_lifetime=300)
    renewed = []
    bundle = F.make_bundle(cloud, recover_auth=lambda: renewed.append(1) or True)
    rc, text = download(bundle, tmp_path / "photos")
    assert rc == 0, text
    assert renewed == [1]


def test_expired_download_links_are_renewed(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    bundle.fetcher.plans[("A1", "resOriginal")] = ["410"]
    bundle.fetcher.plans[("A3", "resVidFull")] = ["403"]
    root = tmp_path / "photos"
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert ("private", "records/lookup") in cloud.calls
    assert (root / "2019/05/IMG_0001.HEIC").read_bytes() == cloud.content("A1", "resOriginal")
    assert verify(root)[0] == 0


def test_links_that_expire_during_a_long_listing_are_renewed_first(tmp_path):
    # Links are already expired when the listing ends, as after a long listing.
    cloud = F.FakeICloud(F.sample_library(), url_lifetime=-60, lookup_lifetime=3600)
    bundle = F.make_bundle(cloud)
    rc, text = download(bundle, tmp_path / "photos")
    assert rc == 0, text
    assert ("private", "records/lookup") in cloud.calls
    assert ("shared", "records/lookup") not in cloud.calls  # the Shared Library zone sits in the private database here
    assert set(fetched(bundle.fetcher).values()) == {1}  # no request was wasted on an expired link


def test_retries_throttling_and_dropped_connections(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    bundle.fetcher.plans[("A1", "resOriginal")] = ["500", "500"]
    bundle.fetcher.plans[("A2", "resOriginal")] = ["truncate", "ignore_range"]
    bundle.fetcher.plans[("A3", "resOriginal")] = ["429", "429"]
    bundle.fetcher.plans[("A5", "resOriginalVidCompl")] = ["truncate"]
    root = tmp_path / "photos"
    rc, text = download(bundle, root)
    assert rc == 0, text
    rc, text = verify(root)
    assert rc == 0, text
    for (asset_id, field), path in ((("A2", "resOriginal"), "2019/05/IMG_0002.HEIC"),
                                    (("A5", "resOriginalVidCompl"), "2021/07/IMG_0005.MOV")):
        assert (root / path).read_bytes() == cloud.content(asset_id, field)


def test_a_file_that_keeps_failing_is_listed_and_retried_next_time(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    bundle.fetcher.plans[("A6", "resOriginalAlt")] = ["500"] * 20
    root = tmp_path / "photos"
    rc, text = download(bundle, root)
    assert rc == 1
    assert "1 files could not be downloaded" in text and "2021/07/IMG_0006.DNG" in text
    row = by_path(root)["2021/07/IMG_0006.DNG"]
    assert row["status"] == "failed" and "500" in row["error"]
    rc, text = verify(root)
    assert rc == 1 and "IMG_0006.DNG  (download failed" in text
    bundle.fetcher.plans.clear()
    assert download(bundle, root)[0] == 0
    assert verify(root)[0] == 0


def test_items_gone_from_icloud_before_download_leave_the_list(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    bundle.fetcher.plans[("A6", "resOriginal")] = ["500"] * 20
    bundle.fetcher.plans[("A6", "resOriginalAlt")] = ["500"] * 20
    root = tmp_path / "photos"
    assert download(bundle, root)[0] == 1
    cloud.photos = [p for p in cloud.photos if p.asset_id != "A6"]
    rc, text = download(bundle, root)
    assert rc == 0, text
    assert "taken off the list" in text
    assert not any(r["asset_id"] == "A6" for r in manifest(root))
    assert verify(root)[0] == 0


def test_not_enough_disk_space_stops_before_downloading(tmp_path, monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(icloud.shutil, "disk_usage", lambda path: usage(10_000, 9_000, 1_000))
    cloud = F.FakeICloud(F.sample_library())
    bundle = F.make_bundle(cloud)
    rc, text = download(bundle, tmp_path / "photos")
    assert rc == 1
    assert "Not enough free space" in text
    assert bundle.fetcher.requests == []


def test_no_edited_and_date_filters(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "plain"
    rc, text = download(F.make_bundle(cloud), root, "--no-edited")
    assert rc == 0, text
    assert len(manifest(root)) == 11
    assert not any("_edited" in r["path"] for r in manifest(root))
    assert verify(root)[0] == 0

    root = tmp_path / "range"
    rc, text = download(F.make_bundle(cloud), root, "--since", "2020-01-01", "--until", "2021-12-31")
    assert rc == 0, text
    assert sorted(set(r["asset_id"] for r in manifest(root))) == ["A3", "A4", "A5", "A6"]


def test_refuses_a_folder_that_syncs_to_icloud_drive(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Photos"
    rc, text = download(F.make_bundle(cloud), root)
    assert rc == 1 and "iCloud Drive" in text
    assert not root.exists()
    rc, text = download(F.make_bundle(cloud), root, "--allow-synced-folder")
    assert rc == 0, text


def test_two_runs_cannot_write_the_same_folder(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    root.mkdir()
    with icloud.DirLock(str(root)):
        with pytest.raises(icloud.HomecloudError) as info:
            download(F.make_bundle(cloud), root)
    assert "already running" in str(info.value)


def test_manifest_lines_that_cannot_be_read_are_kept_and_reported(tmp_path):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    assert download(F.make_bundle(cloud), root)[0] == 0
    path = root / ".homecloud" / "manifest.jsonl"
    path.write_text(path.read_text() + "{not json\n")
    rc, text = download(F.make_bundle(cloud), root)
    assert rc == 0, text
    assert "{not json" in path.read_text()
    rc, text = verify(root)
    assert rc == 1 and "could not be read" in text


# --------------------------------------------------------------------------
# Nothing in iCloud is ever deleted or changed
# --------------------------------------------------------------------------

def test_no_delete_or_modify_call_reaches_icloud(tmp_path, monkeypatch):
    """Runs every command path against a fake that fails on anything but the four
    read-only CloudKit calls and on any method it does not know."""
    monkeypatch.setattr(icloud, "LOOKUP_BATCH", 2)
    cloud = StrictCloud(F.sample_library(), url_lifetime=300)
    fetcher = StrictFetcher(cloud)
    fetcher.plans[("A1", "resOriginal")] = ["410", "500", "truncate"]
    fetcher.plans[("A2", "resOriginal")] = ["403"]
    bundle = F.make_bundle(cloud, fetcher=fetcher)
    root = tmp_path / "photos"
    assert icloud.run_scan(bundle, new_out()) == 0
    assert download(bundle, root)[0] == 0
    assert download(bundle, root)[0] == 0
    assert verify(root)[0] == 0
    assert cloud.violations == []
    assert set(path for _, path in cloud.calls) <= set(icloud.CK_READ_PATHS)
    assert ("private", "records/lookup") in cloud.calls


def test_the_request_guard_lets_only_reads_through():
    ok = [
        ("POST", "https://idmsa.apple.com/appleauth/auth/signin"),
        ("POST", "https://setup.icloud.com/setup/ws/1/accountLogin"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/query"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/shared/zones/list"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/lookup"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/internal/records/query/batch"),
        ("GET", "https://cvws.icloud-content.com/B/abc/${f}?o=1&e=2"),
    ]
    for method, url in ok:
        icloud.check_request(method, url)
    blocked = [
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/modify"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/zones/modify"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/assets/upload"),
        ("POST", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/delete"),
        ("GET", "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/query"),
        ("DELETE", "https://cvws.icloud-content.com/B/abc"),
        ("PUT", "https://cvws.icloud-content.com/B/abc"),
        ("POST", "https://setup.icloud.com/setup/ws/1/logout"),
        ("POST", "https://setup.icloud.com/setup/ws/1/repairDone"),
        ("POST", "https://p42-uploadimagews.icloud.com/upload"),
        ("POST", "https://example.com/anything"),
        ("GET", "http://cvws.icloud-content.com/B/abc"),
    ]
    for method, url in blocked:
        with pytest.raises(icloud.ReadOnlyViolation):
            icloud.check_request(method, url)


def test_the_script_never_calls_a_write_method_or_endpoint():
    """Reads the source: no call named like a write, and every CloudKit path is a read."""
    source = open(SCRIPT, encoding="utf-8").read()
    tree = ast.parse(source)
    removers, rmdirs, write_calls = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in icloud.WRITE_METHODS:
                write_calls.append((node.lineno, name))
            assert name not in ("unlink", "rmtree", "removedirs", "truncate"), node.lineno
            if name == "remove":
                removers.append(node.lineno)
            if name == "rmdir":
                rmdirs.append(node.lineno)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.match(r"^(internal/)?(records|zones|assets|subscriptions|changes|users|tokens)/", node.value):
                assert node.value in icloud.CK_READ_PATHS, node.value
    funcs = dict((n.name, n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

    def inside(line, *names):
        return any(funcs[n].lineno <= line <= funcs[n].end_lineno for n in names)

    # The one call named like a write is the sign-out in end_session_at_apple, which
    # only the logout command and the question after a complete verify reach.
    assert [name for _, name in write_calls] == ["logout"], write_calls
    assert all(inside(line, "end_session_at_apple") for line, _ in write_calls)
    # Files are removed only by _discard (temporary files inside .homecloud) and by
    # forget_session (the session key folder).
    assert all(inside(line, "_discard", "forget_session") for line in removers), removers
    assert all(inside(line, "forget_session") for line in rmdirs), rmdirs
    with pytest.raises(RuntimeError):
        icloud._discard(os.path.join(os.sep, "photos", "2020", "01", "IMG_0001.HEIC"))


def test_sign_out_request_is_allowed_only_inside_logout():
    url = "https://setup.icloud.com/setup/ws/1/logout"
    with pytest.raises(icloud.ReadOnlyViolation):
        icloud.check_request("POST", url)
    with icloud.allow_sign_out():
        icloud.check_request("POST", url)
        with pytest.raises(icloud.ReadOnlyViolation):
            icloud.check_request("POST", "https://p42-ckdatabasews.icloud.com/database/1/"
                                         "com.apple.photos.cloud/production/private/records/modify")
    with pytest.raises(icloud.ReadOnlyViolation):
        icloud.check_request("POST", url)


@needs_pyicloud
def test_pyicloud_write_methods_are_blocked():
    blocked = icloud.install_write_blockers()
    assert blocked, "expected pyicloud methods that can change iCloud to be found and blocked"
    import importlib
    for modname in icloud._WRITE_MODULES:
        try:
            module = importlib.import_module(modname)
        except Exception:
            continue
        for cls in vars(module).values():
            if not isinstance(cls, type) or not str(cls.__module__).startswith("pyicloud"):
                continue
            for name in icloud.WRITE_METHODS:
                attr = cls.__dict__.get(name)
                if attr is not None and callable(attr) and not isinstance(attr, property):
                    with pytest.raises(icloud.ReadOnlyViolation):
                        getattr(cls, name)(None)


@needs_pyicloud
def test_pyicloud_errors_are_sorted_the_right_way():
    def kind(exc):
        return icloud.pyicloud_to_cloud_error(exc).kind

    assert kind(PE.PyiCloudServiceNotActivatedException("finish setting up", "AUTHENTICATION_FAILED")) == "auth"
    assert kind(PE.PyiCloudAPIResponseException("Authentication required for Account.", 421)) == "auth"
    assert kind(PE.PyiCloudAPIResponseException("ACCESS_DENIED. Please wait", "ACCESS_DENIED")) == "access"
    assert kind(PE.PyiCloudAPIResponseException("private db access disabled for this account",
                                                "ACCESS_DENIED")) == "adp_off"
    assert kind(PE.PyiCloudPCSTimeoutException("PCS consent timed out")) == "access"
    assert kind(PE.PyiCloudConnectionException("Request failed to iCloud")) == "transient"
    assert kind(PE.PyiCloudAPIResponseException("Service Unavailable", 503)) == "transient"
    assert kind(PE.PyiCloudServiceNotActivatedException("finish setting up", "ZONE_NOT_FOUND")) == "fatal"


@needs_pyicloud
def test_transport_sends_only_reads_to_the_photos_database():
    sent = []

    class Response(object):
        def json(self):
            return {"records": []}

    class Session(object):
        def post(self, url, params=None, json=None, timeout=None):
            sent.append((url, dict(params), json))
            return Response()

    class Photos(object):
        service_endpoint = "https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private"
        params = {"dsid": "123", "remapEnums": True}

    class API(object):
        session = Session()

    transport = icloud.PyiCloudTransport(API(), Photos())
    transport.post_json("shared", "zones/list", {})
    transport.post_json("private", "records/query", {"query": {}})
    assert sent[0][0].endswith("/production/shared/zones/list")
    assert sent[1][0].endswith("/production/private/records/query")
    assert sent[1][1]["dsid"] == "123"
    with pytest.raises(icloud.ReadOnlyViolation):
        transport.post_json("private", "records/modify", {"operations": []})
    assert len(sent) == 2


@needs_requests
def test_requests_cannot_send_a_write_even_by_mistake():
    import requests
    icloud.install_request_guard()
    with pytest.raises(icloud.ReadOnlyViolation):
        requests.Session().post("https://p42-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/"
                                "production/private/records/modify", json={"operations": []})
    with pytest.raises(icloud.ReadOnlyViolation):
        requests.Session().delete("https://cvws.icloud-content.com/B/abc")
    with pytest.raises(icloud.ReadOnlyViolation):
        icloud.HttpFetcher().get("https://example.com/x", {})


@needs_requests
def test_real_http_fetcher_resumes_a_dropped_connection(tmp_path, monkeypatch):
    """The requests-based fetcher against a local server that drops one download
    halfway. The next request asks only for the rest (HTTP Range)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import unquote, urlsplit as split

    seen = []
    dropped = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            token = unquote(split(self.path).path).split("/")[2]
            asset_id, field, _ = token.split("~")
            data = cloud.content(asset_id, field)
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].rstrip("-"))
            seen.append((asset_id, field, start))
            body = data[start:]
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(body)))
            if start:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
            self.end_headers()
            if (asset_id, field) == ("A2", "resOriginal") and not dropped:
                dropped.add(1)
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cloud = F.FakeICloud(F.sample_library(), url_base="http://127.0.0.1:%d" % server.server_port)
        real_check = icloud.check_request

        def check(method, url):
            if url.startswith("http://127.0.0.1:%d/" % server.server_port):
                return
            real_check(method, url)

        monkeypatch.setattr(icloud, "check_request", check)
        monkeypatch.setattr(icloud, "CHUNK", 16)  # small reads, so the half that arrived is kept
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        bundle = F.make_bundle(cloud, fetcher=icloud.HttpFetcher(timeout=(5, 10)))
        root = tmp_path / "photos"
        rc, text = download(bundle, root)
        assert rc == 0, text
        starts = [start for a, f, start in seen if (a, f) == ("A2", "resOriginal")]
        assert starts[0] == 0 and len(starts) == 2 and starts[1] > 0, starts
        assert (root / "2019/05/IMG_0002.HEIC").read_bytes() == cloud.content("A2", "resOriginal")
        assert verify(root)[0] == 0
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def test_commands_run_end_to_end_with_a_stand_in_for_icloud(tmp_path, monkeypatch):
    class StandIn(icloud.CloudBundle):
        def __init__(self, out, approval_wait_minutes=10):
            bundle = F.make_bundle(cloud)
            icloud.CloudBundle.__init__(self, bundle.client, None, cloud.albums, False, None)
            self.name = "someone@example.com"
            self.spare = bundle.fetcher

        def make_fetcher(self):
            self.fetcher = self.spare
            return self.fetcher

    cloud = F.FakeICloud(F.sample_library())
    monkeypatch.setattr(icloud, "RealCloud", StandIn)
    parser = icloud.build_parser()
    out = new_out()
    assert icloud.cmd_scan(parser.parse_args(["scan", "--json", "-"]), out) == 0
    assert json.loads(text_of(out))["total_assets"] == 8
    out = new_out()
    root = tmp_path / "photos"
    assert icloud.cmd_download(parser.parse_args(["download", "--to", str(root)]), out) == 0, text_of(out)
    assert "Signed in as so***@example.com" in text_of(out)
    assert verify(root)[0] == 0
    assert cloud.violations == []


def test_help_works():
    for args in ([], ["download"], ["verify"], ["scan"], ["login"], ["logout"]):
        proc = subprocess.run([sys.executable, SCRIPT] + args + ["--help"], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "usage:" in proc.stdout
    assert "--workers" in subprocess.run([sys.executable, SCRIPT, "download", "--help"],
                                         capture_output=True, text=True).stdout


def test_source_is_python_39_syntax():
    source = open(SCRIPT, encoding="utf-8").read()
    if sys.version_info >= (3, 8):
        ast.parse(source, feature_version=(3, 9))


@pytest.mark.skipif(not shutil.which("python3.9"), reason="no python3.9 on this computer")
def test_python_39_shows_help_and_explains_the_upgrade():
    proc = subprocess.run(["python3.9", SCRIPT, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "download" in proc.stdout
    proc = subprocess.run(["python3.9", SCRIPT, "scan"], capture_output=True, text=True)
    assert proc.returncode == 1 and "Python 3.10 or newer" in proc.stdout


# --------------------------------------------------------------------------
# login (needs pyicloud for its exception types)
# --------------------------------------------------------------------------

class FakeAPI(object):
    def __init__(self, apple_id, codes=("123456",), web_allowed=True, adp=False, signed_in=False):
        self.apple_id = apple_id
        self.codes = list(codes)
        self.signed_in = signed_in
        self.requires_2fa = not signed_in
        self.requires_2sa = False
        self.is_trusted_session = signed_in
        self.data = {"dsInfo": {"appleId": apple_id}}
        self.web_allowed = web_allowed
        self.adp = adp
        self.two_factor_delivery_method = "trusted_device"
        self.security_key_names = []
        self.fido2_devices = []
        self.trusted = 0

    def get_auth_status(self):
        return {"authenticated": self.signed_in, "trusted_session": self.is_trusted_session,
                "requires_2fa": self.requires_2fa, "requires_2sa": False}

    def authenticate(self, force_refresh=False):
        raise PE.PyiCloudFailedLoginException("no saved session")

    def request_2fa_code(self):
        return True

    def validate_2fa_code(self, code):
        if code in self.codes:
            self.requires_2fa = False
            self.signed_in = True
            return True
        return False

    def trust_session(self):
        self.trusted += 1
        self.is_trusted_session = True
        return True

    def _check_pcs_consent(self):
        return {"isWebAccessAllowed": self.web_allowed, "isICDRSDisabled": self.adp,
                "isDeviceConsentedForPCS": True}

    def logout(self, keep_trusted=True, all_sessions=True, clear_local_session=True):
        self.logged_out = {"keep_trusted": keep_trusted, "all_sessions": all_sessions,
                           "clear_local_session": clear_local_session}
        return {"remote_logout_confirmed": True}

    @property
    def photos(self):
        return object()


def login(factory, answers, password="correct horse battery staple"):
    answers = list(answers)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answers.pop(0)

    def ask_secret(prompt):
        asked.append(prompt)
        return password

    out = new_out()
    args = icloud.build_parser().parse_args(["login"])
    rc = icloud.cmd_login(args, service_factory=factory, ask=ask, ask_secret=ask_secret, out=out)
    return rc, text_of(out), asked


def tree_text(folder):
    chunks = []
    for root, _, files in os.walk(str(folder)):
        for name in files:
            with open(os.path.join(root, name), "rb") as fh:
                chunks.append(fh.read())
    return b"\n".join(chunks)


@needs_pyicloud
def test_login_with_code_trusts_the_session_and_never_keeps_the_password(homecloud_home):
    made = []

    def factory(apple_id, password, authenticate=True, **kwargs):
        assert kwargs["cookie_directory"] == icloud.session_dir()
        api = FakeAPI(apple_id)
        made.append((password, api))
        return api

    rc, text, asked = login(factory, ["someone@example.com", "000000", "123456"])
    assert rc == 0, text
    assert "That code did not work. 2 tries left." in text
    assert "Your password was not saved anywhere." in text
    assert "until you run:  python3 scripts/icloud.py logout" in text
    assert "signing in to\nicloud.com in a browser" in text
    assert made[0][1].trusted == 1
    assert "correct horse" not in text
    assert b"correct horse" not in tree_text(homecloud_home)
    assert not any("Password" in p for p in asked[:1])
    if os.name != "nt":
        assert os.stat(icloud.session_dir()).st_mode & 0o777 == 0o700
        for name in os.listdir(icloud.session_dir()):
            assert os.stat(os.path.join(icloud.session_dir(), name)).st_mode & 0o777 == 0o600


@needs_pyicloud
def test_login_wrong_password():
    def factory(apple_id, password, **kwargs):
        raise PE.PyiCloudFailedLoginException("Invalid email/password combination.")

    rc, text, _ = login(factory, ["someone@example.com"])
    assert rc == 1
    assert "did not accept that Apple Account and password" in text
    assert "correct horse" not in text


@needs_pyicloud
def test_login_locked_account():
    def factory(apple_id, password, **kwargs):
        raise PE.PyiCloudAccountLockedException("-20209", None)

    rc, text, _ = login(factory, ["someone@example.com"])
    assert rc == 1
    assert "locked" in text and "iforgot.apple.com" in text


@needs_pyicloud
def test_login_wrong_code_three_times():
    rc, text, _ = login(lambda a, p, **k: FakeAPI(a), ["someone@example.com", "1", "2", "3"])
    assert rc == 1
    assert "2 tries left" in text and "1 try left" in text
    assert "Run login again to get a new code." in text


@needs_pyicloud
def test_login_explains_advanced_data_protection():
    rc, text, _ = login(lambda a, p, **k: FakeAPI(a, web_allowed=False, adp=True),
                        ["someone@example.com", "123456"])
    assert rc == icloud.EXIT_BLOCKED
    assert "Allow Data Access" in text and "Access iCloud Data on the Web" in text


@needs_pyicloud
def test_login_reuses_a_saved_session_without_a_password():
    os.makedirs(icloud.session_dir())
    open(os.path.join(icloud.session_dir(), "someoneexamplecom.session"), "w").close()

    def factory(apple_id, password, authenticate=True, **kwargs):
        assert password is None and authenticate is False
        return FakeAPI("someone@example.com", signed_in=True)

    def no_password(prompt):
        raise AssertionError("asked for a password although a session was saved")

    out = new_out()
    args = icloud.build_parser().parse_args(["login"])
    rc = icloud.cmd_login(args, service_factory=factory, ask=no_password, ask_secret=no_password, out=out)
    assert rc == 0, text_of(out)
    assert "Already signed in as so***@example.com" in text_of(out)


# --------------------------------------------------------------------------
# logout, and the offer to sign out after a complete verify
# --------------------------------------------------------------------------

def save_fake_session(home):
    folder = icloud.session_dir()
    os.makedirs(folder)
    for name in ("someoneexamplecom.session", "someoneexamplecom.cookiejar"):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write("{}")
    icloud.save_account("someone@example.com")
    with open(os.path.join(str(home), "immich-api-key"), "w") as fh:
        fh.write("not ours to delete")


def session_factory(apis, signed_in=True):
    def factory(apple_id, password, authenticate=True, **kwargs):
        assert password is None and authenticate is False
        api = FakeAPI(apple_id, signed_in=signed_in)
        apis.append(api)
        return api
    return factory


def test_logout_ends_the_session_at_apple_and_deletes_the_key(homecloud_home):
    save_fake_session(homecloud_home)
    apis = []
    out = new_out()
    rc = icloud.cmd_logout(icloud.build_parser().parse_args(["logout"]),
                           service_factory=session_factory(apis), out=out)
    text = text_of(out)
    assert rc == 0, text
    assert len(apis) == 1
    assert apis[0].logged_out == {"keep_trusted": False, "all_sessions": False, "clear_local_session": False}
    assert "Signed out of iCloud." in text
    assert "Deleted the session key in %s (3 files)." % icloud.session_dir() in text
    assert not os.path.exists(icloud.session_dir())
    assert (homecloud_home / "immich-api-key").read_text() == "not ours to delete"


def test_logout_when_the_session_had_already_ended(homecloud_home):
    save_fake_session(homecloud_home)
    apis = []
    out = new_out()
    rc = icloud.cmd_logout(None, service_factory=session_factory(apis, signed_in=False), out=out)
    assert rc == 0
    assert "had already ended" in text_of(out)
    assert not hasattr(apis[0], "logged_out")
    assert not os.path.exists(icloud.session_dir())


def test_logout_with_nothing_saved():
    out = new_out()
    assert icloud.cmd_logout(None, service_factory=session_factory([]), out=out) == 0
    assert "nothing to sign out" in text_of(out)


def verify_with_answer(root, answer, *extra):
    asked = []

    def ask(prompt):
        asked.append(prompt)
        if answer is None:
            raise AssertionError("verify asked although it should not")
        return answer

    apis = []
    args = icloud.build_parser().parse_args(["verify", "--to", str(root)] + list(extra))
    out = new_out()
    rc = icloud.cmd_verify(args, out, ask=ask, service_factory=session_factory(apis))
    return rc, text_of(out), asked, apis


def test_verify_offers_to_sign_out_when_everything_is_here(tmp_path, homecloud_home):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    assert download(F.make_bundle(cloud), root)[0] == 0
    save_fake_session(homecloud_home)

    rc, text, asked, apis = verify_with_answer(root, "n")
    assert rc == 0 and asked == [icloud.SIGN_OUT_QUESTION]
    assert "VERIFIED 15 of 15 files" in text and "stays on this computer" in text
    assert os.path.isdir(icloud.session_dir())

    rc, text, asked, apis = verify_with_answer(root, None, "--keep-session")
    assert rc == 0 and asked == []
    assert last_line(text).startswith("VERIFIED 15 of 15 files")

    rc, text, asked, apis = verify_with_answer(root, "")  # Enter means yes
    assert rc == 0 and asked == [icloud.SIGN_OUT_QUESTION]
    assert apis[0].logged_out["keep_trusted"] is False
    assert "Signed out of iCloud." in text
    assert not os.path.exists(icloud.session_dir())
    assert (homecloud_home / "immich-api-key").exists()

    # Nothing left to sign out: no question.
    rc, text, asked, apis = verify_with_answer(root, None)
    assert rc == 0 and asked == []


def test_verify_signs_out_by_itself_when_nobody_can_answer(tmp_path, homecloud_home, monkeypatch):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    assert download(F.make_bundle(cloud), root)[0] == 0
    save_fake_session(homecloud_home)
    monkeypatch.setattr(icloud, "_stdin_is_tty", lambda: False)

    apis = []
    args = icloud.build_parser().parse_args(["verify", "--to", str(root), "--keep-session"])
    out = new_out()
    assert icloud.cmd_verify(args, out, service_factory=session_factory(apis)) == 0
    assert last_line(text_of(out)).startswith("VERIFIED 15 of 15 files")
    assert os.path.isdir(icloud.session_dir())

    args = icloud.build_parser().parse_args(["verify", "--to", str(root)])
    out = new_out()
    assert icloud.cmd_verify(args, out, service_factory=session_factory(apis)) == 0
    text = text_of(out)
    assert "Signed out of iCloud." in text
    assert last_line(text).startswith("VERIFIED 15 of 15 files")
    assert not os.path.exists(icloud.session_dir())
    assert (homecloud_home / "immich-api-key").exists()


def test_verify_does_not_offer_after_a_partial_download(tmp_path, homecloud_home):
    cloud = F.FakeICloud(F.sample_library())
    root = tmp_path / "photos"
    assert download(F.make_bundle(cloud), root, "--limit", "2")[0] == 0
    save_fake_session(homecloud_home)
    rc, text, asked, apis = verify_with_answer(root, None)
    assert rc == 0 and asked == []
    assert os.path.isdir(icloud.session_dir())
