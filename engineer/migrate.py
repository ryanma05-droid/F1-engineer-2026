"""Bring the data from running the engineer in a terminal (a git clone of the project) into the
installed app, so switching to the installer doesn't start you from zero.

    RaceEngineer.exe --import "C:\\Users\\you\\F1-engineer-2026"

The installed app's first run also looks for such a folder in your home folder (and Documents,
Desktop) and offers to bring it over.

What comes over: everything in the folder's data/ (recordings, tyre and strategy history, track
maps, driver reports and their cache, push-to-talk log and speech model, the voice, the feedback
log) and its config.local.yaml settings. Files the app already has are never overwritten, so it's
safe to run again. The old folder is left as it is.

Uploads: the installed app has no git clone of its own, so `upload.repo` is set to the old folder
(when it is one) and recordings keep going up through it with your own git login.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

# data/ folders not worth copying: half-finished upload parts and fetched recordings.
SKIP_DIRS = {"incoming", "share"}
KEEP_IN_SKIPPED = {("share", "uploaded.txt")}     # which recordings were already uploaded


def is_project_folder(path: Path) -> bool:
    return (path / "engineer" / "cli.py").exists() and (path / "data").is_dir()


def find_project_folders(home: Path | None = None) -> list[Path]:
    """Clones of the project in the usual places, with recordings first."""
    home = home or Path.home()
    found = []
    for base in (home, home / "Documents", home / "Desktop", home / "source" / "repos"):
        try:
            found += [d for d in base.iterdir() if d.is_dir() and is_project_folder(d)]
        except OSError:
            continue
    return sorted(set(found), key=lambda d: -recording_count(d))


def recording_count(folder: Path) -> int:
    return len(list((folder / "data" / "sessions").glob("*.zst")))


def _files_to_copy(src_data: Path):
    for f in sorted(src_data.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src_data)
        top = rel.parts[0] if len(rel.parts) > 1 else None
        if top in SKIP_DIRS and tuple(rel.parts) not in KEEP_IN_SKIPPED:
            continue
        yield f, rel


def import_folder(src: Path, app_home: Path, progress=print) -> dict:
    """Copy src/data into app_home/data (never overwriting) and merge src/config.local.yaml into
    app_home/config.local.yaml. Returns what was done."""
    src = Path(src)
    if not is_project_folder(src):
        raise ValueError(f"{src} doesn't look like a race-engineer folder (no engineer/ and data/)")
    dest_data = app_home / "data"
    copied = skipped = 0
    size = 0
    for f, rel in _files_to_copy(src / "data"):
        dest = dest_data / rel
        if dest.exists():
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        copied += 1
        size += f.stat().st_size
        if f.suffix == ".zst":
            progress(f"  {rel.as_posix()}")

    local = app_home / "config.local.yaml"
    mine = _read_yaml(local)
    theirs = _read_yaml(src / "config.local.yaml")
    theirs.pop("data_dir", None)           # the app keeps its data in its own folder
    merged = _merge(theirs, mine)          # settings already in the app win
    if (src / ".git").exists():
        merged.setdefault("upload", {})["repo"] = str(src)
    app_home.mkdir(parents=True, exist_ok=True)
    local.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return {"copied": copied, "skipped": skipped, "mb": round(size / 1e6),
            "recordings": recording_count(src), "upload_repo": merged.get("upload", {}).get("repo"),
            "upload_auto": bool(merged.get("upload", {}).get("auto"))}


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
