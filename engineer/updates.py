"""New versions of the installed app: notice, download and install.

The installed app (not a clone: that updates with git) asks GitHub Releases for the latest
version at start-up and every few hours. When it's newer than this one (engineer/version.py),
the app window shows a banner with the release page and "Download and install":

- Windows: the setup file is downloaded into data/updates/ (in a thread, never on the asyncio
  loop; checked against the size, and the SHA-256 when GitHub gives one). "Install" closes the
  engineer normally (uploads finish, history is saved), then runs the installer silently once
  the app's process has exited (PowerShell Wait-Process, not a fixed delay); it starts the new
  version when done. The installer closes any other copy of the app still holding its files
  (/CLOSEAPPLICATIONS, CloseApplications=force) and writes data/updates/install.log; a failed
  install is reported on the next start. 11 Nov: an update could fail because the daily driver
  report started in its own process at shutdown, right before the installer, and kept the
  app's files in use for minutes; it's skipped when an install is waiting. Settings and data
  live outside the program folder, so nothing is lost.
- Mac: the banner links to the .dmg (an app from a disk image can't replace itself).

`updates.auto_download: true` (Settings tab) downloads a new version by itself whenever no
session is live, so it's ready to install with one click. "Not now" hides a version until the
next one. Nothing is installed without a click.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from engineer.version import VERSION

log = logging.getLogger(__name__)

CHECK_EVERY_S = 6 * 3600
FIRST_CHECK_AFTER_S = 5.0
TIMEOUT_S = 15
USER_AGENT = "RaceEngineer-updater"
INSTALL_DELAY_S = 2          # after the app's process has gone, a moment for its files
WAIT_EXIT_S = 60             # longest wait for the app to exit before installing anyway
SILENT_ARGS = ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
LOG_NAME = "install.log"
LOG_OK = "Installation process succeeded"   # Inno Setup writes this line on success
# This run's recordings, to add to the tyre history on the first start after an update (14 Nov:
# adding them and uploading every recording at shutdown kept the installer waiting for minutes
# behind a closed window, and the update looked like it failed).
DEFERRED_NAME = "after_update.json"


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """"v0.1.5" / "0.1.5" -> (0, 1, 5). None for dev builds and anything else."""
    if not v:
        return None
    v = v.strip().lstrip("vV")
    parts = v.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def newer(latest: str | None, current: str | None) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


@dataclass(frozen=True)
class Release:
    version: str
    page_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int | None = None
    sha256: str | None = None
    notes: str = ""


def asset_suffix(platform: str = sys.platform) -> str:
    return "-mac.dmg" if platform == "darwin" else ".exe"


def parse_release(data: dict, platform: str = sys.platform) -> Release | None:
    """GitHub's releases/latest JSON -> Release (the installer for this platform)."""
    version = str(data.get("tag_name") or "").lstrip("vV")
    if parse_version(version) is None:
        return None
    want = asset_suffix(platform)
    asset = next((a for a in data.get("assets") or []
                  if str(a.get("name", "")).endswith(want)
                  and (platform == "darwin" or "Setup" in a.get("name", ""))), None)
    digest = str(asset.get("digest") or "") if asset else ""
    return Release(version, data.get("html_url") or "",
                   asset.get("name") if asset else None,
                   asset.get("browser_download_url") if asset else None,
                   asset.get("size") if asset else None,
                   digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
                   str(data.get("body") or "")[:2000])


def fetch_latest(repo: str, platform: str = sys.platform) -> Release | None:
    """The newest published release (blocking: call from a thread)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return parse_release(json.loads(r.read().decode("utf-8")), platform)


def download(rel: Release, folder: Path, progress=lambda done, total: None) -> Path:
    """Download the installer to folder (blocking: call from a thread). A finished file is
    reused; a broken one is deleted."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / rel.asset_name
    if path.exists() and _ok(path, rel):
        return path
    part = path.with_name(path.name + ".part")
    req = urllib.request.Request(rel.asset_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r, open(part, "wb") as out:
        total = int(r.headers.get("Content-Length") or rel.asset_size or 0)
        done = 0
        while chunk := r.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            progress(done, total)
    if not _ok(part, rel):
        part.unlink(missing_ok=True)
        raise ValueError("the download didn't match the release (size or checksum)")
    part.replace(path)
    return path


def _ok(path: Path, rel: Release) -> bool:
    if rel.asset_size and path.stat().st_size != rel.asset_size:
        return False
    if rel.sha256:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        return h.hexdigest().lower() == rel.sha256.lower()
    return True


def _ps(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def installer_command(path: Path, log_path: Path, wait_pid: int) -> list[str]:
    """The hidden PowerShell that waits for the app (process wait_pid) to exit, then runs the
    installer silently with a log."""
    args = ",".join(_ps(a) for a in SILENT_ARGS + [f'/LOG="{log_path}"'])
    script = (f"Wait-Process -Id {int(wait_pid)} -Timeout {WAIT_EXIT_S} "
              f"-ErrorAction SilentlyContinue; Start-Sleep -Seconds {INSTALL_DELAY_S}; "
              f"Start-Process -FilePath {_ps(path)} -ArgumentList {args}")
    return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-Command", script]


def run_installer(path: Path, log_path: Path | None = None, wait_pid: int | None = None) -> None:
    """Windows: the installer, silently, once this process has exited. It starts the new
    version when it's done (packaging/installer.iss)."""
    log_path = log_path or path.with_name(LOG_NAME)
    log_path.unlink(missing_ok=True)                 # this install's log only
    cmd = installer_command(path, log_path, wait_pid if wait_pid is not None else os.getpid())
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)


def defer_history(folder: Path, paths: list[Path]) -> None:
    """Remember recordings to add to the tyre history after the update."""
    path = Path(folder) / DEFERRED_NAME
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        old = []
    keep = list(dict.fromkeys([*old, *(str(p) for p in paths)]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep), encoding="utf-8")


def take_deferred(folder: Path) -> list[Path]:
    """The recordings left for after an update (the list is cleared), only those still there."""
    path = Path(folder) / DEFERRED_NAME
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
    except (OSError, ValueError):
        return []
    return [Path(n) for n in names if Path(n).exists()]


def last_install(folder: Path, current: str) -> dict | None:
    """What the last in-app install did, from its log: None when there's no log."""
    path = Path(folder) / LOG_NAME
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ok = LOG_OK in text
    tail = [ln for ln in text.splitlines() if ln.strip()][-8:]
    return {"ok": ok, "log": str(path), "tail": tail if not ok else [], "version": current}


class Updater:
    def __init__(self, cfg, current: str = VERSION, platform: str = sys.platform,
                 fetch=None, fetch_file=None, racing=lambda: False, enabled: bool | None = None):
        """fetch(repo, platform) and fetch_file(release, folder, progress): replaceable in
        tests. racing(): a session is live (auto-download waits)."""
        from engineer.config import FROZEN
        self.cfg, self.current, self.platform = cfg, current, platform
        self._fetch = fetch or fetch_latest
        self._fetch_file = fetch_file or download
        self.racing = racing
        # Only the installed app: a clone updates with git, and a dev build has no version.
        self.enabled = (FROZEN and parse_version(current) is not None) if enabled is None \
            else enabled
        self.latest: Release | None = None
        self.checked_at: float | None = None
        self.state = "idle"          # idle | checking | downloading | ready | error
        self.error: str | None = None
        self.progress = 0.0
        self.file: Path | None = None
        self.install_requested = False
        self.on_install = None       # cli: close the app (then run_installer)
        self._task: asyncio.Task | None = None
        self._want_download = False
        self.last_install = last_install(self.folder, current) if self.enabled else None
        if self.last_install and not self.last_install["ok"]:
            log.warning("the last update didn't finish (see %s): %s", self.last_install["log"],
                        " | ".join(self.last_install["tail"]))

    @property
    def folder(self) -> Path:
        return self.cfg.data_path / "updates"

    @property
    def available(self) -> bool:
        return self.latest is not None and newer(self.latest.version, self.current)

    @property
    def can_install(self) -> bool:
        """Download-and-install works on Windows; on a Mac it's a link to the disk image."""
        return self.platform == "win32" and self.latest is not None \
            and self.latest.asset_url is not None

    def status(self) -> dict:
        rel = self.latest if self.available else None
        u = self.cfg.updates
        return {"current": self.current, "enabled": self.enabled, "available": rel is not None,
                "latest": rel.version if rel else None, "page": rel.page_url if rel else None,
                "asset": rel.asset_url if rel else None, "notes": rel.notes if rel else "",
                "can_install": self.can_install, "state": self.state, "error": self.error,
                "progress": round(self.progress, 3), "auto_download": bool(u.auto_download),
                "dismissed": rel is not None and u.skip_version == rel.version,
                "checked": self.checked_at is not None,
                "last_install_failed": bool(self.last_install and not self.last_install["ok"]),
                "install_log": self.last_install["log"] if self.last_install else None}

    # -- running ---------------------------------------------------------------------------------

    def start(self) -> None:
        if self.enabled and self.cfg.updates.check:
            self._task = asyncio.ensure_future(self._loop())

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_CHECK_AFTER_S)
        while True:
            try:
                await self.check()
                await self._maybe_download()
            except Exception as exc:             # never let updates take the app down
                log.warning("update check: %s", exc)
            end = time.monotonic() + CHECK_EVERY_S
            while time.monotonic() < end:
                await asyncio.sleep(5)
                if self._want_download or (self.available and self.cfg.updates.auto_download
                                           and self.state == "idle"):
                    await self._maybe_download()

    async def check(self) -> None:
        self.state, self.error = "checking", None
        try:
            self.latest = await asyncio.to_thread(self._fetch, self.cfg.updates.repo,
                                                  self.platform)
            self.checked_at = time.time()
            self.state = "idle"
        except Exception as exc:                 # offline: try again later
            self.state, self.error = "idle", None
            log.info("could not check for a new version: %s", exc)
        if self.available and self.file is not None and self.latest.asset_name != self.file.name:
            self.file, self.state = None, "idle"     # an even newer one came out

    async def _maybe_download(self) -> None:
        if not (self.available and self.can_install) or self.state in ("downloading", "ready"):
            self._want_download = False
            return
        if not self._want_download and not self.cfg.updates.auto_download:
            return
        if not self._want_download and self.racing():
            return                               # auto: not while a session is live
        self._want_download = False
        await self.download_now()

    async def download_now(self) -> None:
        self.state, self.error, self.progress = "downloading", None, 0.0

        def progress(done, total):
            self.progress = done / total if total else 0.0
        try:
            self.file = await asyncio.to_thread(self._fetch_file, self.latest, self.folder,
                                                progress)
            self.state, self.progress = "ready", 1.0
        except Exception as exc:
            self.state, self.error = "error", f"download failed: {exc}"
            log.warning("update download failed: %s", exc)

    # -- the window's buttons ------------------------------------------------------------------

    def request_download(self) -> None:
        self._want_download = True

    def install(self) -> bool:
        """Close the app and install the downloaded version. False if it isn't downloaded."""
        if self.state != "ready" or self.file is None:
            return False
        self.install_requested = True
        if self.on_install is not None:
            self.on_install()
        return True

    def run_if_requested(self) -> None:
        """The very last thing the app does when it closes (cli)."""
        if self.install_requested and self.file is not None and self.file.exists():
            print(f"Installing Race Engineer {self.latest.version}: it starts again when done.",
                  flush=True)
            run_installer(self.file, self.folder / LOG_NAME)
