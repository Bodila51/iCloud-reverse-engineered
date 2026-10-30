import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mm = load("make_manifest", "scripts/immich/make_manifest.py")
imp = load("import_library", "scripts/immich/import_library.py")


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def rows_of(folder):
    lines = (folder / ".homecloud" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["path"]: r for r in map(json.loads, lines)}


def test_lists_photos_and_videos_and_pairs_live_photos(tmp_path):
    touch(tmp_path / "IMG_0001.HEIC", b"heic")
    touch(tmp_path / "IMG_0001.MOV", b"live")
    touch(tmp_path / "IMG_0002.JPG")
    touch(tmp_path / "clip.mp4")
    touch(tmp_path / "sub" / "IMG_0003.heic")
    touch(tmp_path / "IMG_0001.AAE")
    touch(tmp_path / "Photo Details.csv")
    touch(tmp_path / ".DS_Store")

    assert mm.main(["--dir", str(tmp_path)]) == 0
    rows = rows_of(tmp_path)
    assert sorted(rows) == ["IMG_0001.HEIC", "IMG_0001.MOV", "IMG_0002.JPG", "clip.mp4", "sub/IMG_0003.heic"]
    assert rows["IMG_0001.MOV"]["kind"] == "live_video"
    assert rows["clip.mp4"]["kind"] == "video"
    assert rows["IMG_0001.HEIC"]["size"] == 4
    assert {r["source"] for r in rows.values()} == {"folder"}

    entries = imp.load_manifest(tmp_path)
    assert len(entries) == 5 and {e.source for e in entries} == {"folder"}
    assert "make_manifest.py" in imp.source_step(entries, 1)

    touch(tmp_path / "IMG_0004.PNG")
    assert mm.main(["--to", str(tmp_path)]) == 0  # rebuilds its own list
    assert len(rows_of(tmp_path)) == 6


def test_leaves_another_scripts_manifest_alone(tmp_path):
    touch(tmp_path / "IMG_0001.HEIC")
    manifest = tmp_path / ".homecloud" / "manifest.jsonl"
    touch(manifest, (json.dumps({"path": "IMG_0001.HEIC", "source": "icloud-web"}) + "\n").encode())
    before = manifest.read_bytes()
    assert mm.main(["--dir", str(tmp_path)]) == 2
    assert manifest.read_bytes() == before


def test_empty_folder_fails(tmp_path):
    touch(tmp_path / "notes.txt")
    assert mm.main(["--dir", str(tmp_path)]) == 1


def test_refuses_icloud_folders_and_photos_libraries(tmp_path):
    icloud = tmp_path / "Pictures" / "iCloud Photos" / "Photos"
    touch(icloud / "IMG_0001.HEIC")
    assert mm.main(["--dir", str(icloud)]) == 2
    assert not (icloud / ".homecloud").exists()

    library = tmp_path / "Photos Library.photoslibrary" / "originals"
    touch(library / "IMG_0002.HEIC")
    assert mm.main(["--dir", str(library)]) == 2
    assert not (library / ".homecloud").exists()
