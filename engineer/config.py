"""Loads config.yaml into a validated settings object.

pydantic checks types for us, so a typo like `port: "abc"` fails at startup with a clear message
instead of deep inside the listener.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


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
                                     "corner": 30.0}


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
    max_wait_s: float = Field(default=25.0, gt=0)


class Upload(BaseModel):
    auto: bool = False          # push new recordings to GitHub when `engineer run` stops
    remote: str = "origin"
    min_mb: float = Field(default=2.0, ge=0)   # skip tiny recordings (menus, a few seconds)


class Config(BaseModel):
    listen: Endpoint = Endpoint(host="0.0.0.0", port=20777)
    forward: list[Endpoint] = []
    data_dir: Path = Path("data")
    record: bool = True
    radio: Radio = Radio()
    tyres: Tyres = Tyres()
    corners: Corners = Corners()
    upload: Upload = Upload()

    @property
    def data_path(self) -> Path:
        """data_dir resolved against the project folder when it is relative."""
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir

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
    def voices_path(self) -> Path:
        return self.data_path / "voices"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | str | None = None) -> Config:
    """config.yaml, then config.local.yaml next to it (if present) on top.

    config.local.yaml holds settings for this PC only (e.g. auto-upload, the audio device). git
    ignores it, so `git pull` never clashes with it."""
    path = Path(path) if path else DEFAULT_CONFIG
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    local = path.with_name("config.local.yaml")
    if local.exists():
        raw = _merge(raw, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return Config.model_validate(raw)
