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
from engineer.version import VERSION

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
                 config_path: Path | None = None, voice=None, uploader=None, updater=None,
                 overlay=None):
        """ptt: the push-to-talk control (cli.PushToTalkControl) or None (replays, tests).
        voice: radio.switch.VoiceControl (picks the engineer); None = personality only.
        overlay: overlay.control.OverlayControl (the lap delta overlay); None = not offered."""
        from engineer.radio.switch import VoiceControl
        self.voice = voice or VoiceControl(cfg, engine, None)
        self.uploader = uploader                 # ingest.uploader.BackgroundUploader (live only)
        self.updater = updater                   # updates.Updater (live only)
        self.overlay = overlay
        self._voice_task = None
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
            ("POST", "/api/prerace"): self._api_prerace,
            ("POST", "/api/upload"): self._api_upload,
            ("POST", "/api/update"): self._api_update,
            ("POST", "/api/overlay"): self._api_overlay,
        })
        self._prerace_busy = False

    # -- lifecycle ----------------------------------------------------------------------------

    async def start(self) -> str:
        await self.server.start(self.cfg.ui.port)
        return self.server.url

    def close(self) -> None:
        self.server.close()

    # -- events -------------------------------------------------------------------------------

    def _on_radio(self, e: ev.RadioMessage) -> None:
        self.radio.appendleft({"at": time.strftime("%H:%M:%S"), "text": e.said,
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

    def _coaching(self) -> dict:
        from engineer.analyzers.coaching import card
        corners = self.engine.corners
        out = card(corners.coach_laps, corners.cfg.focus_after_laps, corners.cfg.min_loss_s)
        out["report"] = corners.report_card()
        out["ideal"] = corners.ideal_card()
        return out

    def _prerace(self) -> dict:
        sa = self.engine.strategy
        return {"racing": sa.racing(), "grid": sa.on_grid(), "busy": self._prerace_busy,
                "defaults": sa.prerace_defaults(),
                "plan": prerace_dict(sa.prerace, sa) if sa.prerace is not None else None}

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
            "detail": self.engine.corners.detail,
            "quiet": self.engine.director.quiet,
            "controller": {"type": v.controller_type if v.enabled else "off",
                           "button": button_label(v.controller_type, v.button), **ptt},
            "setup_done": self.cfg.ui.setup_done, "report": self._report_status(),
            "feedback": self._feedback_status(),
            "coaching": self._coaching(),
            "prerace": self._prerace(),
            "voice": self.voice.status(),
            "uploads": self.uploader.status() if self.uploader is not None else None,
            "update": self.updater.status() if self.updater is not None
            else {"current": VERSION, "enabled": False, "available": False},
            "console_player": self.cfg.platform == "console",
            "overlay": self.overlay.status() if self.overlay is not None
            else {"available": False, "enabled": False, "state": "off", "error": None},
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

    async def _api_prerace(self, body: dict) -> Response:
        """Work out the pre-race plan for the track and laps picked in the window."""
        track, laps = body.get("track_id"), body.get("laps")
        if track is not None and (not isinstance(track, int) or track not in f.TRACKS):
            return Response(400, {"error": "unknown track"})
        if laps is not None and (not isinstance(laps, int) or not 1 <= laps <= 99):
            return Response(400, {"error": "laps must be 1 to 99"})
        loop = asyncio.get_running_loop()
        done: asyncio.Future = loop.create_future()

        def got(plan) -> None:
            if not done.done():
                done.set_result(plan)
        why = self.engine.strategy.ask_prerace(got, track, laps)
        if why is not None:
            return Response(409, {"error": why, **self.snapshot()})
        self._prerace_busy = True
        try:
            # Answered by the strategy worker; picked up on the next packet or the idle tick.
            await asyncio.wait_for(done, timeout=20)
        except asyncio.TimeoutError:
            return Response(504, {"error": "the strategy worker didn't answer"})
        finally:
            self._prerace_busy = False
        return Response(200, self.snapshot())

    async def _api_upload(self, _body) -> Response:
        """"Upload now": every recording waiting, straight away."""
        if self.uploader is None:
            return Response(409, {"error": "uploads are off here"})
        self.uploader.request_now()
        return Response(200, self.snapshot())

    async def _api_update(self, body: dict) -> Response:
        """The update banner and the Settings card: download, install, not now, check now, and
        the auto-download switch."""
        u = self.updater
        if u is None or not u.enabled:
            return Response(409, {"error": "updates are for the installed app"})
        action = body.get("action")
        save = None
        if action == "download":
            u.request_download()
        elif action == "install":
            if not u.install():
                return Response(409, {"error": "not downloaded yet"})
        elif action == "dismiss":
            if u.latest is not None:
                self.cfg.updates.skip_version = u.latest.version
                save = {"updates": {"skip_version": u.latest.version}}
        elif action == "check":
            await u.check()
        elif action == "auto":
            on = body.get("auto_download")
            if not isinstance(on, bool):
                return Response(400, {"error": "auto_download must be true or false"})
            self.cfg.updates.auto_download = on
            save = {"updates": {"auto_download": on}}
        else:
            return Response(400, {"error": "action must be download, install, dismiss, check "
                                           "or auto"})
        if save:
            try:
                save_local(save, self.config_path)
            except OSError as exc:
                log.warning("could not save settings (%s)", exc)
        return Response(200, self.snapshot())

    async def _api_overlay(self, body: dict) -> Response:
        """The Move overlay / Lock here button."""
        if self.overlay is None:
            return Response(400, {"error": "the overlay isn't available here"})
        action = body.get("action")
        if action == "move":
            await self.overlay.move()
            if self.overlay.cfg.overlay.enabled:
                try:
                    save_local({"overlay": {"enabled": True}}, self.config_path)
                except OSError as exc:
                    log.warning("could not save settings (%s)", exc)
        elif action == "lock":
            self.overlay.lock()
        else:
            return Response(400, {"error": "action must be move or lock"})
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
        detail = body.get("detail")
        if detail is not None:
            if detail not in ("plain", "specific"):
                return Response(400, {"error": "detail must be plain or specific"})
            self.engine.set_detail(detail)
            self.cfg.radio.detail = detail
            save.setdefault("radio", {})["detail"] = detail
        eng = body.get("engineer")
        if eng is not None:
            from engineer.radio.engineers import BY_KEY
            if eng not in BY_KEY:
                return Response(400, {"error": f"engineer must be one of {', '.join(BY_KEY)}"})
            save.setdefault("radio", {})["engineer"] = eng
            # Downloading and loading a voice takes a while: in the background, the window
            # shows how it's going.
            self._voice_task = asyncio.ensure_future(self.voice.switch(eng))
            await asyncio.sleep(0)
        volume = body.get("volume")
        if volume is not None:
            if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
                return Response(400, {"error": "volume must be 0 to 100"})
            self.voice.set_volume(volume)
            save.setdefault("radio", {})["volume_pct"] = volume
        if "driver_name" in body:
            name = body["driver_name"]
            if name is not None and (not isinstance(name, str) or len(name) > 30):
                return Response(400, {"error": "driver_name must be up to 30 characters"})
            name = (name or "").strip() or None
            self.voice.set_driver_name(name)
            save.setdefault("radio", {})["driver_name"] = name
        controller = body.get("controller")
        if controller is not None:
            if controller not in ("xbox", "playstation", "off"):
                return Response(400, {"error": "controller must be xbox, playstation or off"})
            v = self.cfg.voice
            v.enabled = controller != "off"
            if v.enabled:
                v.controller_type = controller
            save["voice"] = {"enabled": v.enabled, "controller_type": v.controller_type}
            if self.cfg.radio.console_only is None:      # console only follows push-to-talk
                for part in (self.engine.strategy, getattr(self.engine, "pace", None)):
                    if part is not None:
                        part.console = not v.enabled
            if self.ptt is not None:
                await self.ptt.apply(self.cfg)
        if "overlay" in body:
            on = body["overlay"]
            if not isinstance(on, bool):
                return Response(400, {"error": "overlay must be true or false"})
            if self.overlay is None:
                return Response(400, {"error": "the overlay isn't available here"})
            await self.overlay.set_enabled(on)
            save["overlay"] = {"enabled": on}
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


def prerace_dict(plan, sa) -> dict:
    """The pre-race plan for the app window: each start tyre's stints, time and wear."""
    from engineer.radio import phrases
    names = {16: "Soft", 17: "Medium", 18: "Hard"}
    best = plan.best
    out = {"track": phrases.say_track(plan.track_id), "track_id": plan.track_id,
           "laps": plan.laps.laps if plan.laps else None,
           "how": plan.laps.how if plan.laps else None, "grid": plan.grid, "error": plan.error,
           "said": phrases.prerace_phrase(plan, "normal", sa.tyres.limit_pct, sa.cfg.margin_pct)
           if plan.error is None else None, "starts": []}
    for st in plan.starts:
        call = st.decision.call
        out["starts"].append({
            "compound": names.get(st.compound, "?"), "key": st.compound,
            "best": st is best, "delta_s": round(st.cost_s - best.cost_s, 1),
            "safe": bool(call.safe), "tight": bool(st.decision.taken_gamble or not call.safe),
            "stints": [{"compound": names.get(t.compound, "?"), "from": t.start_lap,
                        "to": t.end_lap, "wear": round(t.end_wear)
                        if t.end_wear < 1000 else None} for t in call.stints]})
    return out


def report_insights(cfg: Config) -> dict:
    """The latest driver report's highlights, from the cached analyses (nothing is replayed)."""
    from engineer.report.analysis import AnalysisCache, entries_of
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
            entries += entries_of(e)
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



