import numpy as np

from engineer.radio.effects import band_limit, radio_effect

SR = 22050


def test_band_limit_removes_lows_and_highs():
    t = np.arange(SR) / SR
    x = np.sin(2 * np.pi * 100 * t) + np.sin(2 * np.pi * 1000 * t) + np.sin(2 * np.pi * 6000 * t)
    y = band_limit(x, SR)
    spectrum = np.abs(np.fft.rfft(y))
    assert spectrum[1000] > 100 * spectrum[100]
    assert spectrum[1000] > 100 * spectrum[6000]


def test_radio_effect_shape_and_range():
    voice = np.random.default_rng(1).normal(0, 0.3, SR).astype(np.float32)
    out = radio_effect(voice, SR)
    assert out.dtype == np.float32
    assert len(out) > len(voice)                      # beep and squelch added
    assert np.all(np.isfinite(out)) and np.max(np.abs(out)) <= 1.0
    assert np.array_equal(out, radio_effect(voice, SR))  # deterministic
