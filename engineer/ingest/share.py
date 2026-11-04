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
data/share/uploaded.txt so they're never sent twice. Push-to-talk questions asked during the
recording go along as <recording>.questions.jsonl (from data/ptt/questions.jsonl).

With `upload: {auto: true}` in config.local.yaml, `engineer run` does `--pending` for the
recordings of that run when you stop it.
"""

from __future__ import annotations

import argparse
import json
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
    # Bytes in and out, never text mode: on Windows text mode turns "\n" into "\r\n", and
    # `git mktree` then puts a stray "\r" at the end of every file and folder name.
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env,
                           input=None if stdin is None else stdin.encode("utf-8"))
    except FileNotFoundError:
        raise UploadError("git is not installed (or not on PATH)") from None
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        raise UploadError(f"git {args[0]} failed: {err or out}")
    return out


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


def session_questions(path: Path, questions_log: Path | None) -> Path | None:
    """The push-to-talk questions asked during this recording (its session UID is in the file
    name), written next to the parts as <recording>.questions.jsonl; None if there are none."""
    if questions_log is None or not questions_log.exists():
        return None
    uid = path.stem.split("_")[-1].lower()
    lines = []
    for line in questions_log.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("session_uid", "").lower() == uid:
                lines.append(line)
        except ValueError:
            continue
    if not lines:
        return None
    out = path.parent / f"{path.name}.questions.jsonl"
    out.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return out


def upload(path: Path, share_dir: Path, remote: str = "origin", repo: Path = PROJECT_ROOT,
           progress=print, questions_log: Path | None = None) -> str:
    """Push one recording (plus the push-to-talk questions asked during it) to its own branch.
    Returns the branch name."""
    branch = branch_name(path)
    size = path.stat().st_size
    progress(f"Uploading {path.name} ({size / 1e6:.0f} MB) to branch {branch}...")
    parts = split(path, share_dir)
    q = session_questions(path, questions_log)
    if q is not None:
        moved = share_dir / q.name
        q.replace(moved)
        parts.append(moved)
    try:
        blobs = [(p.name, _git(["hash-object", "-w", str(p)], repo)) for p in parts]
        share_tree = _git(["mktree"], repo,
                          stdin="".join(f"100644 blob {sha}\t{name}\n" for name, sha in blobs))
        data_tree = _git(["mktree"], repo, stdin=f"040000 tree {share_tree}\tshare\n")
        root_tree = _git(["mktree"], repo, stdin=f"040000 tree {data_tree}\tdata\n")
        commit = _git(["commit-tree", root_tree, "-m", f"Add recording {path.name}"], repo,
                      env=_commit_env(repo))
        # "+": a recording uploaded before and grown since (an online lobby keeps one session
        # UID, so later sessions are added to the same file) replaces its branch.
        _git(["push", remote, f"+{commit}:refs/heads/{branch}"], repo)
    finally:
        for p in parts:              # the parts now live in git; don't keep a second copy
            p.unlink(missing_ok=True)
    with open(share_dir / UPLOADED_LIST, "a", encoding="utf-8") as f:
        f.write(f"{path.name} {size}\n")
    progress(f"Uploaded: {branch}")
    return branch


def uploaded_sizes(share_dir: Path) -> dict[str, int | None]:
    """Uploaded recordings and their size when sent (None: listed before sizes were kept)."""
    p = share_dir / UPLOADED_LIST
    out: dict[str, int | None] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        bits = line.split()
        if not bits:
            continue
        size = int(bits[1]) if len(bits) > 1 and bits[1].isdigit() else None
        if bits[0] not in out or (size or 0) > (out[bits[0]] or 0):
            out[bits[0]] = size
    return out


def uploaded_names(share_dir: Path) -> set[str]:
    return set(uploaded_sizes(share_dir))


GROWN_BYTES = 1_000_000      # a recording this much bigger than when uploaded goes up again


def pending(cfg: Config, paths: list[Path] | None = None) -> list[Path]:
    """Recordings not uploaded yet, or grown since (later sessions under the same UID), at least
    upload.min_mb, oldest first."""
    done = uploaded_sizes(cfg.data_path / "share")
    if paths is None:
        paths = sorted(cfg.sessions_path.glob("*.zst"))
    out = []
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size < cfg.upload.min_mb * 1e6:
            continue
        sent = done.get(p.name, -1)
        if sent == -1 or (sent is not None and size > sent + GROWN_BYTES):
            out.append(p)
    return out


def upload_pending(cfg: Config, paths: list[Path] | None = None, progress=print) -> list[str]:
    """Upload recordings (default: all in data/sessions/) not uploaded yet (or grown since) and at
    least upload.min_mb in size. Failures are reported and skipped. Returns the branches pushed."""
    share_dir = cfg.data_path / "share"
    todo = pending(cfg, paths)
    if not todo:
        progress("No new recordings to upload.")
        return []
    repo = Path(cfg.upload.repo) if cfg.upload.repo else PROJECT_ROOT
    if not (repo / ".git").exists():
        progress(f"Uploads need a git clone of the project (upload: repo: in config.local.yaml); "
                 f"{repo} isn't one.")
        return []
    branches = []
    for p in todo:
        try:
            branches.append(upload(p, share_dir, cfg.upload.remote, repo=repo, progress=progress,
                                   questions_log=cfg.ptt_path / "questions.jsonl"))
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
            upload(path, cfg.data_path / "share", cfg.upload.remote,
                   repo=Path(cfg.upload.repo) if cfg.upload.repo else PROJECT_ROOT,
                   questions_log=cfg.ptt_path / "questions.jsonl")
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
