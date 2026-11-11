"""Changing the engineer from the app window: download their voice if needed, restart the audio
process with it, and say a line so you can hear who you picked. All off the asyncio loop."""

from __future__ import annotations

import asyncio
import logging

from engineer.radio.engineers import BY_KEY, engineer, personality
from engineer.radio.tts import AudioSettings, voice_model_path

log = logging.getLogger(__name__)

VOLUME_CHECK = "Radio check. How's that?"


def current_key(cfg) -> str | None:
    """The engineer on the radio: the one picked, else the one whose voice is configured."""
    if cfg.radio.engineer in BY_KEY:
        return cfg.radio.engineer
    return next((e.key for e in BY_KEY.values() if e.voice == cfg.radio.voice
                 and e.personality is None), None)


class VoiceControl:
    def __init__(self, cfg, engine, audio=None):
        """audio: cli's AudioProcess, None when the voice is off (only the personality changes)."""
        self.cfg, self.engine, self.audio = cfg, engine, audio
        self.state = "ready" if audio is not None else "off"
        self.error: str | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        return {"engineer": current_key(self.cfg), "state": self.state, "error": self.error,
                "backend": self.audio.backend if self.audio is not None else None,
                "volume": self.cfg.radio.volume_pct, "driver_name": self.cfg.radio.driver_name}

    def set_driver_name(self, name: str | None) -> None:
        """What GP calls you; takes effect from the next call."""
        self.cfg.radio.driver_name = name
        persona = self.engine.director.persona
        if persona is not None and hasattr(persona, "name"):
            persona.name = name

    def set_volume(self, pct: int) -> None:
        """The slider: louder or quieter from the next call on, with a line to hear it by."""
        self.cfg.radio.volume_pct = pct
        if self.audio is not None:
            self.audio.set_volume(self.cfg.radio.gain)
            self.audio.say(VOLUME_CHECK)

    async def switch(self, key: str, download=None) -> None:
        """Make `key` the engineer. download(voice, folder): the voice downloader (tests)."""
        if key not in BY_KEY:
            raise ValueError(f"unknown engineer {key!r}")
        e = engineer(key)
        self.cfg.radio.engineer = key
        self.engine.director.persona = personality(key, self.cfg.radio.driver_name)
        if self.audio is None:
            return
        async with self._lock:
            if self.cfg.radio.engineer != key:         # picked someone else meanwhile
                return
            self.error = None
            model = voice_model_path(self.cfg.voices_path, e.voice)
            if not model.exists():
                self.state = "downloading"
                if download is None:
                    from engineer.radio.setup_voice import download_voice as download
                try:
                    await asyncio.to_thread(download, e.voice, self.cfg.voices_path)
                except Exception as exc:              # offline, or the voice moved
                    log.warning("could not download the voice %s: %s", e.voice, exc)
                    self.state, self.error = "failed", f"couldn't download {e.name}'s voice"
                    return
            self.state = "loading"
            r = self.cfg.radio
            backend = await asyncio.to_thread(self.audio.restart, AudioSettings(
                voice_model=str(model), radio_effect=r.radio_effect, volume=r.gain,
                output_device=r.output_device, length_scale=e.length_scale))
            self.state = "ready" if backend == "piper" else "failed"
            if backend != "piper":
                self.error = f"{e.name}'s voice didn't load; using the computer's voice"
            self.audio.say(e.sample)
