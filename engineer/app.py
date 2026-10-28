"""Builds the engine: pipeline + readers, wired the same way for live play and replay.

Keeping this in one place is what guarantees replay behaves like live: both call build_engine().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

from engineer.analyzers.corners import CornerAnalyzer
from engineer.analyzers.lap_delta import LapDeltaAnalyzer
from engineer.analyzers.racecraft import RaceCraftAnalyzer
from engineer.analyzers.strategy import StrategyAnalyzer
from engineer.analyzers.tyres import TyreAnalyzer
from engineer.config import Config
from engineer.console import LapPrinter, RadioPrinter
from engineer.features.lap_features import LapFeatureBuilder
from engineer.features.laps import LapHistory
from engineer.features.priors import WearPriors
from engineer.pipeline import Pipeline
from engineer.bus import events as ev
from engineer.radio.director import RadioDirector
from engineer.state.track_maps import TrackMaps
from engineer.state.traces import TraceRecorder
from engineer.strategy.runner import InlineRunner
from engineer.voice.answers import QuestionAnswerer


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
    default_tempo: str = "normal"        # what "back to normal" returns to (radio.verbosity)

    @property
    def tempo(self) -> str:
        """How much the engineer says: "terse" (shown as Quiet), "normal" or "chatty"."""
        return self.lap_delta.verbosity

    def set_tempo(self, tempo: str) -> None:
        """Change how much the engineer says, live (the app window or "talk more" on the radio).
        Also ends radio silence ("be quiet")."""
        if tempo not in TEMPOS:
            raise ValueError(f"unknown tempo {tempo!r}; one of {', '.join(TEMPOS)}")
        for part in (self.lap_delta, self.tyres, self.corners, self.strategy, self.answers):
            if part is not None:
                part.verbosity = tempo
        self.director.tempo = tempo
        self.director.quiet = False


TEMPOS = ("terse", "normal", "chatty")


def build_engine(cfg: Config, out: TextIO | None = None,
                 priors: WearPriors | None = None, strategy_runner=None) -> Engine:
    """priors: tyre wear history; by default read from data/features/stints.parquet.
    strategy_runner: where strategy sims run. Default: in-process (replays and tests, so the same
    packets always give the same calls); live play passes a WorkerRunner (a separate process)."""
    pipeline = Pipeline()
    bus, state = pipeline.bus, pipeline.state
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
                                verbosity=cfg.radio.verbosity,
                                max_wait_s=cfg.radio.lap_delta_max_wait_s)
    # After the lap delta analyzer: on the same lap, the lap delta is submitted (and said) first.
    if priors is None:
        priors = WearPriors.load(cfg.features_path)
    tyres = TyreAnalyzer(bus, state, features, director, cfg.tyres, priors=priors,
                         verbosity=cfg.radio.verbosity)
    # Last: corner coaching is the least urgent call of a lap.
    corners = CornerAnalyzer(bus, state, history, traces, TrackMaps(cfg.tracks_path), director,
                             cfg.corners, verbosity=cfg.radio.verbosity, features=features)
    pipeline.after_packet.append(corners.after_packet)
    if strategy_runner is None:
        strategy_runner = InlineRunner(cfg.features_path)
    strategy = StrategyAnalyzer(bus, state, features, director, strategy_runner, cfg.strategy,
                                cfg.tyres, verbosity=cfg.radio.verbosity)
    pipeline.after_packet.append(strategy.after_packet)
    racecraft = RaceCraftAnalyzer(bus, state, director, cfg.racecraft)
    pipeline.after_packet.append(racecraft.after_packet)
    engine = Engine(pipeline, history, director, analyzer, features, tyres, traces, corners,
                    strategy)
    # Push-to-talk answers: questions arrive as QuestionAsked events (live from the speech
    # process; in tests published directly), so they're answered the same way everywhere.
    log_path = cfg.ptt_path / "questions.jsonl" if cfg.voice.log else None
    engine.racecraft = racecraft
    engine.default_tempo = cfg.radio.verbosity
    director.tempo = cfg.radio.verbosity
    engine.answers = QuestionAnswerer(bus, engine, log_path, verbosity=cfg.radio.verbosity,
                                      max_answer_delay_s=cfg.voice.max_answer_delay_s)
    return engine
