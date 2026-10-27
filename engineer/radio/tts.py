"""Audio process: turns radio calls into speech, in a separate process.

Why a separate process: synthesizing and playing speech takes hundreds of milliseconds. Doing that
on the main loop would stall telemetry, so the main process only puts text on a queue and this
process does the slow part.

Voices, best first:
1. Piper (a neural voice, runs offline) with the model in data/voices/, plus the radio effect.
2. The computer's own voice, used if Piper or the model is missing, or audio playback through
   sounddevice fails: pyttsx3 on Windows, the `say` command on a Mac. No radio effect.
3. Text only: prints a warning once and stays silent.

Each call carries the arrival time of the packet that triggered it, so this process can print the
end-to-end latency (line crossed -> voice starts), which is how the Phase 1 "within 1 second"
target is checked live.
"""

from __future__ import annotations

import logging
import queue as queue_mod
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engineer.workers.pool import start_worker

log = logging.getLogger(__name__)


@dataclass
class AudioSettings:
    voice_model: str | None          # path to the Piper .onnx file
    radio_effect: bool = True
    volume: float = 1.0
    backend: str = "auto"            # "auto", "piper", "pyttsx3" or "null" (tests)
    show_latency: bool = True
    output_device: str | int | None = None   # None = Windows default; name part or index


def list_output_devices() -> list[tuple[int, str, bool]]:
    """(index, name, is_default) for every audio output device."""
    import sounddevice as sd

    default_out = sd.default.device[1]
    return [(i, d["name"], i == default_out)
            for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]


def resolve_output_device(spec: str | int | None) -> tuple[int | None, str]:
    """Turn the config value into a sounddevice index (None = default) and a readable name."""
    devices = list_output_devices()
    if spec is None or spec == "":
        default = next((d for d in devices if d[2]), None)
        return None, (default[1] if default else "system default")
    if isinstance(spec, int) or str(spec).isdigit():
        idx = int(spec)
        match = next((d for d in devices if d[0] == idx), None)
    else:
        match = next((d for d in devices if str(spec).lower() in d[1].lower()), None)
    if match is None:
        print(f"[radio] output device {spec!r} not found; using the default. "
              f"List devices with: python -m engineer.radio.setup_voice --devices", flush=True)
        return resolve_output_device(None)
    return match[0], match[1]


# -- backends (run inside the audio process) ---------------------------------------------------

class PiperBackend:
    name = "piper"

    def __init__(self, settings: AudioSettings):
        from piper import PiperVoice   # imported here: heavy, and only needed in this process
        import sounddevice              # noqa: F401  (fail now, not at the first call)

        if not settings.voice_model or not Path(settings.voice_model).exists():
            raise FileNotFoundError(
                f"Piper voice model not found: {settings.voice_model}. "
                f"Run: python -m engineer.radio.setup_voice")
        self.settings = settings
        self.device, self.device_name = resolve_output_device(settings.output_device)
        self.voice = PiperVoice.load(settings.voice_model)
        self._seed = 0
        self.render("Radio check.")    # warm-up: the first synthesis is much slower

    def render(self, text: str) -> tuple[np.ndarray, int]:
        chunks = list(self.voice.synthesize(text))
        sr = chunks[0].sample_rate
        audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        if self.settings.radio_effect:
            from engineer.radio.effects import radio_effect
            self._seed += 1
            audio = radio_effect(audio, sr, seed=self._seed)
        return audio * self.settings.volume, sr

    def speak(self, text: str, on_start) -> None:
        import sounddevice as sd

        audio, sr = self.render(text)
        on_start()
        sd.play(audio, sr, device=self.device)
        sd.wait()


class Pyttsx3Backend:
    name = "pyttsx3"

    def __init__(self, settings: AudioSettings):
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("volume", max(0.0, min(1.0, settings.volume)))

    def speak(self, text: str, on_start) -> None:
        on_start()
        self.engine.say(text)
        self.engine.runAndWait()


class SayBackend:
    """The Mac's built-in voice (the `say` command)."""
    name = "say"

    def __init__(self, settings: AudioSettings):
        import shutil

        if sys.platform != "darwin" or shutil.which("say") is None:
            raise RuntimeError("the say command is macOS only")

    def speak(self, text: str, on_start) -> None:
        import subprocess

        on_start()
        subprocess.run(["say", text], check=False, timeout=30)


class NullBackend:
    """No audio: used in tests, and as the last resort."""
    name = "null"

    def __init__(self, settings: AudioSettings):
        pass

    def speak(self, text: str, on_start) -> None:
        on_start()


def _make_backend(settings: AudioSettings):
    system_voice = SayBackend if sys.platform == "darwin" else Pyttsx3Backend
    order = {"auto": [PiperBackend, system_voice], "piper": [PiperBackend],
             "pyttsx3": [Pyttsx3Backend], "say": [SayBackend],
             "null": [NullBackend]}[settings.backend]
    for cls in order:
        try:
            return cls(settings)
        except Exception as exc:   # missing package, model or sound device
            print(f"[radio] {cls.name} voice unavailable: {exc}", flush=True)
    print("[radio] No voice available; radio calls will be printed only.", flush=True)
    return NullBackend(settings)


def audio_main(calls, status, settings: AudioSettings) -> None:
    """Entry point of the audio process. `calls` receives (text, origin_t_ns) or None to stop.
    `status` receives ("ready", backend) and ("spoke", text, latency_s) messages."""
    # Ctrl+C reaches every process in the console on Windows. Ignore it here: the main process
    # shuts us down cleanly by sending None.
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    backend = _make_backend(settings)
    status.put(("ready", backend.name, getattr(backend, "device_name", "Windows default")))
    while True:
        item = calls.get()
        if item is None:
            break
        text, origin_t_ns = item
        latency = {}

        def on_start():
            if origin_t_ns:
                latency["s"] = (time.time_ns() - origin_t_ns) / 1e9

        try:
            backend.speak(text, on_start)
        except Exception as exc:
            # e.g. the sound device disappeared. Piper -> Windows voice -> text only.
            print(f"[radio] could not speak ({exc}); switching to a fallback voice", flush=True)
            backend = _fallback(settings) if backend.name == "piper" else NullBackend(settings)
            try:
                backend.speak(text, on_start)
            except Exception:
                backend = NullBackend(settings)
        lat = latency.get("s")
        if settings.show_latency and lat is not None and backend.name != "null":
            print(f"        (voice started {lat:.2f}s after the packet arrived)", flush=True)
        status.put(("spoke", text, lat))


def _fallback(settings: AudioSettings):
    try:
        return SayBackend(settings) if sys.platform == "darwin" else Pyttsx3Backend(settings)
    except Exception:
        return NullBackend(settings)


# -- main-process side -------------------------------------------------------------------------

class AudioProcess:
    """Owns the audio worker. `say()` never blocks the caller."""

    def __init__(self, settings: AudioSettings):
        self.settings = settings
        self._proc = None
        self._calls = None
        self.status = None
        self.backend: str | None = None
        self.device_name: str | None = None

    def start(self, wait_ready_s: float = 30.0) -> str | None:
        """Start the process and wait (up to wait_ready_s) for the voice to load."""
        self._proc, self._calls, self.status = start_worker(audio_main, self.settings)
        try:
            msg = self.status.get(timeout=wait_ready_s)
            self.backend, self.device_name = msg[1], msg[2]
        except queue_mod.Empty:
            log.warning("audio process did not report ready within %.0fs", wait_ready_s)
        return self.backend

    def say(self, text: str, origin_t_ns: int | None = None) -> None:
        if self._calls is not None:
            self._calls.put((text, origin_t_ns))

    def close(self, timeout_s: float = 5.0) -> None:
        if self._proc is None:
            return
        self._calls.put(None)
        self._proc.join(timeout_s)
        if self._proc.is_alive():
            self._proc.terminate()
        self._proc = None


def voice_model_path(voices_dir: Path, voice: str) -> Path:
    return Path(voices_dir) / f"{voice}.onnx"


if __name__ == "__main__":   # quick manual check: python -m engineer.radio.tts "Box box."
    from engineer.config import load_config

    cfg = load_config()
    ap = AudioProcess(AudioSettings(str(voice_model_path(cfg.voices_path, cfg.radio.voice)),
                                    cfg.radio.radio_effect, cfg.radio.volume))
    print("voice:", ap.start())
    ap.say(" ".join(sys.argv[1:]) or "Radio check.", time.time_ns())
    ap.close(timeout_s=30)
