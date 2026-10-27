"""Send recordings to GitHub: split them into parts small enough for GitHub (max 100 MB a file)
and push each recording to its own branch.

    uv run python -m engineer.ingest.share --upload          # newest recording -> GitHub
    uv run python -m engineer.ingest.share --upload <file>   # a specific one
    uv run python -m engineer.ingest.share --pending         # every recording not uploaded yet
    uv run python -m engineer.ingest.share                   # only split into data/share/
    uv run python -m engineer.ingest.share --join data/share/<name>.zst.part01

How the upload works: the parts go into a commit of their own (just data/share/<parts>, no code)
which is pushed straight to the branch `recording-<time>`. Nothing in your folder changes: no
branch switching, no staged files, your own edits are untouched. Uploaded recordings are listed in
data/share/uploaded.txt so they're never sent twice.

With `upload: {auto: true}` in config.local.yaml, `engineer run` does `--pending` for the
recordings of that run when you stop it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from engineer.config import PROJECT_ROOT, Config, load_config

PART_BYTES = 90 * 1024 * 1024
UPLOADED_LIST = "uploaded.txt"


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


def branch_name(path: Path) -> str:
    return "recording-" + path.stem.split("_")[0].lower()


# -- upload ------------------------------------------------------------------------------------

class UploadError(Exception):
    pass


def _git(args: list[str], repo: Path, stdin: str | None = None, env=None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True,
                           text=True, env=env)
    except FileNotFoundError:
        raise UploadError("git is not installed (or not on PATH)") from None
    if r.returncode != 0:
        raise UploadError(f"git {args[0]} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _commit_env(repo: Path) -> dict:
    """git needs a name/email for a commit; use a neutral one if none is configured."""
    env = dict(os.environ)
    for key, cfg_key, default in (("GIT_AUTHOR_NAME", "user.name", "race-engineer"),
                                  ("GIT_AUTHOR_EMAIL", "user.email", "race-engineer@localhost")):
        try:
            value = _git(["config", cfg_key], repo)
        except UploadError:
            value = ""
        env[key] = value or default
        env[key.replace("AUTHOR", "COMMITTER")] = env[key]
    return env


def upload(path: Path, share_dir: Path, remote: str = "origin", repo: Path = PROJECT_ROOT,
           progress=print) -> str:
    """Push one recording to its own branch. Returns the branch name."""
    branch = branch_name(path)
    progress(f"Uploading {path.name} ({path.stat().st_size / 1e6:.0f} MB) to branch {branch}...")
    parts = split(path, share_dir)
    try:
        blobs = [(p.name, _git(["hash-object", "-w", str(p)], repo)) for p in parts]
        share_tree = _git(["mktree"], repo,
                          stdin="".join(f"100644 blob {sha}\t{name}\n" for name, sha in blobs))
        data_tree = _git(["mktree"], repo, stdin=f"040000 tree {share_tree}\tshare\n")
        root_tree = _git(["mktree"], repo, stdin=f"040000 tree {data_tree}\tdata\n")
        commit = _git(["commit-tree", root_tree, "-m", f"Add recording {path.name}"], repo,
                      env=_commit_env(repo))
        _git(["push", remote, f"{commit}:refs/heads/{branch}"], repo)
    finally:
        for p in parts:              # the parts now live in git; don't keep a second copy
            p.unlink(missing_ok=True)
    with open(share_dir / UPLOADED_LIST, "a", encoding="utf-8") as f:
        f.write(path.name + "\n")
    progress(f"Uploaded: {branch}")
    return branch


def uploaded_names(share_dir: Path) -> set[str]:
    p = share_dir / UPLOADED_LIST
    return set(p.read_text(encoding="utf-8").split()) if p.exists() else set()


def upload_pending(cfg: Config, paths: list[Path] | None = None, progress=print) -> list[str]:
    """Upload recordings (default: all in data/sessions/) not uploaded yet and at least
    upload.min_mb in size. Failures are reported and skipped. Returns the branches pushed."""
    share_dir = cfg.data_path / "share"
    done = uploaded_names(share_dir)
    if paths is None:
        paths = sorted(cfg.sessions_path.glob("*.zst"))
    todo = [p for p in paths if p.exists() and p.name not in done
            and p.stat().st_size >= cfg.upload.min_mb * 1e6]
    if not todo:
        progress("No new recordings to upload.")
        return []
    branches = []
    for p in todo:
        try:
            branches.append(upload(p, share_dir, cfg.upload.remote, progress=progress))
        except UploadError as exc:
            progress(f"Upload of {p.name} failed: {exc}")
    return branches


# -- command line ------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.ingest.share")
    ap.add_argument("file", nargs="?", help="recording (default: the newest)")
    ap.add_argument("--upload", action="store_true", help="push it to its own GitHub branch")
    ap.add_argument("--pending", action="store_true",
                    help="upload every recording that hasn't been uploaded yet")
    ap.add_argument("--join", action="store_true", help="join parts back into one file")
    args = ap.parse_args(argv)

    if args.join:
        print(f"Joined: {join(Path(args.file))}")
        return 0
    cfg = load_config()
    if args.pending:
        upload_pending(cfg)
        return 0
    if args.file:
        path = Path(args.file)
    else:
        recordings = sorted(cfg.sessions_path.glob("*.zst"), key=lambda p: p.stat().st_mtime)
        if not recordings:
            print(f"No recordings in {cfg.sessions_path}")
            return 1
        path = recordings[-1]
    if args.upload:
        try:
            upload(path, cfg.data_path / "share", cfg.upload.remote)
        except UploadError as exc:
            print(f"Upload failed: {exc}")
            return 1
        return 0
    size_mb = path.stat().st_size / 1e6
    parts = split(path, cfg.data_path / "share")
    print(f"Recording: {path.name} ({size_mb:.0f} MB) -> {len(parts)} part(s) in data/share/")
    print("To send it to GitHub instead, run: uv run python -m engineer.ingest.share --upload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
