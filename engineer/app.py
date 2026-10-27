"""Builds the engine: pipeline + readers, wired the same way for live play and replay.

Keeping this in one place is what guarantees replay behaves like live: both call build_engine().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TextIO

from engineer.analyzers.lap_delta import LapDeltaAnalyzer
from engineer.analyzers.tyres import TyreAnalyzer
from engineer.config import Config
from engineer.console import LapPrinter, RadioPrinter
from engineer.features.lap_features import LapFeatureBuilder
from engineer.features.laps import LapHistory
from engineer.features.priors import WearPriors
from engineer.pipeline import Pipeline
from engineer.radio.director import RadioDirector


@dataclass
class Engine:
    pipeline: Pipeline
    history: LapHistory
    director: RadioDirector
    lap_delta: LapDeltaAnalyzer
    features: LapFeatureBuilder
    tyres: TyreAnalyzer


def build_engine(cfg: Config, out: TextIO | None = None,
                 priors: WearPriors | None = None) -> Engine:
    """priors: tyre wear history; by default read from data/features/stints.parquet."""
    pipeline = Pipeline()
    bus, state = pipeline.bus, pipeline.state
    # Order matters: the bus calls handlers in subscription order, so the lap history must store
    # a lap before the analyzer asks about it.
    # The lap line is printed before any radio call about that lap.
    history = LapHistory(bus, state, arrival_ns=lambda: pipeline.last_arrival_ns)
    features = LapFeatureBuilder(bus, state, temp_window_c=cfg.tyres.temp_window_c)
    pipeline.after_packet.append(features.after_packet)
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
    return Engine(pipeline, history, director, analyzer, features, tyres)
