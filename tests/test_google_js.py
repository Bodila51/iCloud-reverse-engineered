"""Checks for scripts/google/homecloud_google.js, the browser half of the Google Photos path.

The scenarios run the script in tests/google_js/harness.mjs, a small fake photos.google.com
page for Node.js 22: a fake batchexecute that answers in GPTK's format and checks each
request, fake media hosts, and a folder handle that writes into a temporary folder. They
are skipped when Node.js 22 or newer is not installed.
"""

import collections
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "google" / "homecloud_google.js"
GOOGLE_PY = ROOT / "scripts" / "google" / "google_photos.py"
HARNESS = Path(__file__).resolve().parent / "google_js" / "harness.mjs"

CONFIG_LINE = 'const HOMECLOUD_CONFIG = {"version": 1, "freeBytes": null, "folderName": null, "lang": "en"};'
CONFIG_START = "const HOMECLOUD_CONFIG = "
GB = 1000 ** 3
READ_ONLY = {"lcxiM", "Z5xsfc", "snAcKc", "EWgK9e", "fDcn4b", "VrseUb", "yCLA7", "dnv2s"}
# Every other request id in GPTK src/api/api.ts at 6db9ab35: the ones that change something
# (trash, archive, favorite, albums, sharing, Locked Folder, dates, places, descriptions) and
# the reads this script has no use for (Trash, Locked Folder, links, quota, search).
OTHER_RPCS = {"XwAOJf", "ycV3Nd", "OXvT9d", "E1Cajb", "laUYf", "QD9nKf", "Ftfh0", "w7TP3c", "StLnCe", "Pp2Xxe",
              "LjmOue", "V8RKJ", "Es7fke", "EtUHOe", "DaSgWe", "AQNOFd", "zy0IHe", "nMFwOc", "F2A0H", "e9T5je",
              "EzkLib", "swbisb", "EzwWhf", "pLFTfd"}

OPEN_TEXT = "The Homecloud panel is open at the bottom right of the page."
STOPPED_TEXT = "Stopped. Click Start to continue. Files already saved stay in the folder."
LIST_KEYS = ["format", "version", "created", "method", "items", "albums", "totals"]
ITEM_KEYS = ["mediaKey", "takenTime", "fileName", "size", "kind", "live", "originalQuality", "favorite", "archived",
             "albums", "path", "livePath"]
DONE_KEYS = ["mediaKey", "path", "size", "livePath", "liveSize"]
ERROR_KEYS = ["mediaKey", "error"]
PROGRESS_KEYS = ["state", "listed", "done", "failed", "bytesDone", "bytesTotal", "message", "updated"]

PATHS = {
    "K09": "2024/2024-07/IMG_0001.MOV",
    "K01": "2024/2024-07/IMG_0001 (2).HEIC",
    "K02": "2024/2024-07/IMG_0001.HEIC",
    "K11": "2024/2024-07/img_0001 (3).heic",
    "K03": "2024/2024-06/VID_20240630.mp4",
    "K04": "2023/2023-12/archived.jpg",
    "K06": "2023/2023-08/a_b_.jpg",
    "K12": "2023/2023-07/_con.jpg",
    "K13": "2023/2023-06/note_txt_1.jpg",
    "K05": "2023/2023-05/edited.jpg",
    "K07": "2023/2023-04/from-fdcn4b.png",
    "K08": "2023/2023-03/saver.jpg",
    "K10": "1999/1999-12/old.jpg",
}
LIVE_PATHS = {"K01": "2024/2024-07/IMG_0001 (2).MOV"}
ALBUMS = {"K01": ["Family", "Trip"], "K03": ["Trip"], "K04": ["Family"]}
GOOGLE_ALBUMS = [{"title": "Family", "count": 3}, {"title": "Trip", "count": 2}]
USE_DIR_NAME = object()


def find_node() -> Optional[str]:
    for candidate in (os.environ.get("HOMECLOUD_NODE"), shutil.which("node"), "/opt/node22/bin/node"):
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            version = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.match(r"v(\d+)\.", version.strip())
        if match and int(match.group(1)) >= 22:
            return candidate
    return None


NODE = find_node()
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js 22 or newer is not installed")


def script_text() -> str:
    return SCRIPT.read_bytes().decode("ascii")


def filled_script(where: Path, free_bytes, folder_name) -> Path:
    """The script with its settings line filled in, the way google_photos.py prepare does it."""
    settings = {"version": 1, "freeBytes": free_bytes, "folderName": folder_name, "lang": "en"}
    text = script_text()
    assert text.count(CONFIG_LINE) == 1
    out = where / "homecloud_google.filled.js"
    out.write_text(text.replace(CONFIG_LINE, CONFIG_START + json.dumps(settings, ensure_ascii=True) + ";", 1),
                   encoding="ascii")
    return out


def run_page(scenario: str, folder: Path, run: int = 1, free_bytes=10 * GB, folder_name=USE_DIR_NAME) -> dict:
    name = folder.name if folder_name is USE_DIR_NAME else folder_name
    script = filled_script(folder.parent, free_bytes, name)
    env = dict(os.environ, TZ="Asia/Tokyo")
    proc = subprocess.run([NODE, str(HARNESS), "--scenario", scenario, "--dir", str(folder), "--run", str(run),
                           "--script", str(script)], capture_output=True, text=True, timeout=240, env=env)
    assert proc.returncode == 0, proc.stderr[-4000:]
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    summary["stderr"] = proc.stderr
    return summary


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_time(value) -> datetime.datetime:
    assert isinstance(value, str) and value.endswith("Z"), value
    return datetime.datetime.fromisoformat(value[:-1] + "+00:00")


def check_rel(path) -> None:
    assert isinstance(path, str) and path == path.strip() and path
    assert "\\" not in path and ":" not in path and not path.startswith("/")
    parts = path.split("/")
    assert len(parts) == 3 and parts[0] != ".homecloud"
    assert all(part not in ("", ".", "..") for part in parts)


def read_list(folder: Path) -> dict:
    data = json.loads((folder / ".homecloud" / "google-list.json").read_text(encoding="utf-8"))
    assert list(data) == LIST_KEYS
    assert data["format"] == "homecloud-google-list" and data["version"] == 1
    parse_time(data["created"])
    assert isinstance(data["method"], str) and data["method"]
    for item in data["items"]:
        assert list(item) == ITEM_KEYS
        assert isinstance(item["mediaKey"], str) and item["mediaKey"]
        assert is_int(item["takenTime"])
        assert isinstance(item["fileName"], str) and item["fileName"]
        assert item["size"] is None or (is_int(item["size"]) and item["size"] >= 0)
        assert item["kind"] in ("photo", "video")
        for key in ("live", "favorite", "archived"):
            assert isinstance(item[key], bool)
        assert item["originalQuality"] is None or isinstance(item["originalQuality"], bool)
        assert isinstance(item["albums"], list) and all(isinstance(a, str) for a in item["albums"])
        check_rel(item["path"])
        when = datetime.datetime.fromtimestamp(item["takenTime"] / 1000, tz=datetime.timezone.utc)
        assert item["path"].startswith("%04d/%04d-%02d/" % (when.year, when.year, when.month))
        if item["live"]:
            check_rel(item["livePath"])
            assert item["livePath"].rsplit(".", 1)[0] == item["path"].rsplit(".", 1)[0]
        else:
            assert item["livePath"] is None
    for album in data["albums"]:
        assert list(album) == ["title", "count"] and isinstance(album["title"], str) and is_int(album["count"])
    assert list(data["totals"]) == ["count", "bytes"]
    assert data["totals"]["count"] == len(data["items"]) and is_int(data["totals"]["bytes"])
    return data


def read_done(folder: Path) -> List[dict]:
    path = folder / ".homecloud" / "google-done.jsonl"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    records = []
    for line in text.splitlines():
        record = json.loads(line)
        if "error" in record:
            assert list(record) == ERROR_KEYS and isinstance(record["error"], str) and record["error"]
        else:
            assert list(record) == DONE_KEYS
            check_rel(record["path"])
            assert is_int(record["size"]) and record["size"] > 0
            if record["livePath"] is None:
                assert record["liveSize"] is None
            else:
                check_rel(record["livePath"])
                assert is_int(record["liveSize"]) and record["liveSize"] > 0
        records.append(record)
    return records


def last_done(folder: Path) -> Dict[str, dict]:
    """The last line for each item, as google_photos.py verify reads it."""
    return {record["mediaKey"]: record for record in read_done(folder)}


def read_progress(folder: Path) -> dict:
    data = json.loads((folder / ".homecloud" / "google-progress.json").read_text(encoding="utf-8"))
    assert list(data) == PROGRESS_KEYS
    assert data["state"] in ("listing", "downloading", "done", "failed", "stopped")
    for key in ("listed", "done", "failed", "bytesDone", "bytesTotal"):
        assert is_int(data[key]) and data[key] >= 0
    assert isinstance(data["message"], str) and data["message"]
    parse_time(data["updated"])
    return data


def files_on_disk(folder: Path) -> Dict[str, Path]:
    found = {}
    for base, dirs, files in os.walk(folder):
        rel = Path(base).relative_to(folder).as_posix()
        if rel == ".homecloud":
            dirs[:] = []
            continue
        for name in files:
            found[(Path(rel) / name).as_posix() if rel != "." else name] = Path(base) / name
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_clean(summary: dict) -> None:
    """Only read requests in GPTK's format, no other hosts, no HTML sinks, no account data."""
    assert summary["protocolErrors"] == [], summary["stderr"][-3000:]
    assert set(summary["rpc"]) <= READ_ONLY
    assert summary["nonGoogle"] == []
    assert summary["sinks"] == []
    assert summary["unhandled"] == []
    assert summary["accountRead"] is False and summary["raptRead"] is False
    assert summary["joinedAlbumRead"] is False
    assert summary["running"] is False
    assert summary["returned"] == OPEN_TEXT
    assert "SECRET-LINK" not in summary["stderr"] and "AT-TOKEN" not in summary["stderr"]


def assert_complete(folder: Path, summary: dict, data: dict, keys, expected: Optional[dict] = None) -> None:
    """Every item of the list is on disk, with Google's bytes, and google-done.jsonl says so."""
    expected = expected or summary["expected"]
    done = last_done(folder)
    want_files = {}
    for item in data["items"]:
        key = item["mediaKey"]
        record = done[key]
        assert "error" not in record
        assert record["path"] == item["path"] and record["livePath"] == item["livePath"]
        want_files[item["path"]] = expected[key]["orig"]
        assert record["size"] == expected[key]["orig"]["size"]
        if item["live"]:
            want_files[item["livePath"]] = expected[key]["live"]
            assert record["liveSize"] == expected[key]["live"]["size"]
        else:
            assert record["liveSize"] is None
    assert {item["mediaKey"] for item in data["items"]} == set(keys)
    on_disk = files_on_disk(folder)
    assert set(on_disk) == set(want_files)
    for rel, want in want_files.items():
        assert on_disk[rel].stat().st_size == want["size"], rel
        assert sha256(on_disk[rel]) == want["sha256"], rel
    assert sorted(p.name for p in (folder / ".homecloud").iterdir()) == [
        "google-done.jsonl", "google-list.json", "google-progress.json"]
    progress = read_progress(folder)
    assert progress["state"] == "done"
    assert progress["listed"] == progress["done"] == len(data["items"]) and progress["failed"] == 0
    assert progress["bytesDone"] == progress["bytesTotal"] == data["totals"]["bytes"]


def assert_items(data: dict, summary: dict, paths: Dict[str, str]) -> None:
    library = summary["library"]
    for item in data["items"]:
        key = item["mediaKey"]
        facts = library[key]
        assert item["path"] == paths[key]
        assert item["livePath"] == LIVE_PATHS.get(key)
        for field in ("fileName", "takenTime", "kind", "live", "favorite", "archived", "originalQuality"):
            assert item[field] == facts[field], (key, field)
        assert item["albums"] == ALBUMS.get(key, [])
        if item["live"] and summary["liveRule"] != "exact":
            assert item["size"] is None
        else:
            assert item["size"] == facts["googleSize"]
    assert data["albums"] == GOOGLE_ALBUMS
    assert data["totals"]["bytes"] == sum(library[i["mediaKey"]]["googleSize"] for i in data["items"])


def fetches(summary: dict, outcome: Optional[str] = "ok") -> collections.Counter:
    return collections.Counter((m["key"], m["variant"]) for m in summary["media"]
                               if outcome is None or m["outcome"] == outcome)


def verify(folder: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOMECLOUD_HOME=str(tmp_path / "homecloud-home"))
    return subprocess.run([sys.executable, str(GOOGLE_PY), "verify", "--to", str(folder)], capture_output=True,
                          text=True, timeout=120, env=env)


def assert_verify_ok(folder: Path, tmp_path: Path, count: int) -> None:
    if not GOOGLE_PY.exists():
        return
    proc = verify(folder, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last = proc.stdout.strip().splitlines()[-1]
    assert last.startswith("Result: OK. %d of %d items" % (count, count)), proc.stdout


@pytest.fixture
def folder(tmp_path) -> Path:
    path = tmp_path / "Google Photos"
    path.mkdir()
    return path


# ------------------------------------------------------------------ the file itself

def test_script_is_ascii_and_holds_the_settings_line_once():
    text = script_text()
    assert text.count(CONFIG_LINE) == 1
    assert CONFIG_LINE in text.splitlines()


def test_settings_line_is_the_one_google_photos_py_fills():
    if not GOOGLE_PY.exists():
        pytest.skip("scripts/google/google_photos.py is not there")
    spec = importlib.util.spec_from_file_location("google_photos_for_js_checks", GOOGLE_PY)
    gp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gp)
    assert gp.CONFIG_LINE == CONFIG_LINE
    template = script_text()
    filled = gp.fill_settings(template, 123456789, "Google Photos", "en")
    settings = {"version": 1, "freeBytes": 123456789, "folderName": "Google Photos", "lang": "en"}
    assert filled == template.replace(CONFIG_LINE, CONFIG_START + json.dumps(settings, ensure_ascii=True) + ";")


def test_no_html_sinks_no_eval_and_only_read_requests():
    text = script_text()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   "Function(", "setAttribute('style'", "createContextualFragment", "srcdoc"):
        assert banned not in text, banned
    for rpcid in OTHER_RPCS:
        assert rpcid not in text, rpcid
    block = re.search(r"const READ_ONLY_RPCS = \{(.*?)\};", text, re.S).group(1)
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == READ_ONLY
    used = set(re.findall(r"makeApiRequest\('(\w+)'", text))
    assert used == READ_ONLY
    assert len(re.findall(r"\bfetch\(", text)) == 2
    assert "credentials: 'include'" in text and "method: 'POST'" in text
    code = text[text.index("\n{\n"):]
    code = re.sub(r"(^|\s)//[^\n]*", r"\1", code)
    assert set(re.findall(r"https?://[^\s'\"]*", code)) == {"https://photos.google.com"}
    for word in re.findall(r"\b(?:un)?test(?:ed|ing)?\b(?!\()", text, re.I):
        pytest.fail("wording about testing: " + word)
    assert "rapt" not in code.replace("rapt (Dbw5Ud)", "")
    assert "oPEP7c" not in code.replace("account (oPEP7c)", "")


def test_header_credits_gptk_with_its_commit_and_license():
    head = script_text().split("\n{\n", 1)[0]
    assert "https://github.com/xob0t/Google-Photos-Toolkit" in head
    assert "6db9ab355e4797327e4078cf87a2b6994d36cf73" in head
    assert "Copyright (c) 2024 xob0t" in head
    assert "Permission is hereby granted, free of charge" in head
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in head


@needs_node
def test_node_check_passes():
    proc = subprocess.run([NODE, "--check", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------------ the fake page

@needs_node
def test_full_library_with_albums_archive_and_a_live_photo(folder, tmp_path):
    s = run_page("full", folder)
    assert_clean(s)
    assert s["state"] == "done"
    assert s["message"] == s["statusText"] == "Done. All 13 items are in the folder. Go back to Claude Code."
    # 4 library pages and one 429 retry, 2 album pages, 2 + 3 album item pages (never the album of
    # another owner), one batch for names and sizes, one item without them, one info per item.
    assert s["rpc"] == {"lcxiM": 5, "Z5xsfc": 2, "snAcKc": 5, "EWgK9e": 1, "fDcn4b": 1, "VrseUb": 13}
    assert s["picker"] == [{"id": "homecloud-google", "mode": "readwrite"}]
    assert s["wake"] == {"requested": 1, "released": 1} and s["locks"] == 1
    assert "Click Start" in s["firstText"] and '"Google Photos"' in s["firstText"]

    data = read_list(folder)
    assert data["method"] == "item-url, credentials include"
    assert_items(data, s, PATHS)
    assert_complete(folder, s, data, PATHS)
    # Each file once, the original of the edited photo, and the video again after its 503.
    want = collections.Counter({(key, "orig"): 1 for key in PATHS})
    want[("K03", "orig")] = 2
    want[("K01", "live")] = 1
    assert fetches(s, None) == want
    assert {m["cred"] for m in s["media"]} == {"include"}
    assert_verify_ok(folder, tmp_path, 13)


@needs_node
def test_size_check_stops_before_any_download(folder):
    s = run_page("nospace", folder, free_bytes=300000)
    assert_clean(s)
    assert s["state"] == "failed"
    assert s["message"].startswith("Not enough free space. Your photos and videos need about 400 KB, but only "
                                   "300 KB may be used on this disk.")
    assert "Nothing was downloaded." in s["message"] and "tell Claude Code" in s["message"]
    assert s["media"] == []
    assert not {"VrseUb", "yCLA7", "dnv2s"} & set(s["rpc"])
    data = read_list(folder)
    assert data["method"] == "none" and len(data["items"]) == 13
    assert files_on_disk(folder) == {}
    assert read_done(folder) == []
    progress = read_progress(folder)
    assert progress["state"] == "failed" and progress["done"] == 0 and progress["listed"] == 13


@needs_node
def test_blocked_item_urls_fall_back_to_the_zip_route(folder, tmp_path):
    s = run_page("cors", folder)
    assert_clean(s)
    assert s["state"] == "done"
    data = read_list(folder)
    assert data["method"] == "zip, credentials include"
    assert_items(data, s, PATHS)
    assert_complete(folder, s, data, PATHS)
    # The first photo, video and Live Photo find the route; the rest go straight to it.
    assert s["blocked"] <= 16
    assert s["rpc"]["VrseUb"] == 3 and s["rpc"]["yCLA7"] == 13
    assert fetches(s) == collections.Counter({(key, "zip"): 1 for key in PATHS})
    assert_verify_ok(folder, tmp_path, 13)


@needs_node
def test_credentials_blocked_falls_back_to_omit(folder, tmp_path):
    s = run_page("omit", folder)
    assert_clean(s)
    assert s["state"] == "done"
    data = read_list(folder)
    assert data["method"] == "item-url, credentials omit"
    assert_complete(folder, s, data, PATHS)
    assert s["blocked"] == 6
    assert {m["cred"] for m in s["media"] if m["outcome"] == "ok"} == {"omit"}
    assert "yCLA7" not in s["rpc"]


@needs_node
def test_nothing_works_stops_with_a_plain_message(folder):
    s = run_page("broken", folder)
    assert_clean(s)
    assert s["state"] == "failed"
    assert s["message"].startswith("Downloads do not work from this page. Photos did not download (")
    assert "Nothing was changed in Google Photos. Tell Claude Code that the download failed. It can offer Google " \
           "Takeout instead." in s["message"]
    assert len(s["message"]) <= 300
    assert {m["outcome"] for m in s["media"]} == {"blocked"}
    assert files_on_disk(folder) == {}
    assert all("error" in r for r in read_done(folder))
    assert read_list(folder)["method"] == "none"
    assert read_progress(folder)["state"] == "failed"


@needs_node
def test_second_run_resumes_and_keeps_paths(folder, tmp_path):
    first = run_page("resume", folder, run=1)
    assert_clean(first)
    assert first["state"] == "stopped" and first["message"] == STOPPED_TEXT
    listed = read_list(folder)
    assert listed["method"] == "item-url, credentials include"
    assert {i["mediaKey"]: i["path"] for i in listed["items"]} == PATHS
    done1 = {k for k, r in last_done(folder).items() if "error" not in r}
    assert {"K02", "K09", "K01"} <= done1 and "K10" not in done1
    assert read_progress(folder)["state"] == "stopped"
    assert not any(p.endswith(".crswap") for p in files_on_disk(folder))
    for key in done1:
        assert (folder / PATHS[key]).stat().st_size == first["expected"][key]["orig"]["size"]

    # A cut Live Photo and a lost line in google-done.jsonl.
    with open(folder / PATHS["K01"], "r+b") as fh:
        fh.truncate(100)
    done_file = folder / ".homecloud" / "google-done.jsonl"
    lines = done_file.read_text(encoding="utf-8").splitlines(True)
    done_file.write_text("".join(line for line in lines if '"K09"' not in line), encoding="utf-8")

    # Google now lists K20, and no longer K02 (finished) or K10 (never downloaded).
    second = run_page("resume", folder, run=2)
    assert_clean(second)
    assert second["state"] == "done"
    paths = dict(PATHS, K20="2024/2024-07/IMG_0001 (4).HEIC")
    del paths["K10"]
    data = read_list(folder)
    assert {i["mediaKey"]: i["path"] for i in data["items"]} == paths
    # K02 is no longer listed, so its bytes are the ones of the first run.
    assert_complete(folder, second, data, paths, dict(first["expected"], **second["expected"]))
    got = fetches(second)
    assert got[("K02", "orig")] == 0 and got[("K09", "orig")] == 0
    assert got[("K01", "orig")] == 1 and got[("K01", "live")] == 1
    assert got[("K20", "orig")] == 1
    for key in done1 - {"K01"}:
        assert got[(key, "orig")] == 0, key
    for key in set(paths) - done1 - {"K02", "K09"}:
        assert got[(key, "orig")] == 1, key
    k02 = next(i for i in data["items"] if i["mediaKey"] == "K02")
    assert k02["size"] == 5000 and k02["fileName"] == "IMG_0001.HEIC"
    k01 = next(i for i in data["items"] if i["mediaKey"] == "K01")
    assert k01["size"] is None and second["liveRule"] == "both"
    assert not (folder / PATHS["K10"]).exists()
    assert_verify_ok(folder, tmp_path, len(paths))


@needs_node
def test_no_connection_says_so(folder):
    s = run_page("offline", folder)
    assert_clean(s)
    assert s["state"] == "failed"
    assert s["message"] == ("This page cannot reach Google right now. Check your internet connection, then click "
                            "Start. It continues where it stopped.")
    assert s["offlineTries"] == 6
    assert sorted(p.name for p in (folder / ".homecloud").iterdir()) == ["google-progress.json"]
    assert files_on_disk(folder) == {}


@needs_node
def test_wrong_folder_writes_nothing(folder):
    s = run_page("wrongfolder", folder, folder_name="Another folder")
    assert s["statusText"] == ('You picked the folder "Google Photos". Click Start again and pick the folder named '
                               '"Another folder".')
    assert s["rpc"] == {} and s["media"] == [] and s["state"] is None
    assert not (folder / ".homecloud").exists()
