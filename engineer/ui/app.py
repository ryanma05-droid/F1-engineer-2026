"""The app window: what it shows and what its buttons do. Served by UiServer on the engineer's own
loop while `engineer run` is going, so the window always shows the race as it's being recorded.

It reads (never writes) the race: RaceState, the listener and recorder counters, the radio calls
and questions as they happen. Settings it changes are applied live (the tempo, the push-to-talk
controller) and saved to config.local.yaml, so they stick for the next run.
"""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import time
import webbrowser
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from engineer.app import TEMPOS
from engineer.bus import events as ev
from engineer.config import Config, save_local
from engineer.ingest.formats import f1_2025 as f
from engineer.ui.page import PAGE
from engineer.ui.server import Response, UiServer, file_response

log = logging.getLogger(__name__)

RADIO_KEEP = 12
QUESTIONS_KEEP = 6
FEEDBACK_SHOW = 8
LIVE_PACKET_S = 3.0          # packets within this long = "connected to the game"


def _num(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _laptime(ms: float | None) -> str | None:
    if not ms:
        return None
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:06.3f}"


class AppWindow:
    def __init__(self, cfg: Config, engine, listener=None, recorder=None, ptt=None,
                 config_path: Path | None = None):
        """ptt: the push-to-talk control (cli.PushToTalkControl) or None (replays, tests)."""
        self.cfg, self.engine, self.listener, self.recorder = cfg, engine, listener, recorder
        self.ptt, self.config_path = ptt, config_path
        self.on_setup_done = None              # called once when the first-time questions are done
        self.state = engine.pipeline.state
        self.radio: deque = deque(maxlen=RADIO_KEEP)
        self.questions: deque = deque(maxlen=QUESTIONS_KEEP)
        self.report_proc: subprocess.Popen | None = None
        self.started = time.monotonic()
        self._last_packets, self._last_t, self._rate = 0, time.monotonic(), 0.0
        self._last_packet_at: float | None = None
        self._feedback: tuple[float | None, list[dict]] = (None, [])   # (file mtime, entries)
        bus = engine.pipeline.bus
        bus.subscribe(ev.RadioMessage, self._on_radio)
        # Subscribed after the QuestionAnswerer, so its answer is already there when this runs.
        bus.subscribe(ev.QuestionAsked, self._on_question)
        self.server = UiServer({
            ("GET", "/"): self._page,
            ("GET", "/api/state"): self._api_state,
            ("GET", "/api/report"): self._api_report,
            ("GET", "/report/latest.pdf"): self._latest_pdf,
            ("POST", "/api/settings"): self._api_settings,
            ("POST", "/api/report/build"): self._api_build,
            ("POST", "/api/feedback/dismiss"): self._api_dismiss,
        })

    # -- lifecycle ----------------------------------------------------------------------------

    async def start(self) -> str:
        await self.server.start(self.cfg.ui.port)
        return self.server.url

    def close(self) -> None:
        self.server.close()

    # -- events -------------------------------------------------------------------------------

    def _on_radio(self, e: ev.RadioMessage) -> None:
        self.radio.appendleft({"at": time.strftime("%H:%M:%S"), "text": e.text,
                               "lap": self._player_lap()})

    def _on_question(self, e: ev.QuestionAsked) -> None:
        answered = self.engine.answers.answered if self.engine.answers else []
        answer = answered[-1][1] if answered else None
        self.questions.appendleft({"at": time.strftime("%H:%M:%S"), "heard": e.text,
                                   "answer": answer})

    # -- state --------------------------------------------------------------------------------

    def _player_lap(self) -> int | None:
        p = self.state.player_index
        if p is None or p >= len(self.state.cars.lap):
            return None
        lap = _num(self.state.cars.lap[p])
        return int(lap) if lap is not None else None

    def _connection(self) -> dict:
        now = time.monotonic()
        packets = self.listener.packets_received if self.listener else 0
        if packets != self._last_packets:
            self._last_packet_at = now
        dt = now - self._last_t
        if dt >= 1.0:
            self._rate = (packets - self._last_packets) / dt
            self._last_packets, self._last_t = packets, now
        live = self._last_packet_at is not None and now - self._last_packet_at < LIVE_PACKET_S
        return {"port": self.cfg.listen.port, "packets": packets, "rate": round(self._rate),
                "live": live, "platform": self.cfg.platform}

    def _session(self) -> dict | None:
        s, c, p = self.state.session, self.state.cars, self.state.player_index
        if s.track_id is None or p is None or p >= len(c.lap):
            return None
        history = self.engine.history.for_car(p) if self.engine.history else []
        laps = [r.lap_time_ms for r in history if r.lap_time_ms and not r.invalid]
        wear = c.tyre_wear_pct[p]
        compound = _num(c.visual_compound[p])
        ahead = _num(c.delta_to_car_in_front_ms[p])
        return {"track": s.track_name, "session": s.session_name, "lap": self._player_lap(),
                "total_laps": s.total_laps if s.is_race else None,
                "position": int(c.position[p]) if _num(c.position[p]) else None,
                "cars": int(np.sum(c.active)) or None,
                "last_lap": _laptime(laps[-1]) if laps else None,
                "best_lap": _laptime(min(laps)) if laps else None,
                "tyre": f.VISUAL_COMPOUNDS.get(int(compound)) if compound is not None else None,
                "wear": None if np.isnan(wear).all() else round(float(np.nanmax(wear))),
                "gap_ahead": round(ahead / 1000, 1) if ahead else None,
                "safety_car": bool(s.safety_car_status) and s.safety_car_status in (1, 2)}

    def _recording(self) -> dict:
        r = self.recorder
        path = r.current_path if r is not None else None
        mb = path.stat().st_size / 1e6 if path is not None and path.exists() else None
        return {"on": r is not None, "file": path.name if path else None,
                "mb": round(mb, 1) if mb is not None else None,
                "folder": str(self.cfg.sessions_path)}

    def _report_status(self) -> dict:
        pdf = self.cfg.data_path / "reports" / "driver-report-latest.pdf"
        when = datetime.fromtimestamp(pdf.stat().st_mtime) if pdf.exists() else None
        return {"pdf": pdf.exists(), "generated": when.strftime("%a %d %b, %H:%M") if when else None,
                "building": self.report_proc is not None and self.report_proc.poll() is None,
                "stamp": pdf.stat().st_mtime if pdf.exists() else None}

    def _feedback_status(self) -> dict:
        """The questions you flagged with "log that" (re-read only when the log file changes)."""
        fb = self.engine.answers.feedback if self.engine.answers else None
        if fb is None or fb.path is None:
            return {"on": False, "open": [], "count": 0, "file": None}
        try:
            mtime = fb.path.stat().st_mtime if fb.path.exists() else None
        except OSError:
            mtime = None
        if mtime != self._feedback[0]:
            self._feedback = (mtime, fb.open_entries())
        from engineer.voice.feedback import matches_now
        items = self._feedback[1]
        return {"on": True, "count": len(items),
                "file": str(fb.markdown_path),
                "open": [{"id": e["id"], "question": e["question"], "lap": e.get("lap"),
                          "track": e.get("track"), "answer": e.get("answer"),
                          "matches": matches_now(e["question"], e.get("intent_then"))}
                         for e in reversed(items[-FEEDBACK_SHOW:])]}

    def snapshot(self) -> dict:
        from engineer.voice.controller import button_label
        v = self.cfg.voice
        ptt = self.ptt.status() if self.ptt else {"state": "off", "held": False,
                                                    "connected": 0}
        return {
            "connection": self._connection(), "session": self._session(),
            "recording": self._recording(), "radio": list(self.radio),
            "questions": list(self.questions),
            "tempo": self.engine.tempo, "default_tempo": self.engine.default_tempo,
            "quiet": self.engine.director.quiet,
            "controller": {"type": v.controller_type if v.enabled else "off",
                           "button": button_label(v.controller_type, v.button), **ptt},
            "setup_done": self.cfg.ui.setup_done, "report": self._report_status(),
            "feedback": self._feedback_status(),
            "console_player": self.cfg.platform == "console",
        }

    # -- routes -------------------------------------------------------------------------------

    async def _page(self, _body) -> Response:
        return Response(200, PAGE, "text/html; charset=utf-8")

    async def _api_state(self, _body) -> Response:
        return Response(200, self.snapshot())

    async def _api_report(self, _body) -> Response:
        # Reading the cached analyses and combining them takes a moment: off the loop.
        data = await asyncio.to_thread(report_insights, self.cfg)
        return Response(200, data)

    async def _latest_pdf(self, _body) -> Response:
        return file_response(self.cfg.data_path / "reports" / "driver-report-latest.pdf")

    async def _api_build(self, _body) -> Response:
        from engineer.report.daily import spawn
        if self.report_proc is None or self.report_proc.poll() is not None:
            self.report_proc = spawn(self.cfg, self.config_path, now=True)
        return Response(200, self._report_status())

    async def _api_dismiss(self, body: dict) -> Response:
        fb = self.engine.answers.feedback if self.engine.answers else None
        entry_id = body.get("id")
        if fb is None or not isinstance(entry_id, str):
            return Response(400, {"error": "expected an id"})
        if not await asyncio.to_thread(fb.dismiss, entry_id):
            return Response(404, {"error": "no such question"})
        fb.write_markdown_in_background()
        return Response(200, self.snapshot())

    async def _api_settings(self, body: dict) -> Response:
        save: dict = {}
        tempo = body.get("tempo")
        if tempo is not None:
            if tempo not in TEMPOS:
                return Response(400, {"error": f"tempo must be one of {', '.join(TEMPOS)}"})
            self.engine.set_tempo(tempo)
            self.engine.default_tempo = tempo
            self.cfg.radio.verbosity = tempo
            save["radio"] = {"verbosity": tempo}
        controller = body.get("controller")
        if controller is not None:
            if controller not in ("xbox", "playstation", "off"):
                return Response(400, {"error": "controller must be xbox, playstation or off"})
            v = self.cfg.voice
            v.enabled = controller != "off"
            if v.enabled:
                v.controller_type = controller
            save["voice"] = {"enabled": v.enabled, "controller_type": v.controller_type}
            if self.ptt is not None:
                await self.ptt.apply(self.cfg)
        if body.get("setup_done") and not self.cfg.ui.setup_done:
            self.cfg.ui.setup_done = True
            save["ui"] = {"setup_done": True}
            if self.on_setup_done:
                self.on_setup_done()
        if save:
            try:
                save_local(save, self.config_path)
            except OSError as exc:
                log.warning("could not save settings (%s)", exc)
        return Response(200, self.snapshot())


def report_insights(cfg: Config) -> dict:
    """The latest driver report's highlights, from the cached analyses (nothing is replayed)."""
    from engineer.report.analysis import AnalysisCache
    from engineer.report.insights import build_report
    from engineer.state.track_maps import TrackMaps

    cache = AnalysisCache(cfg.data_path / "reports" / "cache", TrackMaps(cfg.tracks_path))
    recs = sorted(cfg.sessions_path.glob("*.zst"))
    entries, pending = [], 0
    for rec in recs:
        e = cache.cached(rec)
        if e is None:
            pending += 1
        else:
            entries.append(e)
    r = build_report(entries, datetime.now(timezone.utc))
    return {
        "sessions": len(r.sessions), "laps": r.laps, "pending": pending,
        "overall_pct": round(r.overall_pct, 2), "ideal_gap_s": round(r.ideal_gap_s, 2),
        "themes": [{"title": t.title, "cost_s": round(t.cost_s, 2), "confidence": t.confidence,
                    "detail": t.detail, "advice": t.advice,
                    "corners": [[c, round(v, 2)] for c, v in t.corners[:4]],
                    "trend": [round(v, 3) for _, v in t.trend]} for t in r.themes],
        "strengths": r.strengths,
        "tracks": [{"track": t.track, "pct": round(t.pct, 2), "rank": t.rank, "laps": t.laps,
                    "cars": t.cars, "confidence": t.confidence} for t in r.tracks],
        "types": [{"name": t.name, "relative": round(t.relative, 2), "pct": round(t.pct, 2)}
                  for t in r.types],
    }


def open_window(url: str) -> None:
    """Open the page as an app window (Edge/Chrome --app: no tabs or address bar), else in the
    default browser."""
    from engineer.report.pdf import find_browser
    browser = find_browser()
    if browser is not None and "headless" not in browser.name:
        try:
            subprocess.Popen([str(browser), f"--app={url}", "--window-size=1280,900"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    try:
        webbrowser.open(url)
    except Exception as exc:                          # no browser at all: the URL is printed
        log.info("could not open the app window (%s)", exc)



