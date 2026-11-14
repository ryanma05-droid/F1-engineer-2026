"""New versions of the installed app (engineer/updates.py): the check, the download, the banner's
buttons and the settings, without touching GitHub."""

import asyncio
import hashlib
import io
import json

from engineer.app import build_engine
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.updates import (
    Release,
    Updater,
    download,
    newer,
    parse_release,
    parse_version,
)

GITHUB = {
    "tag_name": "v0.1.6", "html_url": "https://github.com/x/y/releases/tag/v0.1.6",
    "body": "New: answers first.",
    "assets": [
        {"name": "RaceEngineer-0.1.6-mac.dmg", "size": 9,
         "browser_download_url": "https://example/mac.dmg"},
        {"name": "RaceEngineer-Setup-0.1.6.exe", "size": 5,
         "browser_download_url": "https://example/setup.exe",
         "digest": "sha256:" + hashlib.sha256(b"setup").hexdigest()},
    ],
}


def test_versions():
    assert parse_version("v0.1.10") == (0, 1, 10) and parse_version("0.0.0-dev") is None
    assert newer("0.1.10", "0.1.9") and not newer("0.1.5", "0.1.5")
    assert not newer("0.1.6", "0.0.0-dev")                # a clone never nags
    win = parse_release(GITHUB, "win32")
    assert (win.version, win.asset_name, win.asset_size) == ("0.1.6", "RaceEngineer-Setup-0.1.6.exe", 5)
    assert win.sha256 == hashlib.sha256(b"setup").hexdigest()
    assert parse_release(GITHUB, "darwin").asset_url == "https://example/mac.dmg"
    assert parse_release({"tag_name": "nightly"}, "win32") is None


def test_download_checks_size_and_checksum(tmp_path, monkeypatch):
    rel = parse_release(GITHUB, "win32")

    class Resp(io.BytesIO):
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    body = {"data": b"setup"}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Resp(body["data"]))
    seen = []
    path = download(rel, tmp_path, lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == b"setup" and seen[-1] == (5, 5)
    assert download(rel, tmp_path) == path                 # already there: reused
    path.unlink()
    body["data"] = b"evil!"                                 # same size, wrong checksum
    try:
        download(rel, tmp_path)
        raise AssertionError("a bad download was kept")
    except ValueError:
        pass
    assert not list(tmp_path.iterdir())


def _updater(tmp_path, platform="win32", racing=False, current="0.1.5"):
    cfg = Config(data_dir=tmp_path)
    got = []

    def fetch_file(rel, folder, progress):
        progress(5, 5)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / rel.asset_name
        p.write_bytes(b"setup")
        got.append(rel.version)
        return p
    u = Updater(cfg, current=current, platform=platform, enabled=True,
                fetch=lambda repo, plat: parse_release(GITHUB, plat), fetch_file=fetch_file,
                racing=lambda: racing)
    return cfg, u, got


def test_check_download_install(tmp_path):
    cfg, u, got = _updater(tmp_path)
    asyncio.run(u.check())
    s = u.status()
    assert s["available"] and s["latest"] == "0.1.6" and s["can_install"] and s["state"] == "idle"
    asyncio.run(u._maybe_download())                        # auto-download is off
    assert got == []
    assert not u.install()                                  # nothing downloaded yet
    u.request_download()
    asyncio.run(u._maybe_download())
    assert got == ["0.1.6"] and u.status()["state"] == "ready"
    closed = []
    u.on_install = lambda: closed.append(True)
    assert u.install() and closed and u.install_requested
    # Up to date: no banner.
    _cfg, same, _ = _updater(tmp_path / "b", current="0.1.6")
    asyncio.run(same.check())
    assert not same.status()["available"]


def test_auto_download_waits_for_the_session_to_end(tmp_path):
    cfg, u, got = _updater(tmp_path, racing=True)
    cfg.updates.auto_download = True
    asyncio.run(u.check())
    asyncio.run(u._maybe_download())
    assert got == []                                        # racing
    u.racing = lambda: False
    asyncio.run(u._maybe_download())
    assert got == ["0.1.6"]


def test_mac_gets_a_link_and_offline_is_quiet(tmp_path):
    _cfg, u, got = _updater(tmp_path, platform="darwin")
    asyncio.run(u.check())
    s = u.status()
    assert s["available"] and not s["can_install"] and s["asset"] == "https://example/mac.dmg"
    u.request_download()
    asyncio.run(u._maybe_download())
    assert got == []

    def offline(repo, plat):
        raise OSError("no internet")
    u2 = Updater(Config(data_dir=tmp_path), current="0.1.5", platform="win32", enabled=True,
                 fetch=offline)
    asyncio.run(u2.check())
    assert not u2.status()["available"] and u2.status()["error"] is None
    # A clone (not the installed app) never checks.
    assert not Updater(Config(data_dir=tmp_path), current="0.0.0-dev").enabled


def test_window_buttons_and_settings(tmp_path):
    import yaml

    from engineer.ui.app import AppWindow
    cfg, u, got = _updater(tmp_path)
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    win = AppWindow(cfg, eng, config_path=tmp_path / "config.yaml", updater=u)
    asyncio.run(u.check())
    assert win.snapshot()["update"]["latest"] == "0.1.6"
    post = lambda body: asyncio.run(win._api_update(body))
    assert post({"action": "install"}).status == 409
    r = post({"action": "dismiss"})
    assert json.loads(r.body)["update"]["dismissed"]
    assert post({"action": "auto", "auto_download": True}).status == 200
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert saved["updates"] == {"skip_version": "0.1.6", "auto_download": True}
    assert post({"action": "auto", "auto_download": "yes"}).status == 400
    assert post({"action": "explode"}).status == 400
    # Without an updater (replays, a clone): the version only.
    plain = AppWindow(cfg, eng)
    assert plain.snapshot()["update"]["enabled"] is False
    assert asyncio.run(plain._api_update({"action": "check"})).status == 409
    assert isinstance(Release("1", "p"), Release)


def test_installer_waits_for_the_app_to_exit_and_logs(tmp_path):
    # 11 Nov: a fixed 4 s wait, and the daily report process holding the app's files.
    from pathlib import PureWindowsPath
    from engineer.updates import SILENT_ARGS, installer_command
    # A Windows path on every platform (the release build runs this on Windows: 11 Nov, v0.1.7).
    exe = PureWindowsPath(r"C:\Users\o'neil\AppData\Local\RaceEngineer\data\updates\RaceEngineer-Setup-0.1.7.exe")
    log = exe.with_name("install.log")
    cmd = installer_command(exe, log, 4242)
    assert cmd[0] == "powershell" and "-WindowStyle" in cmd
    script = cmd[-1]
    assert script.startswith("Wait-Process -Id 4242 ")
    assert "'C:\\Users\\o''neil\\" in script                     # quotes escaped
    assert "/CLOSEAPPLICATIONS" in SILENT_ARGS and "'/CLOSEAPPLICATIONS'" in script
    escaped = str(log).replace("'", "''")                       # PowerShell's quote doubling
    assert f"'/LOG=\"{escaped}\"'" in script


def test_a_failed_install_is_reported_next_start(tmp_path):
    from engineer.updates import last_install
    assert last_install(tmp_path, "0.1.7") is None
    (tmp_path / "install.log").write_text("2026-11-11 Log opened.\nDeleteFile failed; code 5.\n"
                                          "Access is denied.\n", encoding="utf-8")
    got = last_install(tmp_path, "0.1.7")
    assert got["ok"] is False and "Access is denied." in got["tail"]
    (tmp_path / "install.log").write_text("Installation process succeeded.\nLog closed.\n",
                                          encoding="utf-8")
    assert last_install(tmp_path, "0.1.7")["ok"] is True


def test_an_update_leaves_this_runs_races_for_the_next_start(tmp_path):
    # 14 Nov: "Install and restart" closed the window, then adding this run's races to the
    # history and uploading every recording kept the installer waiting for minutes, and the
    # update looked broken. The races are remembered and added on the first start after it.
    from engineer.updates import defer_history, take_deferred
    a, b = tmp_path / "a.zst", tmp_path / "b.zst"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    folder = tmp_path / "updates"
    defer_history(folder, [a])
    defer_history(folder, [a, b])                  # a second try at the update: no repeats
    b.unlink()                                      # gone since: skipped
    assert take_deferred(folder) == [a]
    assert take_deferred(folder) == []              # taken once
