"""Builds the driver report, and the once-a-day trigger.

    uv run python -m engineer.report            # build it now and open the PDF
    uv run python -m engineer.report --daily    # only if there are new sessions and none today

The daily report needs no Windows scheduler: sessions only appear while the engineer runs, so when
`engineer run` stops it checks whether a report is due (new recordings since the last report and
no report yet today) and, if so, builds it in a separate background process. Closing the window
doesn't stop it. Reports land in data/reports/ (driver-report-<date>.pdf, plus
driver-report-latest.pdf), and data/reports/state.json remembers what the last one covered so the
next can say what changed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from engineer.config import FROZEN, Config
from engineer.report.analysis import AnalysisCache
from engineer.report.insights import build_report
from engineer.report.pdf import html_to_pdf, open_file
from engineer.report.render import render_html
from engineer.state.track_maps import TrackMaps

log = logging.getLogger(__name__)


def reports_dir(cfg: Config) -> Path:
    return cfg.data_path / "reports"


def _state_path(cfg: Config) -> Path:
    return reports_dir(cfg) / "state.json"


def load_state(cfg: Config) -> dict:
    try:
        return json.loads(_state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def recordings(cfg: Config) -> list[Path]:
    return sorted(cfg.sessions_path.glob("*.zst"))


def due(cfg: Config) -> bool:
    """A daily report is due: recordings the last report hasn't seen, and none built today."""
    state = load_state(cfg)
    if state.get("date") == _today():
        return False
    seen = set(state.get("seen", []))
    return any(p.name not in seen for p in recordings(cfg))


def generate(cfg: Config, progress=print) -> Path | None:
    """Build today's report. Returns the PDF (or the HTML if no browser could print it)."""
    out = reports_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    recs = recordings(cfg)
    cache = AnalysisCache(out / "cache", TrackMaps(cfg.tracks_path))
    entries = [cache.get(p, progress) for p in recs]
    state = load_state(cfg)
    # "Since last report" compares with the last report from an earlier day.
    previous = state.get("before") if state.get("date") == _today() else state.get("last")
    report = build_report(entries, datetime.now(timezone.utc), previous)
    if not report.sessions:
        progress(f"No sessions with laps to compare yet ({len(recs)} recordings in "
                 f"{cfg.sessions_path}). Race a few laps with the engineer running first.")
    stem = f"driver-report-{_today()}"
    html = out / f"{stem}.html"
    html.write_text(render_html(report), encoding="utf-8")
    pdf = out / f"{stem}.pdf"
    result = html
    if html_to_pdf(html, pdf):
        shutil.copyfile(pdf, out / "driver-report-latest.pdf")
        result = pdf
    new_state = {"date": _today(), "seen": [p.name for p in recs], "last": report.summary(),
                 "before": previous if state.get("date") == _today() else state.get("last")}
    _state_path(cfg).write_text(json.dumps(new_state, indent=1), encoding="utf-8")
    return result


def _command(config_path: Path | None, now: bool = False) -> list[str]:
    """now=False: the daily check (opens when ready); now=True: build now, from the app window,
    which shows it (so it isn't opened)."""
    if FROZEN:                                   # the installed app: RaceEngineer.exe
        return [sys.executable, "--report" if now else "--daily-report"]
    cmd = [sys.executable, "-m", "engineer.report"] + (["--no-open"] if now else ["--daily"])
    return cmd + ["--config", str(Path(config_path).resolve())] if config_path else cmd


def spawn(cfg: Config, config_path: Path | None = None, now: bool = False):
    """Start a report build in its own process (closing the window doesn't stop it).
    Returns the process, or None if it couldn't start."""
    out = reports_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        with open(out / "daily.log", "a", encoding="utf-8") as logf:
            return subprocess.Popen(_command(config_path, now), stdout=logf,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    close_fds=True, **kwargs)
    except OSError as exc:
        log.warning("could not start the driver report (%s)", exc)
        return None


def start_in_background(cfg: Config, config_path: Path | None = None) -> bool:
    """Called when `engineer run` stops: if a report is due, build it in its own process."""
    if not cfg.report.daily or not due(cfg):
        return False
    if spawn(cfg, config_path) is None:
        return False
    print(f"Building today's driver report in the background: it opens when ready "
          f"({reports_dir(cfg)}).")
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse
    from engineer.config import load_config

    ap = argparse.ArgumentParser(prog="python -m engineer.report",
                                 description="Your driver report: strengths and weaknesses from "
                                             "every recorded session, as a PDF.")
    ap.add_argument("--daily", action="store_true",
                    help="only build if there are new sessions and no report today")
    ap.add_argument("--no-open", action="store_true", help="don't open the report when done")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.daily and not due(cfg):
        print("No new sessions since today's report: nothing to do.")
        return 0
    path = generate(cfg)
    if path is None:
        return 1
    print(f"Driver report: {path}")
    print(f"Open it: {path.resolve().as_uri()}")
    if not args.no_open and (not args.daily or cfg.report.open_when_ready):
        open_file(path)
    return 0
