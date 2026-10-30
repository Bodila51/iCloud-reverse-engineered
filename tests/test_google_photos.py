"""Tests for scripts/google/google_photos.py and its "google-web" rows in import_library.py.
No network: the browser, the clipboard, Immich and GitHub are replaced by stand-ins."""

import collections
import importlib.util
import io
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_SCRIPT = ROOT / "scripts" / "google" / "homecloud_google.js"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gp = load("google_photos", "scripts/google/google_photos.py")
imp = load("import_library", "scripts/immich/import_library.py")

GB = 1000 ** 3
Usage = collections.namedtuple("Usage", "total used free")


@pytest.fixture
def stub_script(tmp_path, monkeypatch):
    """A stand-in for homecloud_google.js that holds the contract's settings line."""
    path = tmp_path / "stub" / "homecloud_google.js"
    path.parent.mkdir()
    path.write_text("// Homecloud stand-in\n" + gp.CONFIG_LINE + "\nconsole.log(HOMECLOUD_CONFIG.folderName);\n",
                    encoding="ascii")
    monkeypatch.setattr(gp, "BROWSER_SCRIPT", path)
    return path


@pytest.fixture
def no_desktop(monkeypatch):
    """prepare must not reach the browser or the clipboard when told not to."""
    def refuse(*args, **kwargs):
        raise AssertionError("opened a browser or the clipboard")
    monkeypatch.setattr(gp, "open_in_browser", refuse)
    monkeypatch.setattr(gp, "copy_to_clipboard", refuse)
    monkeypatch.setattr(gp, "find_browser", refuse)


def settings_of(script_text):
    lines = [line for line in script_text.splitlines() if line.startswith(gp.CONFIG_START)]
    assert len(lines) == 1
    return json.loads(lines[0][len(gp.CONFIG_START):].rstrip(";"))


# --------------------------------------------------------------------------- prepare

def test_prepare_fills_the_settings_line(tmp_path, stub_script, no_desktop, monkeypatch, capsys):
    monkeypatch.setattr(gp.shutil, "disk_usage", lambda path: Usage(500 * GB, 400 * GB, 100 * GB))
    folder = tmp_path / "Google Photos"
    code = gp.main(["prepare", "--to", str(folder), "--no-open", "--no-clipboard", "--lang", "de-DE"])
    out = capsys.readouterr().out
    assert code == 0
    written = (folder / ".homecloud" / "homecloud_google.js").read_text(encoding="ascii")
    assert gp.CONFIG_LINE not in written
    # Margin: 2% of a 500 GB disk is 10 GB, more than the 5 GB minimum.
    assert settings_of(written) == {"version": 1, "freeBytes": 90 * GB, "folderName": "Google Photos", "lang": "de"}
    line = [x for x in written.splitlines() if x.startswith(gp.CONFIG_START)][0]
    assert line == ('const HOMECLOUD_CONFIG = {"version": 1, "freeBytes": 90000000000, '
                    '"folderName": "Google Photos", "lang": "de"};')
    assert written.replace(line, gp.CONFIG_LINE) == stub_script.read_text(encoding="ascii")
    assert "keeps 10.0 GB of it free as a safety margin" in out
    assert "allow pasting" in out and "scammers" in out
    assert "verify --to" in out and "\u2014" not in out
    assert "If the browser says that photos.google.com wants to download multiple files, click Allow." in out
    assert "status and verify unpack the files of each one into the folder" in out
    assert "status --to DIR --wait 100" in gp.__doc__


def test_prepare_keeps_the_script_ascii_for_any_folder_name(tmp_path, stub_script, no_desktop, monkeypatch):
    monkeypatch.setattr(gp.shutil, "disk_usage", lambda path: Usage(100 * GB, 50 * GB, 50 * GB))
    folder = tmp_path / "Fotos de José"
    assert gp.main(["prepare", "--to", str(folder), "--no-open", "--no-clipboard", "--lang", "es"]) == 0
    written = (folder / ".homecloud" / "homecloud_google.js").read_bytes()
    assert written.isascii()
    assert settings_of(written.decode("ascii"))["folderName"] == "Fotos de José"


def test_prepare_stops_when_the_disk_is_nearly_full(tmp_path, stub_script, no_desktop, monkeypatch, capsys):
    monkeypatch.setattr(gp.shutil, "disk_usage", lambda path: Usage(100 * GB, 97 * GB, 3 * GB))
    folder = tmp_path / "copy"
    assert gp.main(["prepare", "--to", str(folder), "--no-open", "--no-clipboard"]) == 1
    assert "less than the 5.0 GB" in capsys.readouterr().out
    assert not (folder / ".homecloud" / "homecloud_google.js").exists()


def test_prepare_refuses_a_folder_the_browser_cannot_pick(tmp_path, stub_script, no_desktop, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert gp.main(["prepare", "--to", str(home / "Documents"), "--no-open", "--no-clipboard"]) == 2
    assert "Documents folder itself" in capsys.readouterr().out


def test_prepare_refuses_a_folder_with_another_copy(tmp_path, stub_script, no_desktop, capsys):
    folder = tmp_path / "icloud"
    (folder / ".homecloud").mkdir(parents=True)
    (folder / ".homecloud" / "manifest.jsonl").write_text(json.dumps({"source": "icloud-web"}) + "\n")
    assert gp.main(["prepare", "--to", str(folder), "--no-open", "--no-clipboard"]) == 2
    assert "icloud-web" in capsys.readouterr().out


def test_prepare_opens_the_first_installed_browser_or_says_what_to_install(tmp_path, stub_script, monkeypatch,
                                                                          capsys):
    monkeypatch.setattr(gp.shutil, "disk_usage", lambda path: Usage(100 * GB, 50 * GB, 50 * GB))
    installed = {"brave": "/Apps/Brave Browser.app", "vivaldi": "/Apps/Vivaldi.app"}
    opened, copied = [], []
    monkeypatch.setattr(gp, "browser_target", lambda key: installed.get(key))
    monkeypatch.setattr(gp, "open_in_browser", lambda target, url: opened.append((target, url)) or True)
    monkeypatch.setattr(gp, "copy_to_clipboard", lambda text: copied.append(text) or True)
    folder = tmp_path / "copy"

    assert gp.main(["prepare", "--to", str(folder), "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert opened == [("/Apps/Brave Browser.app", "https://photos.google.com")]
    assert copied == [(folder / ".homecloud" / "homecloud_google.js").read_text(encoding="ascii")]
    assert "brave://flags/#file-system-access-api" in out and "copied to your clipboard" in out

    assert gp.main(["prepare", "--to", str(folder), "--browser", "edge"]) == 1
    assert "Microsoft Edge is not installed on this computer. Installed: Brave, Vivaldi." in capsys.readouterr().out

    installed.clear()
    other = tmp_path / "other"
    assert gp.main(["prepare", "--to", str(other)]) == 1
    assert "Install Google Chrome from https://www.google.com/chrome/" in capsys.readouterr().out
    assert not (other / ".homecloud" / "homecloud_google.js").exists()
    assert len(opened) == 1 and len(copied) == 1


@pytest.mark.skipif(not REAL_SCRIPT.is_file(), reason="scripts/google/homecloud_google.js is not in this checkout")
def test_prepare_with_the_real_browser_script(tmp_path, no_desktop, monkeypatch):
    template = REAL_SCRIPT.read_text(encoding="utf-8")
    assert template.isascii()
    assert template.count(gp.CONFIG_LINE) == 1
    monkeypatch.setattr(gp.shutil, "disk_usage", lambda path: Usage(100 * GB, 50 * GB, 50 * GB))
    folder = tmp_path / "Google Photos"
    assert gp.main(["prepare", "--to", str(folder), "--no-open", "--no-clipboard", "--lang", "en"]) == 0
    written = (folder / ".homecloud" / "homecloud_google.js").read_text(encoding="ascii")
    assert settings_of(written)["freeBytes"] == 45 * GB


# ---------------------------------------------------------------------------- status

def write_progress(folder, state, **extra):
    data = {"state": state, "listed": 10, "done": 4, "failed": 1, "bytesDone": 400, "bytesTotal": 1000,
            "message": "", "updated": "2026-10-30T12:00:00.000Z"}
    data.update(extra)
    (folder / ".homecloud").mkdir(parents=True, exist_ok=True)
    (folder / ".homecloud" / "google-progress.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("state, code", [
    (None, 4), ("listing", 4), ("downloading", 4), ("done", 0), ("failed", 1), ("stopped", 1)])
def test_status_exit_codes(tmp_path, capsys, state, code):
    tmp_path.joinpath(".homecloud").mkdir()
    if state:
        write_progress(tmp_path, state, message="Stopped at https://lh3.googleusercontent.com/secret=d")
    assert gp.main(["status", "--to", str(tmp_path)]) == code
    out = capsys.readouterr().out
    assert "googleusercontent" not in out
    if state == "done":
        assert "verify --to" in out


def test_status_without_a_folder_is_a_usage_error(tmp_path):
    assert gp.main(["status", "--to", str(tmp_path / "nothing")]) == 2


def test_status_wait_polls_every_ten_seconds_until_done(tmp_path, monkeypatch, capsys):
    write_progress(tmp_path, "downloading")
    clock = {"now": 1000.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds
        if len(clock["sleeps"]) == 3:
            write_progress(tmp_path, "done", done=9)

    monkeypatch.setattr(gp, "_clock", lambda: clock["now"])
    monkeypatch.setattr(gp, "_sleep", sleep)
    assert gp.main(["status", "--to", str(tmp_path), "--wait", "600"]) == 0
    assert clock["sleeps"] == [10, 10, 10]
    out = capsys.readouterr().out
    assert "Downloading: 4 of 10 items (40%)" in out and "Done: 9 of 10 items saved" in out


def test_status_wait_ends_at_the_limit_while_running(tmp_path, monkeypatch, capsys):
    write_progress(tmp_path, "downloading")
    clock = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(gp, "_clock", lambda: clock["now"])
    monkeypatch.setattr(gp, "_sleep", sleep)
    assert gp.main(["status", "--to", str(tmp_path), "--wait", "25"]) == 4
    assert clock["sleeps"] == [10, 10, 5]
    assert "Not finished after 25 seconds" in capsys.readouterr().out


# ---------------------------------------------------------------------------- verify

T2019 = 1556970000000  # 2019-05-04 11:40:00 UTC
T2020 = 1577836800000  # 2020-01-01 00:00:00 UTC
T2021 = 1609459200000  # 2021-01-01 00:00:00 UTC


def item(key, taken, name, path, size, kind="photo", **extra):
    data = {"mediaKey": key, "takenTime": taken, "fileName": name, "size": size, "kind": kind, "live": False,
            "originalQuality": True, "favorite": False, "archived": False, "albums": [], "path": path,
            "livePath": None}
    data.update(extra)
    return data


ITEMS = [
    item("AAA", T2019, "IMG_0001.HEIC", "2019/2019-05/IMG_0001.HEIC", 4, live=True, favorite=True,
         albums=["Trip"], livePath="2019/2019-05/IMG_0001.MOV"),
    item("BBB", T2020, "VID_0002.MP4", "2020/2020-01/VID_0002.MP4", 10, kind="video", archived=True,
         originalQuality=False),
    item("CCC", T2021, "IMG_0003.JPG", "2021/2021-01/IMG_0003.JPG", None, albums=["Trip", "Cats"]),
    item("DDD", T2021, "IMG_0003.JPG", "2021/2021-01/IMG_0003 (2).JPG", 5),
]
SIZES = {"2019/2019-05/IMG_0001.HEIC": 4, "2019/2019-05/IMG_0001.MOV": 7, "2020/2020-01/VID_0002.MP4": 10,
         "2021/2021-01/IMG_0003.JPG": 16, "2021/2021-01/IMG_0003 (2).JPG": 5}
DONE = [
    {"mediaKey": "AAA", "path": "2019/2019-05/IMG_0001.HEIC", "size": 4,
     "livePath": "2019/2019-05/IMG_0001.MOV", "liveSize": 7},
    {"mediaKey": "BBB", "error": "HTTP 500"},  # failed first, then finished on the second run
    {"mediaKey": "BBB", "path": "2020/2020-01/VID_0002.MP4", "size": 10, "livePath": None, "liveSize": None},
    {"mediaKey": "CCC", "path": "2021/2021-01/IMG_0003.JPG", "size": 16, "livePath": None, "liveSize": None},
    {"mediaKey": "DDD", "path": "2021/2021-01/IMG_0003 (2).JPG", "size": 5, "livePath": None, "liveSize": None},
]


def make_copy(folder, items=ITEMS, done=DONE, sizes=SIZES):
    state = folder / ".homecloud"
    state.mkdir(parents=True, exist_ok=True)
    listing = {"format": "homecloud-google-list", "version": 1, "created": "2026-10-30T09:00:00.000Z",
               "method": "stand-in", "items": items,
               "albums": [{"title": "Trip", "count": 2}, {"title": "Cats", "count": 1}],
               "totals": {"count": len(items), "bytes": sum(i["size"] or 0 for i in items)}}
    (state / "google-list.json").write_text(json.dumps(listing), encoding="utf-8")
    (state / "google-done.jsonl").write_text("".join(json.dumps(r) + "\n" for r in done), encoding="utf-8")
    write_progress(folder, "done", listed=len(items), done=len(items), failed=0)
    for rel, size in sizes.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return folder


def run_verify(folder, capsys):
    code = gp.main(["verify", "--to", str(folder)])
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1].startswith("Result:")  # the contract: the last line
    return code, out


def result_line(out):
    return [line for line in out.splitlines() if line.startswith("Result:")][-1]


def test_verify_passes_sets_dates_and_writes_the_manifest(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy")
    code, out = run_verify(folder, capsys)
    assert code == 0
    assert result_line(out) == "Result: OK. 4 of 4 items are on disk with the right size."
    assert "Saved at Google in Storage saver: 1 item." in out
    assert "Live Photo video parts: 1 of 1" in out

    for rel, taken in [("2019/2019-05/IMG_0001.HEIC", T2019), ("2019/2019-05/IMG_0001.MOV", T2019),
                       ("2020/2020-01/VID_0002.MP4", T2020), ("2021/2021-01/IMG_0003 (2).JPG", T2021)]:
        assert os.stat(folder / rel).st_mtime_ns == taken * 1_000_000

    entries = {e.path: e for e in imp.load_manifest(folder)}
    assert len(entries) == 5
    assert {e.source for e in entries.values()} == {"google-web"}
    assert {e.status for e in entries.values()} == {"done"}
    heic, mov = entries["2019/2019-05/IMG_0001.HEIC"], entries["2019/2019-05/IMG_0001.MOV"]
    assert (heic.kind, mov.kind, entries["2020/2020-01/VID_0002.MP4"].kind) == ("photo", "live_video", "video")
    assert heic.favorite and heic.albums == ["Trip"] and not heic.hidden
    assert mov.filename == "IMG_0001.MOV" and mov.size == 7
    assert entries["2020/2020-01/VID_0002.MP4"].hidden and not entries["2020/2020-01/VID_0002.MP4"].favorite
    assert entries["2021/2021-01/IMG_0003.JPG"].albums == ["Cats", "Trip"]
    assert entries["2021/2021-01/IMG_0003.JPG"].size == 16  # the list had no size; the browser's record does
    assert entries["2021/2021-01/IMG_0003 (2).JPG"].filename == "IMG_0003.JPG"
    assert "google_photos.py verify --to DIR" in imp.source_step(list(entries.values()), 1)
    rows = [json.loads(x) for x in (folder / ".homecloud" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["asset_id"] for r in rows} == {"google:AAA", "google:BBB", "google:CCC", "google:DDD"}
    assert rows[0]["created"] == "2019-05-04T11:40:00+00:00"

    # A second run changes no dates and writes the same manifest.
    before = (folder / ".homecloud" / "manifest.jsonl").read_bytes()
    code, out = run_verify(folder, capsys)
    assert code == 0 and "Dates:" not in out
    assert (folder / ".homecloud" / "manifest.jsonl").read_bytes() == before


def test_verify_fails_on_a_missing_file(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy")
    (folder / "2020/2020-01/VID_0002.MP4").unlink()
    code, out = run_verify(folder, capsys)
    assert code == 1
    assert result_line(out).startswith("Result: NOT COMPLETE. 3 of 4 items")
    assert "VID_0002.MP4, taken 2020-01-01, should be at 2020/2020-01/VID_0002.MP4" in out
    entries = {e.path: e for e in imp.load_manifest(folder)}
    assert entries["2020/2020-01/VID_0002.MP4"].status == "missing"
    assert entries["2019/2019-05/IMG_0001.HEIC"].status == "done"


def test_verify_fails_on_a_short_file(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy")
    (folder / "2019/2019-05/IMG_0001.HEIC").write_bytes(b"xy")
    code, out = run_verify(folder, capsys)
    assert code == 1
    assert "only 2 of 4 bytes, an unfinished download" in out
    assert {e.path: e for e in imp.load_manifest(folder)}["2019/2019-05/IMG_0001.HEIC"].status == "failed"
    assert "Result: NOT COMPLETE" in result_line(out)
    assert (folder / ".homecloud" / "google-problems.txt").is_file()


def test_verify_fails_on_a_missing_live_photo_video_part(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy")
    (folder / "2019/2019-05/IMG_0001.MOV").unlink()
    code, out = run_verify(folder, capsys)
    assert code == 1
    assert "IMG_0001.MOV, the video part of IMG_0001.HEIC" in out
    entries = {e.path: e for e in imp.load_manifest(folder)}
    assert entries["2019/2019-05/IMG_0001.MOV"].status == "missing"


def test_verify_reports_browser_errors_without_links(tmp_path, capsys):
    done = DONE[:4] + [{"mediaKey": "DDD", "error": "Download failed: https://video.googleusercontent.com/abc?token=1"}]
    sizes = dict(SIZES)
    del sizes["2021/2021-01/IMG_0003 (2).JPG"]
    folder = make_copy(tmp_path / "copy", done=done, sizes=sizes)
    code, out = run_verify(folder, capsys)
    assert code == 1
    assert "IMG_0003.JPG, taken 2021-01-01: Download failed: (link left out)" in out
    assert "googleusercontent" not in out and "token" not in out
    entries = {e.path: e for e in imp.load_manifest(folder)}
    assert entries["2021/2021-01/IMG_0003 (2).JPG"].status == "failed"


def test_verify_refuses_another_sources_manifest(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy")
    manifest = folder / ".homecloud" / "manifest.jsonl"
    manifest.write_text(json.dumps({"path": "IMG_0001.HEIC", "source": "icloud-web"}) + "\n", encoding="utf-8")
    before = manifest.read_bytes()
    mtime = os.stat(folder / "2019/2019-05/IMG_0001.HEIC").st_mtime_ns
    code, out = run_verify(folder, capsys)
    assert code == 2
    assert "icloud-web" in out and result_line(out).startswith("Result: NOT CHECKED")
    assert manifest.read_bytes() == before
    assert os.stat(folder / "2019/2019-05/IMG_0001.HEIC").st_mtime_ns == mtime


def test_verify_without_a_list_is_not_complete(tmp_path, capsys):
    (tmp_path / ".homecloud").mkdir()
    code, out = run_verify(tmp_path, capsys)
    assert code == 1 and result_line(out).startswith("Result: NOT COMPLETE")
    assert not (tmp_path / ".homecloud" / "manifest.jsonl").exists()


def test_import_library_knows_the_google_source():
    copy_step, verify_step = imp.SOURCE_STEPS["google-web"]
    assert verify_step == "python3 scripts/google/google_photos.py verify --to DIR"
    assert "google_photos.py prepare --to DIR" in copy_step


def test_verify_accepts_a_live_photo_saved_without_its_video_part(tmp_path, capsys):
    done = [dict(DONE[0], livePath=None, liveSize=None, liveError="The browser could not fetch the file.")] + DONE[1:]
    sizes = dict(SIZES)
    del sizes["2019/2019-05/IMG_0001.MOV"]
    folder = make_copy(tmp_path / "copy", done=done, sizes=sizes)
    code, out = run_verify(folder, capsys)
    assert code == 0
    assert result_line(out) == "Result: OK. 4 of 4 items are on disk with the right size."
    assert "Live Photos saved without their video part, because it did not download: 1." in out
    assert "IMG_0001.HEIC, taken 2019-05-04: The browser could not fetch the file." in out
    assert {e.path for e in imp.load_manifest(folder)} == set(sizes)


# ------------------------------------------------------------- zip files in Downloads

def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def write_batch(folder, batch_id, keys, zips):
    """A batch file as the browser script writes it, for items of ITEMS."""
    by_key = {i["mediaKey"]: i for i in ITEMS}
    items = [{"mediaKey": k, "fileName": by_key[k]["fileName"], "size": by_key[k]["size"], "kind": by_key[k]["kind"],
              "live": by_key[k]["live"], "path": by_key[k]["path"], "livePath": by_key[k]["livePath"]} for k in keys]
    base = folder / ".homecloud" / "google-batches"
    base.mkdir(parents=True, exist_ok=True)
    data = {"format": "homecloud-google-batch", "version": 1, "batch": batch_id, "created": "2026-10-30T10:00:00.000Z",
            "zips": [{"name": name, "size": len(data)} for name, data in zips], "items": items}
    (base / (batch_id + ".json")).write_text(json.dumps(data), encoding="utf-8")


def collected_lines(folder):
    path = folder / ".homecloud" / "google-collected.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()] if path.exists() else []


def test_status_moves_zip_files_from_downloads_and_deletes_them(tmp_path, capsys):
    # The page fetched DDD itself; the rest came as zip files in the Downloads folder.
    folder = make_copy(tmp_path / "copy", done=[DONE[4]], sizes={"2021/2021-01/IMG_0003 (2).JPG": 5})
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    first = zip_bytes([("Google Photos/IMG_0001.HEIC", b"a" * 4), ("Google Photos/IMG_0001.MOV", b"m" * 7),
                       ("VID_0002.MP4", b"v" * 10), ("IMG_0003.JPG", b"c" * 16)])
    second = zip_bytes([("IMG_0003.JPG", b"x" * 5)])
    write_batch(folder, "20261030T100000-0001", ["AAA", "BBB", "CCC"], [("Photos-001.zip", first)])
    write_batch(folder, "20261030T100000-0002", ["DDD"], [("Photos-001.zip", second)])
    (downloads / "Photos-001.zip").write_bytes(first)
    (downloads / "Photos-001 (1).zip").write_bytes(second)
    (downloads / "Photos-002.zip.crdownload").write_bytes(first)  # still downloading: left alone
    (downloads / "notes.txt").write_bytes(b"n" * len(first))  # same size, not a zip of a batch

    assert gp.main(["status", "--to", str(folder), "--downloads", str(downloads)]) == 0
    out = capsys.readouterr().out
    # DDD is in place already, with the same size: its zip file counts as used and goes too.
    assert f"Moved 4 files from 2 downloads in the Downloads folder into {folder}." in out
    assert "Zip files from the browser: 2 of 2 moved into the folder." in out
    assert sorted(p.name for p in downloads.iterdir()) == ["Photos-002.zip.crdownload", "notes.txt"]
    assert (folder / "2019/2019-05/IMG_0001.MOV").read_bytes() == b"m" * 7
    assert (folder / "2021/2021-01/IMG_0003.JPG").read_bytes() == b"c" * 16
    assert (folder / "2021/2021-01/IMG_0003 (2).JPG").read_bytes() == b"x" * 5
    lines = collected_lines(folder)
    assert {"mediaKey": "AAA", "path": "2019/2019-05/IMG_0001.HEIC", "size": 4,
            "livePath": "2019/2019-05/IMG_0001.MOV", "liveSize": 7} in lines
    assert {"batch": "20261030T100000-0001", "part": 0, "files": 4} in lines
    assert {"batch": "20261030T100000-0002", "part": 0, "files": 1} in lines
    assert not [p for p in folder.rglob("*") if p.name.endswith(gp.PIECE_SUFFIX)]

    # verify reads the moved items from google-collected.jsonl; a second look moves nothing.
    code, out = run_verify(folder, capsys)
    assert code == 0 and result_line(out) == "Result: OK. 4 of 4 items are on disk with the right size."
    assert "Moved" not in out and "Live Photo video parts: 1 of 1" in out


def test_collect_checks_sizes_and_keeps_a_zip_with_other_files(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy", done=[], sizes={})
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    data = zip_bytes([("IMG_0001.HEIC", b"a" * 4), ("IMG_0001.MOV", b"m" * 7), ("VID_0002.MP4", b"v" * 9),
                      ("IMG_0001-edited.HEIC", b"e" * 3)])
    write_batch(folder, "b1", ["AAA", "BBB"], [("Photos-001.zip", data)])
    (downloads / "Photos-001.zip").write_bytes(data)
    assert gp.main(["status", "--to", str(folder), "--downloads", str(downloads)]) == 0
    out = capsys.readouterr().out
    assert "VID_0002.MP4: the zip file holds 9 bytes, Google lists 10. It was not moved" in out
    assert "Left in the Downloads folder: Photos-001.zip." in out
    assert (downloads / "Photos-001.zip").exists()
    assert (folder / "2019/2019-05/IMG_0001.HEIC").exists() and not (folder / "2020/2020-01/VID_0002.MP4").exists()
    code, out = run_verify(folder, capsys)
    assert code == 1 and "VID_0002.MP4, taken 2020-01-01, should be at" in out


def test_collect_moves_a_video_that_came_as_a_plain_file(tmp_path, capsys):
    folder = make_copy(tmp_path / "copy", done=[], sizes={})
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    write_batch(folder, "b1", ["BBB"], [("VID_0002.MP4", b"v" * 10)])
    (downloads / "VID_0002 (1).MP4").write_bytes(b"v" * 10)
    assert gp.main(["status", "--to", str(folder), "--downloads", str(downloads)]) == 0
    assert "Moved 1 file from 1 download" in capsys.readouterr().out
    assert list(downloads.iterdir()) == []
    assert (folder / "2020/2020-01/VID_0002.MP4").read_bytes() == b"v" * 10
    assert collected_lines(folder)[0]["mediaKey"] == "BBB"


def test_status_keeps_waiting_while_zip_files_are_still_coming(tmp_path, monkeypatch, capsys):
    folder = make_copy(tmp_path / "copy", done=[], sizes={})
    now = gp.datetime.datetime.now(gp.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    write_progress(folder, "done", listed=4, done=0, failed=0, updated=now)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    write_batch(folder, "b1", ["BBB"], [("VID_0002.MP4", b"v" * 10)])
    clock = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds
        if len(clock["sleeps"]) == 2:
            (downloads / "VID_0002.MP4").write_bytes(b"v" * 10)

    monkeypatch.setattr(gp, "_clock", lambda: clock["now"])
    monkeypatch.setattr(gp, "_sleep", sleep)
    assert gp.main(["status", "--to", str(folder), "--wait", "100", "--downloads", str(downloads)]) == 0
    assert clock["sleeps"] == [10, 10]
    assert "Zip files from the browser: 1 of 1 moved into the folder." in capsys.readouterr().out

    # A zip file that never comes: status says so at the end of the wait, and exits with 4 while
    # the page's run ended only minutes ago.
    write_batch(folder, "b2", ["CCC"], [("Photos-001.zip", b"z" * 30)])
    clock["sleeps"].clear()
    assert gp.main(["status", "--to", str(folder), "--wait", "20", "--downloads", str(downloads)]) == 4
    assert "The browser is still saving the rest in the Downloads folder." in capsys.readouterr().out
    write_progress(folder, "done", listed=4, done=0, failed=0)  # a run that ended long ago
    assert gp.main(["status", "--to", str(folder), "--downloads", str(downloads)]) == 0
    assert "1 zip file did not arrive in the Downloads folder." in capsys.readouterr().out


def test_downloads_folders_on_each_system(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Telechargements").mkdir()
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Telechargements"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(gp.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(gp, "is_windows", lambda: False)
    monkeypatch.setattr(gp.sys, "platform", "linux")
    assert gp.downloads_folders() == [(home / "Telechargements").resolve(), (home / "Downloads").resolve()]
    monkeypatch.setattr(gp.sys, "platform", "darwin")
    assert gp.downloads_folders() == [(home / "Downloads").resolve()]
    moved = tmp_path / "D" / "Downloads"
    moved.mkdir(parents=True)
    monkeypatch.setattr(gp, "is_windows", lambda: True)
    monkeypatch.setattr(gp.sys, "platform", "win32")
    monkeypatch.setattr(gp, "windows_downloads", lambda: moved)
    assert gp.downloads_folders() == [moved.resolve(), (home / "Downloads").resolve()]
    assert gp.downloads_folders([str(tmp_path / "nothing"), str(moved)]) == [moved.resolve()]


# --------------------------------------------------------------------------- takeout

def fake_archive(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Takeout/Google Photos/Photos from 2020/IMG_0001.JPG", b"jpeg")
        zf.writestr("Takeout/Google Photos/Photos from 2020/IMG_0001.JPG.json", b"{}")


def test_takeout_prints_the_steps(tmp_path, capsys):
    assert gp.main(["takeout", "--to", str(tmp_path / "takeout"), "--no-open"]) == 0
    out = capsys.readouterr().out
    assert "Deselect all" in out and ".zip" in out and "--import" in out
    assert (tmp_path / "takeout").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="the stand-in for immich-go is a shell script")
def test_takeout_import_gives_the_key_to_immich_go_only_in_its_environment(tmp_path, monkeypatch, capsys):
    secret = "hc-secret-key-1234567890"
    key_file = tmp_path / "immich-api-key"
    key_file.write_text(secret + "\n", encoding="utf-8")
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    fake_archive(folder / "takeout-20261030T000000Z-002.zip")

    tool = tmp_path / "tool"
    tool.mkdir()
    record = tmp_path / "record.json"
    (tool / "record.py").write_text(
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': {{k: v for k, v in os.environ.items() "
        f"if k.startswith('IMMICH_GO_')}}}}, open({str(record)!r}, 'w'))\n", encoding="utf-8")
    exe = tool / "immich-go"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tool / "record.py"}" "$@"\n', encoding="utf-8")
    exe.chmod(0o755)

    monkeypatch.delenv("IMMICH_API_KEY", raising=False)
    monkeypatch.setenv("IMMICH_GO_UPLOAD_DRY_RUN", "true")  # left over from another run: must not leak in
    monkeypatch.setattr(gp, "check_immich", lambda url, key: None)
    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: 0)
    monkeypatch.setattr(gp, "install_immich_go", lambda asset=None: exe)
    code = gp.main(["takeout", "--to", str(folder), "--import", "--yes", "--key-file", str(key_file),
                    "--app-dir", str(tmp_path / "no-immich-here")])
    out = capsys.readouterr().out
    assert code == 0, out
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["env"] == {"IMMICH_GO_UPLOAD_API_KEY": secret}
    assert secret not in " ".join(seen["argv"]) and secret not in out
    assert seen["argv"][:3] == ["upload", "from-google-photos", "--server=http://localhost:2283"]
    assert "--include-unmatched" in seen["argv"] and "--pause-immich-jobs=false" in seen["argv"]
    assert seen["argv"][-2:] == [str(folder / "takeout-20261030T000000Z-001.zip"),
                                 str(folder / "takeout-20261030T000000Z-002.zip")]
    assert Path(seen["cwd"]) == tool
    assert "Result: OK" in out


def test_takeout_import_asks_first_and_changes_nothing_without_a_yes(tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "immich-api-key"
    key_file.write_text("hc-secret\n", encoding="utf-8")
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    monkeypatch.setattr(gp, "check_immich", lambda url, key: None)
    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: 0)

    def no_install(asset=None):
        raise AssertionError("installed immich-go without a yes")

    monkeypatch.setattr(gp, "install_immich_go", no_install)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code = gp.main(["takeout", "--to", str(folder), "--import", "--key-file", str(key_file),
                    "--app-dir", str(tmp_path)])
    assert code == 2
    assert "Nothing was changed" in capsys.readouterr().out


def test_takeout_import_refuses_an_immich_that_already_holds_photos(tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "immich-api-key"
    key_file.write_text("hc-secret\n", encoding="utf-8")
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    monkeypatch.setattr(gp, "check_immich", lambda url, key: None)
    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: 42)

    def no_install(asset=None):
        raise AssertionError("ran immich-go on an Immich that holds photos")

    monkeypatch.setattr(gp, "install_immich_go", no_install)
    code = gp.main(["takeout", "--to", str(folder), "--import", "--yes", "--key-file", str(key_file),
                    "--app-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "already holds 42 photos and videos" in out and "deletes the photo in Immich" in out
    assert not (folder / ".homecloud" / gp.TAKEOUT_MARKER).exists()


def test_takeout_import_goes_on_after_its_own_first_run(tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "immich-api-key"
    key_file.write_text("hc-secret\n", encoding="utf-8")
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    calls = []
    monkeypatch.setattr(gp, "check_immich", lambda url, key: None)
    monkeypatch.setattr(gp, "install_immich_go", lambda asset=None: tmp_path / "immich-go")
    monkeypatch.setattr(gp, "run_immich_go", lambda exe, url, key, archives: calls.append(url) or 0)

    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: 0)
    args = ["takeout", "--to", str(folder), "--import", "--yes", "--key-file", str(key_file),
            "--app-dir", str(tmp_path)]
    assert gp.main(args) == 0
    marker = json.loads((folder / ".homecloud" / gp.TAKEOUT_MARKER).read_text(encoding="utf-8"))
    assert marker["immichAssetsBefore"] == 0

    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: 7)
    assert gp.main(args) == 0
    assert len(calls) == 2
    assert "only what this import added" in capsys.readouterr().out


def test_takeout_import_stops_when_immich_does_not_give_a_count(tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "immich-api-key"
    key_file.write_text("hc-secret\n", encoding="utf-8")
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    monkeypatch.setattr(gp, "check_immich", lambda url, key: None)
    monkeypatch.setattr(gp, "immich_asset_count", lambda url, key: None)
    assert gp.main(["takeout", "--to", str(folder), "--import", "--yes", "--key-file", str(key_file),
                    "--app-dir", str(tmp_path)]) == 1
    assert "did not say how many" in capsys.readouterr().out


def test_takeout_import_finds_a_cut_off_archive_and_a_missing_part(tmp_path, capsys):
    folder = tmp_path / "takeout"
    folder.mkdir()
    fake_archive(folder / "takeout-20261030T000000Z-001.zip")
    (folder / "takeout-20261030T000000Z-003.zip").write_bytes(b"PK\x03\x04 cut off")
    assert gp.main(["takeout", "--to", str(folder), "--import", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "takeout-20261030T000000Z-003.zip is cut off" in out and "002" in out


def tar_gz_with(name, data):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for member, content in (("LICENSE", b"MIT"), (name, data)):
            info = tarfile.TarInfo(member)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_install_immich_go_checks_the_sha256(monkeypatch, homecloud_home):
    asset = "immich-go_Linux_x86_64.tar.gz"
    good = tar_gz_with("immich-go", b"#!/bin/sh\necho immich-go\n")
    files = {gp.IMMICH_GO_CHECKSUMS: f"{gp.sha256_hex(good)}  {asset}\n".encode(), asset: good}
    monkeypatch.setattr(gp, "fetch", lambda url, timeout=120: files[url.rsplit("/", 1)[1]])
    monkeypatch.setitem(gp.IMMICH_GO_SHA256, asset, gp.sha256_hex(good))

    exe = gp.install_immich_go(asset)
    assert exe.read_bytes() == b"#!/bin/sh\necho immich-go\n"
    assert exe.parent == homecloud_home / "tools" / "immich-go-v0.32.0"
    assert sorted(p.name for p in exe.parent.iterdir()) == ["immich-go", "immich-go.sha256"]

    # A download that does not match the release's checksums.txt installs nothing.
    exe.unlink()
    files[asset] = tar_gz_with("immich-go", b"something else")
    with pytest.raises(gp.Stop, match="does not match its SHA-256"):
        gp.install_immich_go(asset)
    assert not exe.exists()

    # Nor does a checksums.txt that differs from the value this kit pins.
    monkeypatch.setitem(gp.IMMICH_GO_SHA256, asset, "0" * 64)
    files[asset] = good
    with pytest.raises(gp.Stop, match="not the one this kit"):
        gp.install_immich_go(asset)
    assert not exe.exists()


def test_immich_go_build_names():
    assert gp.immich_go_asset("darwin", "arm64") == "immich-go_Darwin_arm64.tar.gz"
    assert gp.immich_go_asset("windows", "x86_64") == "immich-go_Windows_x86_64.zip"
    assert set(gp.IMMICH_GO_SHA256) == {gp.immich_go_asset(s, c) for s in ("darwin", "windows", "linux")
                                        for c in ("arm64", "x86_64")}
