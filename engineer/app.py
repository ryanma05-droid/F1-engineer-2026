"""Builds the engine: pipeline + readers, wired the same way for live play and replay.

Keeping this in one place is what guarantees replay behaves like live: both call build_engine().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

from engineer.analyzers.lap_delta import LapDeltaAnalyzer
from engineer.config import Config
from engineer.console import LapPrinter, RadioPrinter
from engineer.features.laps import LapHistory
from engineer.pipeline import Pipeline
from engineer.radio.director import RadioDirector


@dataclass
class Engine:
    pipeline: Pipeline
    history: LapHistory
    director: RadioDirector
    lap_delta: LapDeltaAnalyzer


def build_engine(cfg: Config, out: TextIO | None = None) -> Engine:
    pipeline = Pipeline()
    bus, state = pipeline.bus, pipeline.state
    # Order matters: the bus calls handlers in subscription order, so the lap history must store
    # a lap before the analyzer asks about it.
    # The lap line is printed before any radio call about that lap.
    history = LapHistory(bus, state, arrival_ns=lambda: pipeline.last_arrival_ns)
    LapPrinter(bus, state, out=out)
    RadioPrinter(bus, out=out)
    director = RadioDirector(bus, state, cooldowns_s=cfg.radio.cooldowns_s)
    pipeline.after_packet.append(director.after_packet)
    analyzer = LapDeltaAnalyzer(bus, state, history, director,
                                verbosity=cfg.radio.verbosity,
                                max_wait_s=cfg.radio.lap_delta_max_wait_s)
    return Engine(pipeline, history, director, analyzer)
