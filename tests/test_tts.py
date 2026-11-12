import time

from engineer.radio.tts import AudioProcess, AudioSettings


def test_audio_process_round_trip_with_null_voice():
    ap = AudioProcess(AudioSettings(voice_model=None, backend="null", show_latency=False))
    assert ap.start(wait_ready_s=30) == "null"
    t0 = time.time_ns()
    ap.say("One oh nine one.", t0)
    kind, text, latency = ap.status.get(timeout=10)
    ap.close()
    assert (kind, text) == ("spoke", "One oh nine one.")
    assert 0 <= latency < 5


def test_missing_piper_model_falls_back_without_crashing():
    ap = AudioProcess(AudioSettings(voice_model="/nonexistent/voice.onnx", backend="auto",
                                    show_latency=False))
    backend = ap.start(wait_ready_s=60)
    assert backend in ("pyttsx3", "say", "null")
    # The system voice needs a sound device to finish speaking; build machines (GitHub's
    # Windows runners) have none, so the spoken round trip is only checked for the null voice.
    if backend == "null":
        ap.say("Radio check.")
        kind, text, _ = ap.status.get(timeout=30)
        assert kind == "spoke"
    ap.close(timeout_s=5)


def test_piper_render_uses_chunks_and_applies_effect():
    import numpy as np
    from piper import AudioChunk

    from engineer.radio.tts import PiperBackend

    class FakeVoice:
        def synthesize(self, text):
            for _ in range(2):
                audio = np.full(2205, 0.1, dtype=np.float32)
                yield AudioChunk(sample_rate=22050, sample_width=2, sample_channels=1,
                                 audio_float_array=audio, phonemes=[], phoneme_ids=[])

    b = object.__new__(PiperBackend)       # skip loading a real model / opening a sound device
    b.voice, b._seed = FakeVoice(), 0
    b.settings = AudioSettings(voice_model=None, radio_effect=False, volume=0.5)
    audio, sr = b.render("Box box.")
    assert sr == 22050 and len(audio) == 4410 and np.allclose(audio, 0.05)
    b.settings.radio_effect = True
    audio_fx, _ = b.render("Box box.")
    assert len(audio_fx) > 4410


def test_resolve_output_device(monkeypatch):
    from engineer.radio import tts

    fake = [(3, "Speakers (Realtek Audio)", True), (7, "Headphones (Arctis Nova 7)", False)]
    monkeypatch.setattr(tts, "list_output_devices", lambda: fake)
    assert tts.resolve_output_device(None) == (None, "Speakers (Realtek Audio)")
    assert tts.resolve_output_device("arctis") == (7, "Headphones (Arctis Nova 7)")
    assert tts.resolve_output_device(7) == (7, "Headphones (Arctis Nova 7)")
    assert tts.resolve_output_device("7") == (7, "Headphones (Arctis Nova 7)")
    assert tts.resolve_output_device("nope") == (None, "Speakers (Realtek Audio)")


def test_volume_slider_70_is_the_old_loudness_and_100_is_louder_without_clipping():
    """4 Nov: "too low most of the time". 70 % = as before; up to +10 dB at 100 %."""
    import numpy as np
    from piper import AudioChunk

    from engineer.radio.tts import AudioSettings, PiperBackend, soft_limit, volume_gain
    assert volume_gain(70) == 1.0 and volume_gain(0) == 0.0 and volume_gain(35) == 0.5
    assert abs(volume_gain(100) - 10 ** 0.5) < 1e-9 and volume_gain(85) > 1.0
    assert volume_gain(150) == volume_gain(100)
    quiet = np.array([0.5, -0.79], dtype=np.float32)
    assert np.array_equal(soft_limit(quiet), quiet)            # below the knee: untouched
    loud = soft_limit(np.array([0.9, -1.1, 1.3], dtype=np.float32))
    assert np.all(np.abs(loud) <= 0.99) and loud[0] > 0.8 and loud[1] < -0.8
    assert loud[2] > loud[0]                                   # still in order: no flat clip

    class FakeVoice:
        def synthesize(self, text):
            yield AudioChunk(sample_rate=22050, sample_width=2, sample_channels=1,
                             audio_float_array=np.sin(np.linspace(0, 200, 22050)).astype(
                                 np.float32), phonemes=[], phoneme_ids=[])
    b = object.__new__(PiperBackend)
    b.voice, b._seed = FakeVoice(), 0
    b.settings = AudioSettings(voice_model=None, radio_effect=True, volume=volume_gain(70))
    before, _ = b.render("x")
    b._seed = 0
    b.settings.volume = volume_gain(100)
    after, _ = b.render("x")
    rms = lambda a: float(np.sqrt(np.mean(a ** 2)))
    assert rms(after) > 1.5 * rms(before) and np.max(np.abs(after)) <= 0.99


def test_excited_calls_are_said_faster_livelier_and_louder():
    # 12 Nov: "more energetic calls in moments that matter". The excited flag reaches the audio
    # process and Piper uses its own synthesis settings and a little more gain.
    import numpy as np
    from piper import AudioChunk

    from engineer.radio.tts import EXCITED, PiperBackend

    seen = []

    class FakeVoice:
        def synthesize(self, text, syn_config=None):
            seen.append(syn_config)
            yield AudioChunk(sample_rate=22050, sample_width=2, sample_channels=1,
                             audio_float_array=np.full(100, 0.1, dtype=np.float32),
                             phonemes=[], phoneme_ids=[])

    b = object.__new__(PiperBackend)
    b.voice, b._seed, b.syn = FakeVoice(), 0, None
    b.settings = AudioSettings(voice_model=None, radio_effect=False, volume=0.5)
    b.syn_excited = "excited-config"
    calm, _ = b.render("Great move!")
    loud, _ = b.render("Great move!", excited=True)
    assert seen == [None, "excited-config"]
    assert np.allclose(loud, calm * EXCITED["gain"])
    # The queue carries the flag; an old two-item message still works.
    ap = AudioProcess(AudioSettings(voice_model=None, backend="null", show_latency=False))
    assert ap.start(wait_ready_s=30) == "null"
    ap.say("Great move on Norris!", None, excited=True)
    ap._calls.put(("Radio check.", None))
    assert ap.status.get(timeout=10)[:2] == ("spoke", "Great move on Norris!")
    assert ap.status.get(timeout=10)[:2] == ("spoke", "Radio check.")
    ap.close()
