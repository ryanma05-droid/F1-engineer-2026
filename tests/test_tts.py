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
    ap.say("Radio check.")
    kind, text, _ = ap.status.get(timeout=30)
    ap.close()
    assert backend in ("pyttsx3", "null")
    assert kind == "spoke"


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
