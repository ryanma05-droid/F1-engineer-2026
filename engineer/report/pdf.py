"""HTML -> PDF with a browser that's already installed: Microsoft Edge comes with Windows, and
Chrome/Edge/Chromium are used on a Mac or Linux. Headless printing keeps the report's layout
exactly as designed, with no extra Python packages. No browser found: the HTML is kept and opened
instead (print it from the browser)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

PRINT_TIMEOUT_S = 90


def _candidates() -> list[Path]:
    env = os.environ.get
    if sys.platform == "win32":
        roots = [env("PROGRAMFILES(X86)"), env("PROGRAMFILES"), env("LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        return [Path(r) / p for p in rel for r in roots if r]
    if sys.platform == "darwin":
        return [Path(p) for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")]
    found = [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser",
                                       "microsoft-edge", "msedge")]
    pw = Path("/opt/pw-browsers")             # Playwright's browsers (the dev container)
    found += sorted(str(p) for p in pw.glob("chromium_headless_shell-*/*/chrome-headless-shell"))
    found += sorted(str(p) for p in pw.glob("chromium-*/chrome-linux*/chrome"))
    return [Path(p) for p in found if p]


def find_browser() -> Path | None:
    override = os.environ.get("RACE_ENGINEER_BROWSER")
    if override and Path(override).exists():
        return Path(override)
    return next((p for p in _candidates() if p.exists()), None)


def _wait_for_pdf(proc: subprocess.Popen, pdf_path: Path) -> None:
    """Wait for the browser to finish. Some builds write the PDF and then linger, so once the
    file has stopped growing for a second the browser is closed."""
    last, stable_since = -1, None
    deadline = time.monotonic() + PRINT_TIMEOUT_S
    while time.monotonic() < deadline and proc.poll() is None:
        size = pdf_path.stat().st_size if pdf_path.exists() else -1
        if size > 0 and size == last:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 1.0:
                break
        else:
            stable_since = None
        last = size
        time.sleep(0.25)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Print html_path to pdf_path. False (and a log line) if there's no browser or it fails."""
    browser = find_browser()
    if browser is None:
        log.warning("no Edge/Chrome/Chromium found to make the PDF; keeping the HTML report")
        return False
    pdf_path.unlink(missing_ok=True)
    # A throwaway profile: a browser window you already have open doesn't get in the way.
    with tempfile.TemporaryDirectory(prefix="race-engineer-pdf-") as profile:
        cmd = [str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--disable-sync",
               "--disable-background-networking", "--disable-component-update",
               f"--user-data-dir={profile}", "--no-pdf-header-footer",
               "--print-to-pdf-no-header", f"--print-to-pdf={pdf_path}",
               html_path.resolve().as_uri()]
        if "headless-shell" in browser.name:
            cmd.remove("--headless=new")      # the headless-only build is headless already
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("could not start %s (%s)", browser.name, exc)
            return False
        _wait_for_pdf(proc, pdf_path)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        log.warning("%s didn't write the PDF; keeping the HTML report", browser.name)
        return False
    return True


def open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)                       # noqa: S606 (the user's own report)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.info("could not open %s (%s)", path, exc)
