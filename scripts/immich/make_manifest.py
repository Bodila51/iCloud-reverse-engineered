#!/usr/bin/env python3
"""Make the list of files that import_library.py needs, for a folder that no other
Homecloud script made: a copy of the iCloud Photos folder from iCloud for Windows,
or the unpacked archives of Apple's data copy from privacy.apple.com.

    python3 scripts/immich/make_manifest.py --dir DIR

It reads DIR and writes DIR/.homecloud/manifest.jsonl, one line per photo or video.
It does not change, move or delete any photo or video. Run it again after you add
files: it rebuilds the list. Then import as usual:

    python3 scripts/immich/import_library.py check --dir DIR

On Windows, use py instead of python3. Python 3.9 or newer, no extra packages.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

SOURCE = "folder"
PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".tif", ".tiff", ".webp", ".avif",
              ".jxl", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".mts", ".m2ts", ".webm"}

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def say(text=""):
    print(text, flush=True)


def human_bytes(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def walk(root: Path):
    """Every file under root, skipping hidden folders and files such as .homecloud."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.startswith("."):
                yield Path(folder) / name


def build(root: Path):
    media, skipped = [], {}
    for path in walk(root):
        ext = path.suffix.lower()
        if ext in PHOTO_EXTS or ext in VIDEO_EXTS:
            media.append(path)
        else:
            skipped[ext or "(no extension)"] = skipped.get(ext or "(no extension)", 0) + 1

    # A video next to a photo with the same name is the video part of a Live Photo.
    photo_stems = {(p.parent, p.stem.lower()) for p in media if p.suffix.lower() in PHOTO_EXTS}
    rows = []
    for path in media:
        ext = path.suffix.lower()
        if ext in PHOTO_EXTS:
            kind = "photo"
        elif (path.parent, path.stem.lower()) in photo_stems:
            kind = "live_video"
        else:
            kind = "video"
        st = path.stat()
        rel = path.relative_to(root).as_posix()
        rows.append({
            "asset_id": f"{SOURCE}:{rel}",
            "filename": path.name,
            "path": rel,
            "size": st.st_size,
            "sha256": None,
            "created": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(),
            "kind": kind,
            "favorite": False,
            "hidden": False,
            "albums": [],
            "source": SOURCE,
            "status": "done",
        })
    return rows, skipped


def existing_sources(manifest: Path):
    sources = set()
    with manifest.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    sources.add(json.loads(line).get("source"))
                except (ValueError, AttributeError):
                    sources.add(None)
    return sources


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="List the photos and videos in a folder for import_library.py (iCloud for Windows "
                    "and Apple's data copy). Changes no photo or video.")
    parser.add_argument("--dir", "--to", dest="dir", required=True,
                        help="the folder with the copy (DIR); --to works too")
    args = parser.parse_args(argv)

    root = Path(os.path.expanduser(args.dir)).resolve()
    if not root.is_dir():
        say(f"No folder at {root}.")
        return EXIT_USAGE
    parts = [part.lower() for part in root.parts]
    if any(part in ("icloud photos", "icloudphotos", "iclouddrive", "mobile documents") or part.endswith(".photoslibrary")
           for part in parts):
        say(f"{root} is inside an iCloud folder or a Photos library. Homecloud never writes there, because a "
            "change there can reach iCloud. Copy the photos into another folder first, then run this on that folder.")
        return EXIT_USAGE
    manifest = root / ".homecloud" / "manifest.jsonl"
    if manifest.is_file():
        others = existing_sources(manifest) - {SOURCE}
        if others:
            say(f"{manifest} was written by another Homecloud script ({', '.join(sorted(map(str, others)))}). "
                "This script leaves it alone. Use the verify command of that script instead.")
            return EXIT_USAGE

    rows, skipped = build(root)
    if not rows:
        say(f"No photos or videos found in {root}.")
        return EXIT_FAIL

    manifest.parent.mkdir(exist_ok=True)
    tmp = manifest.with_name(manifest.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, manifest)

    kinds = {k: sum(1 for r in rows if r["kind"] == k) for k in ("photo", "video", "live_video")}
    say(f"Listed {len(rows):,} files ({human_bytes(sum(r['size'] for r in rows))}): {kinds['photo']:,} photos, "
        f"{kinds['video']:,} videos, {kinds['live_video']:,} Live Photo video parts.")
    if skipped:
        say("Left out, not photos or videos: " + ", ".join(f"{n:,} {ext}" for ext, n in sorted(skipped.items())) + ".")
    say(f"Wrote {manifest}")
    say("Next: python3 scripts/immich/import_library.py check --dir DIR")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
