"""A light "team radio" sound: squelch beep, band-limited voice, a touch of grit and static.

Real pit-to-car radio carries roughly 300-3400 Hz, so we cut the lows and highs, compress the voice
a little so it sits forward, and add faint static. Short tones mark the start and end of a
transmission. All plain numpy (FFT filtering), so no extra audio libraries are needed.
"""

from __future__ import annotations

import numpy as np


def _smoothstep(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def band_limit(x: np.ndarray, sr: int, low_hz=300.0, high_hz=3400.0) -> np.ndarray:
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    mask = _smoothstep(freqs, low_hz * 0.6, low_hz) * (1.0 - _smoothstep(freqs, high_hz, high_hz * 1.15))
    return np.fft.irfft(spectrum * mask, n=len(x))


def _fade(x: np.ndarray, sr: int, ms: float = 5.0) -> np.ndarray:
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n:
        ramp = np.linspace(0.0, 1.0, n)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def tone(sr: int, hz: float, ms: float, level: float = 0.25) -> np.ndarray:
    t = np.arange(int(sr * ms / 1000)) / sr
    return _fade(level * np.sin(2 * np.pi * hz * t), sr)


def silence(sr: int, ms: float) -> np.ndarray:
    return np.zeros(int(sr * ms / 1000))


def squelch_tail(sr: int, rng: np.random.Generator, ms: float = 90.0) -> np.ndarray:
    n = int(sr * ms / 1000)
    noise = band_limit(rng.normal(0.0, 1.0, n), sr, 500.0, 3000.0)
    noise *= np.linspace(1.0, 0.0, n) ** 2
    return _fade(0.12 * noise / (np.max(np.abs(noise)) + 1e-9), sr, 3.0)


def radio_effect(voice: np.ndarray, sr: int, seed: int = 0) -> np.ndarray:
    """voice: mono float samples in [-1, 1]. Returns the processed clip, float32 in [-1, 1]."""
    rng = np.random.default_rng(seed)
    x = band_limit(np.asarray(voice, dtype=np.float64), sr)
    x /= np.max(np.abs(x)) + 1e-9
    drive = 2.0
    x = np.tanh(drive * x) / np.tanh(drive)          # gentle compression / grit
    x = band_limit(x + rng.normal(0.0, 0.006, len(x)), sr)   # faint static, also band-limited
    x *= 0.8
    out = np.concatenate([
        tone(sr, 1600.0, 70), silence(sr, 40),       # opening beep
        x,
        silence(sr, 30), squelch_tail(sr, rng),      # closing squelch
    ])
    return np.clip(out, -1.0, 1.0).astype(np.float32)
