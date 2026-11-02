"""Moving from a terminal install (a clone of the project) to the installed app."""

import yaml

from engineer.config import Config
from engineer.ingest.share import upload_pending
from engineer.migrate import find_project_folders, import_folder


def _old_install(root):
    (root / "engineer").mkdir(parents=True)
    (root / "engineer" / "cli.py").write_text("")
    (root / ".git").mkdir()
    d = root / "data"
    for rel, text in {"sessions/20261029T013436Z_a430.zst": "race",
                      "features/stints.parquet": "history", "tracks/imola.json": "{}",
                      "ptt/questions.jsonl": "{}\n", "feedback/logged.jsonl": "{}\n",
                      "reports/driver-report-latest.pdf": "pdf",
                      "share/uploaded.txt": "x.zst\n", "share/x.zst.part01": "part",
                      "incoming/big.zst": "fetched"}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    (root / "config.local.yaml").write_text(yaml.safe_dump(
        {"upload": {"auto": True}, "voice": {"controller_type": "xbox"}, "data_dir": "data"}))
    return root


def test_import_brings_data_and_settings_and_never_overwrites(tmp_path):
    old = _old_install(tmp_path / "home" / "F1-engineer-2026")
    app = tmp_path / "LocalAppData" / "RaceEngineer"
    (app / "data" / "tracks").mkdir(parents=True)
    (app / "data" / "tracks" / "imola.json").write_text('{"mine": 1}')
    assert find_project_folders(tmp_path / "home") == [old]

    done = import_folder(old, app, progress=lambda _: None)
    data = app / "data"
    assert (data / "sessions" / "20261029T013436Z_a430.zst").read_text() == "race"
    assert (data / "feedback" / "logged.jsonl").exists()
    assert (data / "share" / "uploaded.txt").exists()          # what was already uploaded
    assert not (data / "share" / "x.zst.part01").exists() and not (data / "incoming").exists()
    assert (data / "tracks" / "imola.json").read_text() == '{"mine": 1}'   # kept the app's
    local = yaml.safe_load((app / "config.local.yaml").read_text())
    assert local == {"upload": {"auto": True, "repo": str(old)},
                     "voice": {"controller_type": "xbox"}}
    assert done["upload_repo"] == str(old) and done["skipped"] == 1

    again = import_folder(old, app, progress=lambda _: None)  # safe to run twice
    assert again["copied"] == 0


def test_uploads_need_a_git_clone(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.upload.repo = str(tmp_path / "not-a-clone")
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "a.zst").write_bytes(b"x" * 3_000_000)
    said = []
    assert upload_pending(cfg, progress=said.append) == []
    assert "isn't one" in said[0]
