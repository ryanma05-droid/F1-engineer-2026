import subprocess
from pathlib import Path

from engineer.config import Config, load_config
from engineer.ingest import share


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def test_split_and_join_round_trip(tmp_path):
    src = tmp_path / "x.zst"
    src.write_bytes(bytes(range(256)) * 1000)
    parts = share.split(src, tmp_path / "share", part_bytes=100_000)
    assert len(parts) == 3
    joined = share.join(parts[0])
    assert joined.read_bytes() == src.read_bytes()


def test_upload_pushes_a_branch_without_touching_the_working_tree(tmp_path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git("init", "--bare", str(remote), cwd=tmp_path)
    git("init", "-b", "main", str(repo), cwd=tmp_path)
    (repo / "code.py").write_text("print('hi')\n")
    git("add", ".", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    (repo / "code.py").write_text("print('local edit')\n")          # uncommitted edit

    rec = tmp_path / "20261027T010920Z_cdb2205fbdb7a7db.zst"
    rec.write_bytes(b"\x01" * 250_000)
    branch = share.upload(rec, tmp_path / "share", "origin", repo=repo, progress=lambda m: None)

    assert branch == "recording-20261027t010920z"
    files = git("ls-tree", "-r", "--name-only", branch, cwd=remote).split("\n")
    assert files == ["data/share/20261027T010920Z_cdb2205fbdb7a7db.zst.part01"]   # no stray \r
    blob = subprocess.run(["git", "show", f"{branch}:{files[0]}"], cwd=remote, check=True,
                          capture_output=True).stdout
    assert blob == rec.read_bytes()
    # The user's folder is untouched: same branch, edit still there, nothing staged
    assert git("branch", "--show-current", cwd=repo) == "main"
    assert git("status", "--porcelain", cwd=repo) == "M code.py"
    assert share.uploaded_names(tmp_path / "share") == {rec.name}
    assert not list((tmp_path / "share").glob("*.part*"))


def test_upload_pending_skips_done_and_tiny(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    sessions = cfg.sessions_path
    sessions.mkdir(parents=True)
    (sessions / "a_1.zst").write_bytes(b"x" * 3_000_000)
    (sessions / "b_2.zst").write_bytes(b"x" * 10)                     # too small
    (sessions / "c_3.zst").write_bytes(b"x" * 3_000_000)
    (tmp_path / "share").mkdir()
    (tmp_path / "share" / "uploaded.txt").write_text("c_3.zst\n")
    sent = []
    monkeypatch.setattr(share, "upload", lambda p, *a, **k: sent.append(p.name) or "b")
    share.upload_pending(cfg, progress=lambda m: None)
    assert sent == ["a_1.zst"]


def test_local_config_overrides(tmp_path):
    (tmp_path / "config.yaml").write_text("upload:\n  auto: false\n  min_mb: 5\n")
    (tmp_path / "config.local.yaml").write_text("upload:\n  auto: true\n")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.upload.auto is True and cfg.upload.min_mb == 5
    (tmp_path / "other").mkdir()
    assert load_config(Path(tmp_path / "other" / "missing.yaml")).upload.auto is False
