"""New versions of the installed app: notice, download and install.

The installed app (not a clone: that updates with git) asks GitHub Releases for the latest
version at start-up and every few hours. When it's newer than this one (engineer/version.py),
the app window shows a banner with the release page and "Download and install":

- Windows: the setup file is downloaded into data/updates/ (in a thread, never on the asyncio
  loop; checked against the size, and the SHA-256 when GitHub gives one). "Install" closes the
  engineer normally (uploads finish, history is saved), then runs the installer silently a few
  seconds later, once the app's files are free; it starts the new version when done. Settings
  and data live outside the program folder, so nothing is lost.
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
INSTALL_DELAY_S = 4          # the app closes first, then the installer runs
SILENT_ARGS = ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART"]


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


def run_installer(path: Path) -> None:
    """Windows: the installer, silently, a few seconds from now (the app closes meanwhile). It
    starts the new version when it's done (packaging/installer.iss)."""
    args = " ".join(SILENT_ARGS)
    # ping waits without a console window or a keypress (timeout.exe needs a console).
    cmd = f'ping -n {INSTALL_DELAY_S + 1} 127.0.0.1 >nul & start "" "{path}" {args}'
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    subprocess.Popen(["cmd", "/c", cmd], creationflags=flags, close_fds=True)


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
                "checked": self.checked_at is not None}

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
            run_installer(self.file)
