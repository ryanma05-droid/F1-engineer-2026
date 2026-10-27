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
    cooldowns_s: dict[str, float] = {"lap_delta": 5.0}


class Config(BaseModel):
    listen: Endpoint = Endpoint(host="0.0.0.0", port=20777)
    forward: list[Endpoint] = []
    data_dir: Path = Path("data")
    record: bool = True
    radio: Radio = Radio()

    @property
    def data_path(self) -> Path:
        """data_dir resolved against the project folder when it is relative."""
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir

    @property
    def sessions_path(self) -> Path:
        return self.data_path / "sessions"

    @property
    def voices_path(self) -> Path:
        return self.data_path / "voices"


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        return Config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)
