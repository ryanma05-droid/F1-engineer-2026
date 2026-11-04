"""Builds the engine: pipeline + readers, wired the same way for live play and replay.

Keeping this in one place is what guarantees replay behaves like live: both call build_engine().
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TextIO

from engineer.analyzers.coaching import CoachingSummary
from engineer.analyzers.corners import CornerAnalyzer
from engineer.analyzers.lap_delta import LapDeltaAnalyzer
from engineer.analyzers.racecraft import RaceCraftAnalyzer
from engineer.analyzers.strategy import StrategyAnalyzer
from engineer.analyzers.tyres import TyreAnalyzer
from engineer.bus import events as ev
from engineer.config import Config
from engineer.console import LapPrinter, RadioPrinter
from engineer.features.lap_features import LapFeatureBuilder
from engineer.features.laps import LapHistory
from engineer.features.pit_loss import PitLossMeter
from engineer.features.priors import WearPriors
from engineer.pipeline import Pipeline
from engineer.radio.director import RadioDirector
from engineer.radio.engineers import personality
from engineer.radio.variety import VARIETY
from engineer.report.focus import load_focus
from engineer.state.traces import TraceRecorder
from engineer.state.track_maps import TrackMaps
from engineer.strategy.runner import InlineRunner
from engineer.voice.answers import QuestionAnswerer
from engineer.voice.feedback import FeedbackLog


@dataclass
class Engine:
    pipeline: Pipeline
    history: LapHistory
    director: RadioDirector
    lap_delta: LapDeltaAnalyzer
    features: LapFeatureBuilder
    tyres: TyreAnalyzer
    traces: TraceRecorder
    corners: CornerAnalyzer
    strategy: StrategyAnalyzer
    answers: QuestionAnswerer | None = None
    racecraft: RaceCraftAnalyzer | None = None
    coaching: CoachingSummary | None = None
    default_tempo: str = "normal"        # what "back to normal" returns to (radio.verbosity)
    tempo: str = "normal"                # "terse" (shown as Quiet), "normal", "chatty", "coaching"

    def set_tempo(self, tempo: str) -> None:
        """Change how the engineer talks, live (the app window or "talk more" on the radio).
        "coaching" talks like normal, but the corner calls compare every lap with the fastest
        lap on track and the race-craft chatter is left out. Also ends radio silence."""
        if tempo not in TEMPOS:
            raise ValueError(f"unknown tempo {tempo!r}; one of {', '.join(TEMPOS)}")
        self.tempo = tempo
        base = "normal" if tempo == "coaching" else tempo
        for part in (self.lap_delta, self.tyres, self.corners, self.strategy, self.answers):
            if part is not None:
                part.verbosity = base
        self.corners.coaching = tempo == "coaching"
        self.director.tempo = tempo
        self.director.quiet = False

    def idle_tick(self) -> None:
        """Live, while no packets come (menus, car setup): pick up strategy answers from the
        worker (normally done after each packet), e.g. the pre-race plan."""
        self.strategy._drain()

    def set_detail(self, detail: str) -> None:
        """How corner causes are said: "plain" ("brake slightly later") or "specific"
        ("braking fifteen metres earlier"). Live, from the app window or by voice."""
        if detail not in ("plain", "specific"):
            raise ValueError(f"unknown detail {detail!r}")
        self.corners.detail = detail
        if self.answers is not None:
            self.answers.detail = detail


TEMPOS = ("terse", "normal", "chatty", "coaching")


def build_engine(cfg: Config, out: TextIO | None = None,
                 priors: WearPriors | None = None, strategy_runner=None) -> Engine:
    """priors: tyre wear history; by default read from data/features/stints.parquet.
    strategy_runner: where strategy sims run. Default: in-process (replays and tests, so the same
    packets always give the same calls); live play passes a WorkerRunner (a separate process)."""
    pipeline = Pipeline()
    bus, state = pipeline.bus, pipeline.state
    # First, so a new session's calls are already worded from its own seed.
    bus.subscribe(ev.SessionStarted, lambda e: VARIETY.new_session(e.session_uid))
    # Analyzers know terse/normal/chatty; coaching is set up by set_tempo at the end.
    talk = "normal" if cfg.radio.verbosity == "coaching" else cfg.radio.verbosity
    # Order matters: the bus calls handlers in subscription order, so the lap history must store
    # a lap before the analyzer asks about it.
    # The lap line is printed before any radio call about that lap.
    history = LapHistory(bus, state, arrival_ns=lambda: pipeline.last_arrival_ns)
    features = LapFeatureBuilder(bus, state, temp_window_c=cfg.tyres.temp_window_c)
    pipeline.after_packet.append(features.after_packet)
    traces = TraceRecorder(state)
    bus.subscribe(ev.SessionStarted, lambda e: traces.reset())
    bus.subscribe(ev.Flashback, lambda e: traces.rewind())
    pipeline.after_packet.append(traces.after_packet)
    LapPrinter(bus, state, out=out)
    RadioPrinter(bus, out=out)
    director = RadioDirector(bus, state, cooldowns_s=cfg.radio.cooldowns_s)
    pipeline.after_packet.append(director.after_packet)
    analyzer = LapDeltaAnalyzer(bus, state, history, director,
                                verbosity=talk,
                                max_wait_s=cfg.radio.lap_delta_max_wait_s)
    # After the lap delta analyzer: on the same lap, the lap delta is submitted (and said) first.
    if priors is None:
        priors = WearPriors.load(cfg.features_path)
    tyres = TyreAnalyzer(bus, state, features, director, cfg.tyres, priors=priors,
                         verbosity=talk)
    # Last: corner coaching is the least urgent call of a lap.
    # What the driver report found (data/reports/focus.json, written with each report).
    focus = load_focus(cfg.data_path / "reports") if cfg.corners.report_focus else None
    corners = CornerAnalyzer(bus, state, history, traces, TrackMaps(cfg.tracks_path), director,
                             cfg.corners, verbosity=talk, features=features,
                             detail=cfg.radio.detail, focus=focus)
    pipeline.after_packet.append(corners.after_packet)
    if strategy_runner is None:
        strategy_runner = InlineRunner(cfg.features_path)
    strategy = StrategyAnalyzer(bus, state, features, director, strategy_runner, cfg.strategy,
                                cfg.tyres, verbosity=talk)
    # Pit stops timed with the game's pit lane timer (before strategy: a stop is measured on the
    # packet that ends it).
    pit_meter = PitLossMeter(bus, state)
    pipeline.after_packet.append(pit_meter.after_packet)
    strategy.pit_meter = pit_meter
    pipeline.after_packet.append(strategy.after_packet)
    racecraft = RaceCraftAnalyzer(bus, state, director, cfg.racecraft)
    pipeline.after_packet.append(racecraft.after_packet)
    # After race craft: on the last lap the finish call comes first, then the coaching summary.
    coaching = CoachingSummary(bus, state, corners, director)
    engine = Engine(pipeline, history, director, analyzer, features, tyres, traces, corners,
                    strategy)
    # Push-to-talk answers: questions arrive as QuestionAsked events (live from the speech
    # process; in tests published directly), so they're answered the same way everywhere.
    log_path = cfg.ptt_path / "questions.jsonl" if cfg.voice.log else None
    engine.racecraft = racecraft
    engine.coaching = coaching
    engine.default_tempo = cfg.radio.verbosity
    # "Log that" on the radio: the feedback log (data/feedback/). Off when the question log is
    # (replays of logged questions), so a replayed "log that" isn't logged twice.
    feedback = FeedbackLog(cfg.data_path / "feedback" if cfg.voice.log else None,
                           cfg.sessions_path)
    engine.answers = QuestionAnswerer(bus, engine, log_path, verbosity=talk,
                                      max_answer_delay_s=cfg.voice.max_answer_delay_s,
                                      feedback=feedback, detail=cfg.radio.detail)
    engine.set_tempo(cfg.radio.verbosity)
    engine.director.persona = personality(cfg.radio.engineer)
    # Wording variety: each session reseeded (live: random; replay: from the session UID).
    VARIETY.enabled = cfg.radio.variety and os.environ.get("RACE_ENGINEER_VARIETY", "1") != "0"
    VARIETY.new_session(None)
    return engine
