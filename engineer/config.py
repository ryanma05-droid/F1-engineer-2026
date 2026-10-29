"""Loads config.yaml into a validated settings object.

pydantic checks types for us, so a typo like `port: "abc"` fails at startup with a clear message
instead of deep inside the listener.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Installed app (PyInstaller): the read-only files ship inside the bundle, while settings and data
# live in the user's own folder (%LOCALAPPDATA%\RaceEngineer on Windows, ~/Library/Application
# Support/RaceEngineer on a Mac), never in the install folder.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def _app_home() -> Path:
    if not FROZEN:
        return PROJECT_ROOT
    if sys.platform == "darwin":                 # macOS: ~/Library/Application Support
        return Path.home() / "Library" / "Application Support" / "RaceEngineer"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "RaceEngineer"


APP_HOME = _app_home()
DEFAULT_CONFIG = BUNDLE_DIR / "config.yaml"


class Endpoint(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)


class Radio(BaseModel):
    enabled: bool = True
    verbosity: Literal["terse", "normal", "chatty"] = "normal"
    voice: str = "en_GB-northern_english_male-medium"
    radio_effect: bool = True
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    output_device: str | int | None = None   # None = Windows default; part of a name, or index
    lap_delta_max_wait_s: float = Field(default=3.0, gt=0)
    cooldowns_s: dict[str, float] = {"lap_delta": 5.0, "tyre_status": 30.0, "tyre_warning": 10.0,
                                     "tyre_temp": 10.0, "tyre_dropoff": 60.0,
                                     "corner": 30.0, "strategy_plan": 20.0, "box": 0.0,
                                     "undercut": 30.0, "racecraft_defend": 30.0, "racecraft_attack": 60.0}


class Tyres(BaseModel):
    enabled: bool = True
    limit_pct: float = Field(default=60.0, gt=0, le=100)           # "laps left" = laps to this
    laps_left_from_pct: float = Field(default=25.0, ge=0, le=100)  # start saying laps left here
    status_every_laps: int = Field(default=3, ge=1)
    wear_warnings_pct: list[float] = [40.0, 50.0, 60.0]
    temp_calls: bool = True
    temp_window_c: tuple[float, float] = (80.0, 110.0)    # carcass (inner) temp, slicks
    temp_min_fraction: float = Field(default=0.6, gt=0, le=1)
    dropoff_ms_per_lap: float = Field(default=150.0, gt=0)
    fuel_s_per_kg: float = Field(default=0.03, ge=0)
    max_wait_s: float = Field(default=20.0, gt=0)


class Corners(BaseModel):
    enabled: bool = True
    min_loss_s: float = Field(default=0.1, gt=0)      # only call a corner losing at least this
    nearby_gap_s: float = Field(default=2.0, ge=0)    # compare with the car ahead/behind within
    focus_after_laps: int = Field(default=2, ge=2)   # same corner losing this many laps: priority
    ref_wear_window_pct: float = Field(default=8.0, gt=0)   # your pace = laps with wear this close
    back_off_after: int = Field(default=3, ge=1)     # same corner called this often: every other lap
    battle_gap_s: float = Field(default=1.0, ge=0)   # car this close ahead/behind: no coaching
    max_wait_s: float = Field(default=25.0, gt=0)


class RaceCraft(BaseModel):
    enabled: bool = True             # battles, passes, start/finish, penalties (races only)
    start: bool = True
    passes: bool = True
    battles: bool = True
    countdown: bool = True
    finish: bool = True
    fastest_lap: bool = True
    penalties: bool = True
    safety_car: bool = True          # "safety car in this lap"
    damage: bool = True              # damage after contact
    pass_hold_s: float = Field(default=4.0, ge=0)      # a pass must stick this long
    defend_gap_s: float = Field(default=1.0, gt=0)     # car behind inside this and closing
    clear_gap_s: float = Field(default=2.0, gt=0)      # ... until it drops back past this
    attack_gap_s: float = Field(default=4.0, gt=0)     # car ahead within this ...
    min_closing_s: float = Field(default=0.15, gt=0)   # ... and you close this much a lap


class Strategy(BaseModel):
    enabled: bool = True
    min_race_laps: int = Field(default=8, ge=1)       # no strategy calls in shorter races
    announce_after_lap: int = Field(default=2, ge=1)  # first plan once a wear rate is measured
    max_stops: int = Field(default=3, ge=1, le=5)
    window_s: float = Field(default=1.0, ge=0)        # pit window: stop laps within this of best
    replan_laps: int = Field(default=2, ge=1)         # "plan change" if the stop moves this much
    over_limit_s_per_pct: float = Field(default=1.0, ge=0)   # cost of each lap past tyres.limit_pct
    min_gain_s: float = Field(default=3.0, ge=0)   # an extra stop must save this much to be the plan
    # Pit stop time loss, used until a stop at this track has been measured from real laps.
    pit_loss_s: float = Field(default=22.0, gt=0)
    sc_pit_loss_factor: float = Field(default=0.5, gt=0, le=1)   # under SC/VSC, until measured
    two_compound_rule: Literal["auto", "on", "off"] = "auto"
    two_compound_min_laps: int = Field(default=10, ge=1)          # "auto": races this long
    ai_weight: float = Field(default=0.5, ge=0, le=1)   # the AI field counts as this much of a driver
    game_prior_laps: float = Field(default=5.0, ge=0)        # the game's estimate counts as N laps
    game_deg_sd: float = Field(default=0.02, gt=0)   # how far off the game's deg figure may be
    fallback_deg_s_per_pct: float = Field(default=0.05, ge=0)   # only when there's no data at all
    assume_sets_per_compound: int = Field(default=2, ge=1)   # if the game sends no tyre sets
    undercut_reminder: bool = True
    undercut_gap_s: float = Field(default=3.0, ge=0)
    rejoin_traffic_s: float = Field(default=1.5, ge=0)   # "you'll come out behind X" within this
    max_wait_s: float = Field(default=20.0, gt=0)
    # Committing to a pit lap: within commit_laps of the stop, it only moves if the new best is
    # change_gain_s quicker (or the tyres won't make it, or a safety car).
    commit_laps: int = Field(default=3, ge=0)
    change_gain_s: float = Field(default=1.5, ge=0)
    # Safe call vs gamble (strategy/decision.py): a safe plan ends every stint margin_pct under
    # the wear limit, allowing for how sure the wear rate is.
    margin_pct: float = Field(default=5.0, ge=0)
    rate_sd_one_lap: float = Field(default=0.3, ge=0)
    gamble_min_s: float = Field(default=1.0, ge=0)   # quicker by less: not worth naming
    gamble_take_s: float = Field(default=5.0, ge=0)  # quicker by more: the gamble is the call


class Voice(BaseModel):
    enabled: bool = True             # push-to-talk questions
    controller_type: Literal["xbox", "playstation"] = "xbox"
    button: str | None = None        # None: LB (Xbox) / L1 (PlayStation). Or RB, A, R1, CROSS, ...
    ps_button_number: int | None = None   # PlayStation: the raw button number, if L1 isn't found
    controller: int | None = None    # None = any connected controller
    # base.en: small.en took ~3 s per question on the CPU in a race (27 Oct); base.en is ~3x faster.
    model: str = "base.en"           # faster-whisper model: base.en, small.en, medium.en, ...
    # CPU, not the GPU: in a race F1 25 keeps the GPU busy and a question waited 41 s for it
    # (real race, 27 Oct). "auto" = the Nvidia GPU if it works, for when the game isn't running.
    device: Literal["auto", "cuda", "cpu"] = "cpu"
    cpu_threads: int = Field(default=4, ge=1)   # leaves the rest of the CPU to the game
    compute_type: str = "auto"
    beam_size: int = Field(default=1, ge=1, le=10)
    max_answer_delay_s: float = Field(default=10.0, gt=0)   # later than this: not answered
    input_device: str | int | None = None   # None = Windows default recording device
    min_press_s: float = Field(default=0.25, ge=0)   # shorter taps are ignored
    max_question_s: float = Field(default=10.0, gt=0)
    keep_audio: bool = True          # save each question to data/ptt/clips/
    log: bool = True                 # log every question to data/ptt/questions.jsonl


class Ui(BaseModel):
    enabled: bool = True             # the app window (a page served on this PC only)
    port: int = Field(default=20780, ge=1024, le=65535)
    open: bool = True                # open the window when the engineer starts
    setup_done: bool = False         # the first-time questions (controller, tempo) were answered


class Report(BaseModel):
    daily: bool = True              # build the driver report when `engineer run` stops, once a day
    open_when_ready: bool = True    # open the PDF when the daily report is done


class Upload(BaseModel):
    auto: bool = False          # push new recordings to GitHub when `engineer run` stops
    remote: str = "origin"
    min_mb: float = Field(default=2.0, ge=0)   # skip tiny recordings (menus, a few seconds)


class Config(BaseModel):
    platform: Literal["pc", "console"] = "pc"   # where F1 25 runs (console: telemetry over Wi-Fi)
    listen: Endpoint = Endpoint(host="0.0.0.0", port=20777)
    forward: list[Endpoint] = []
    data_dir: Path = Path("data")
    record: bool = True
    # Add each run's recordings to data/features/ when `engineer run` stops (the installed app;
    # in the repo you run `python -m engineer.features build` yourself).
    features_auto_add: bool = FROZEN
    radio: Radio = Radio()
    tyres: Tyres = Tyres()
    corners: Corners = Corners()
    strategy: Strategy = Strategy()
    racecraft: RaceCraft = RaceCraft()
    voice: Voice = Voice()
    upload: Upload = Upload()
    report: Report = Report()
    ui: Ui = Ui()

    @property
    def data_path(self) -> Path:
        """data_dir resolved against the project folder when it is relative."""
        return self.data_dir if self.data_dir.is_absolute() else APP_HOME / self.data_dir

    @property
    def sessions_path(self) -> Path:
        return self.data_path / "sessions"

    @property
    def features_path(self) -> Path:
        return self.data_path / "features"

    @property
    def tracks_path(self) -> Path:
        return self.data_path / "tracks"

    @property
    def ptt_path(self) -> Path:
        """Push-to-talk: question log, saved clips, speech model."""
        return self.data_path / "ptt"

    @property
    def voices_path(self) -> Path:
        return self.data_path / "voices"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def local_config_path(path: Path | str | None = None) -> Path:
    """Where this PC's own settings live (config.local.yaml); see load_config."""
    path = Path(path) if path else DEFAULT_CONFIG
    return APP_HOME / "config.local.yaml" if FROZEN and path == DEFAULT_CONFIG \
        else path.with_name("config.local.yaml")


def save_local(updates: dict, path: Path | str | None = None) -> Path:
    """Merge updates into config.local.yaml (other settings in it are kept)."""
    local = local_config_path(path)
    raw = {}
    if local.exists():
        raw = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(yaml.safe_dump(_merge(raw, updates), sort_keys=False), encoding="utf-8")
    return local


def load_config(path: Path | str | None = None) -> Config:
    """config.yaml, then config.local.yaml next to it (if present) on top.

    config.local.yaml holds settings for this PC only (e.g. auto-upload, the audio device). git
    ignores it, so `git pull` never clashes with it. In the installed app it's
    %LOCALAPPDATA%\\RaceEngineer\\config.local.yaml."""
    path = Path(path) if path else DEFAULT_CONFIG
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # The installed app keeps its local settings in the user's folder (written by first-run setup).
    local = local_config_path(path)
    if local.exists():
        raw = _merge(raw, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return Config.model_validate(raw)
