"""Get a recording ready to send: copy it into parts small enough for GitHub (max 100 MB a file).

    uv run python -m engineer.ingest.share            # newest recording in data/sessions/
    uv run python -m engineer.ingest.share <file.zst>

Writes data/share/<name>.zst.part01, .part02, ... and prints the git commands to push them to
their own branch. The parts are plain byte slices; join them back with `--join`:

    uv run python -m engineer.ingest.share --join data/share/<name>.zst.part01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engineer.config import load_config

PART_BYTES = 90 * 1024 * 1024


def split(path: Path, out_dir: Path, part_bytes: int = PART_BYTES) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{path.name}.part*"):
        old.unlink()
    parts = []
    with open(path, "rb") as src:
        n = 1
        while chunk := src.read(part_bytes):
            part = out_dir / f"{path.name}.part{n:02d}"
            part.write_bytes(chunk)
            parts.append(part)
            n += 1
    return parts


def join(first_part: Path) -> Path:
    stem = first_part.name.rsplit(".part", 1)[0]
    parts = sorted(first_part.parent.glob(f"{stem}.part*"))
    out = first_part.parent / stem
    with open(out, "wb") as dst:
        for p in parts:
            dst.write(p.read_bytes())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.ingest.share")
    ap.add_argument("file", nargs="?", help="recording to share (default: the newest)")
    ap.add_argument("--join", action="store_true", help="join parts back into one file")
    args = ap.parse_args(argv)

    if args.join:
        print(f"Joined: {join(Path(args.file))}")
        return 0
    cfg = load_config()
    if args.file:
        path = Path(args.file)
    else:
        recordings = sorted(cfg.sessions_path.glob("*.zst"), key=lambda p: p.stat().st_mtime)
        if not recordings:
            print(f"No recordings in {cfg.sessions_path}")
            return 1
        path = recordings[-1]
    size_mb = path.stat().st_size / 1e6
    parts = split(path, cfg.data_path / "share")
    branch = "recording-" + path.stem.split("_")[0].replace(":", "").lower()
    print(f"Recording: {path.name} ({size_mb:.0f} MB) -> {len(parts)} part(s) in data/share/\n")
    print("Now run these, one line at a time:\n")
    print(f"  git switch -c {branch}")
    print("  git add -f data/share")
    print(f'  git commit -m "Add recording {path.name}"')
    print(f"  git push -u origin {branch}")
    print("  git switch main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
