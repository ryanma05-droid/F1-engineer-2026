"""Picking the microphone and the engineer's output (Settings > Audio devices, 14 Nov: a
PlayStation player's headset sound came in as the microphone when he held L1)."""

import asyncio
import io
import json
import queue

import numpy as np
import yaml

from engineer.audio_devices import group, is_loopback

# Windows lists each device once per audio system: MME (0) and WASAPI (1) here.
DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2,
     "max_output_channels": 0},
    {"name": "Headset Microphone (Wireless Co", "hostapi": 0, "max_input_channels": 1,
     "max_output_channels": 0},
    {"name": "Stereo Mix (Realtek Audio)", "hostapi": 0, "max_input_channels": 2,
     "max_output_channels": 0},
    {"name": "Speakers (Realtek Audio)", "hostapi": 0, "max_input_channels": 0,
     "max_output_channels": 2},
    {"name": "Headset Earphone (Wireless Co", "hostapi": 0, "max_input_channels": 0,
     "max_output_channels": 2},
    {"name": "Headset Microphone (Wireless Controller)", "hostapi": 1, "max_input_channels": 1,
     "max_output_channels": 0},
    {"name": "Stereo Mix (Realtek Audio)", "hostapi": 0, "max_input_channels": 2,
     "max_output_channels": 0},
]


def test_devices_once_each_from_the_default_audio_system():
    got = group(DEVICES, default_in=2, default_out=3)
    assert [d["name"] for d in got["inputs"]] == [
        "Stereo Mix (Realtek Audio)", "Microsoft Sound Mapper - Input",
        "Headset Microphone (Wireless Co"]                 # default first, no repeats, MME only
    assert got["inputs"][0] == {"name": "Stereo Mix (Realtek Audio)", "default": True,
                                "loopback": True}           # the player's problem, flagged
    assert [d["name"] for d in got["outputs"]] == ["Speakers (Realtek Audio)",
                                                    "Headset Earphone (Wireless Co"]
    assert is_loopback("What U Hear (Sound Blaster)") and not is_loopback("Microphone Array")
    assert group([], -1, -1) == {"inputs": [], "outputs": []}


class FakeStream:
    active = True

    def __init__(self, callback, **kw):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def test_mic_test_and_switching_the_microphone_in_the_speech_worker(monkeypatch):
    from engineer.voice import speech
    devices = [(0, "Stereo Mix (Realtek Audio)", True), (1, "Headset Microphone", False)]
    monkeypatch.setattr(speech, "list_input_devices", lambda: devices)
    opened = []

    class SD:
        def InputStream(self, **kw):
            opened.append(kw["device"])
            stream = FakeStream(**kw)
            self.stream = stream
            return stream

        def _terminate(self):
            pass

        def _initialize(self):
            pass

    sd = SD()
    mic = speech._Mic(sd, None)
    mic.open()
    assert mic.name == "Stereo Mix (Realtek Audio)"
    # Four seconds of the game's sound coming in.
    loud = np.full((512, 1), 0.1, np.float32)
    monkeypatch.setattr(speech.time, "sleep", lambda s: [sd.stream.callback(loud, 512, None, None)
                                                          for _ in range(10)])
    monkeypatch.setattr(speech, "transcribe", lambda model, audio, beam: "box this lap")
    kind, text, level, name = speech.mic_test(mic, object(), speech.SpeechSettings(), 4.0)
    assert (kind, text, name) == ("test", "box this lap", "Stereo Mix (Realtek Audio)")
    assert -21 < level < -19                                     # 0.1 RMS = -20 dB
    # Nothing at all arrives: said as nothing heard.
    monkeypatch.setattr(speech.time, "sleep", lambda s: None)
    assert speech.mic_test(mic, object(), speech.SpeechSettings(), 4.0)[1:3] == ("", -100.0)
    mic.spec = "headset"
    mic.reopen()
    assert mic.name == "Headset Microphone" and opened[-1] == 1
    assert speech.level_db(np.zeros(0, np.float32)) == -100.0


def test_the_output_device_changes_without_reloading_the_voice(monkeypatch):
    from engineer.radio import tts
    picked = []
    monkeypatch.setattr(tts, "resolve_output_device", lambda spec: picked.append(spec) or (3, spec))
    b = object.__new__(tts.PiperBackend)
    b.settings = tts.AudioSettings(voice_model=None)
    b.set_device("Headset Earphone")
    assert (b.device, b.device_name, picked) == (3, "Headset Earphone", ["Headset Earphone"])
    ap = tts.AudioProcess(tts.AudioSettings(voice_model=None))
    ap._calls = queue.Queue()
    ap.set_output("Headset Earphone")
    assert ap._calls.get_nowait() == {"output_device": "Headset Earphone"}


class FakePtt:
    def __init__(self):
        self.mic, self.test, self.speech, self.inputs = "Stereo Mix", None, object(), []

    def status(self):
        return {"state": "ready", "held": False, "connected": 1, "error": None}

    def set_input(self, name):
        self.inputs.append(name)
        self.mic = name

    def mic_test(self):
        self.test = {"state": "listening"}
        return True


def test_settings_pick_devices_and_test_the_mic(tmp_path, monkeypatch):
    import engineer.audio_devices as ad
    from engineer.app import build_engine
    from engineer.config import Config
    from engineer.features.priors import WearPriors
    from engineer.ui.app import AppWindow
    from tests.test_ui import _http
    monkeypatch.setattr(ad, "list_devices", lambda: group(DEVICES, 2, 3))
    cfg = Config(data_dir=tmp_path / "data")
    cfg.ui.port = 21894
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n")
    engine = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    ptt = FakePtt()
    window = AppWindow(cfg, engine, config_path=config_path, ptt=ptt)

    async def run():
        await window.start()
        port = window.server.port
        st, body = await _http(port, "GET", "/")
        assert b"Audio devices" in body and b"Test microphone" in body
        st, body = await _http(port, "GET", "/api/audio")
        got = json.loads(body)
        assert st == 200 and got["inputs"][0]["loopback"] and got["mic"] == "Stereo Mix"
        st, body = await _http(port, "POST", "/api/audio",
                               {"input": "Headset Microphone (Wireless Co"})
        assert json.loads(body)["audio"]["input"] == "Headset Microphone (Wireless Co"
        assert ptt.inputs == ["Headset Microphone (Wireless Co"]
        st, body = await _http(port, "POST", "/api/audio", {"output": "Headset Earphone"})
        assert st == 200 and cfg.radio.output_device == "Headset Earphone"
        st, body = await _http(port, "POST", "/api/audio", {"action": "test"})
        assert json.loads(body)["audio"]["test"] == {"state": "listening"}
        st, _ = await _http(port, "POST", "/api/audio", {"input": 5})
        assert st == 400
        st, body = await _http(port, "POST", "/api/audio", {"input": None})   # Windows default
        assert json.loads(body)["audio"]["input"] is None
        window.close()
    asyncio.run(run())
    local = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert local["voice"]["input_device"] is None
    assert local["radio"]["output_device"] == "Headset Earphone"


def test_what_the_engineer_says_after_a_mic_check():
    from engineer.cli import mic_check_text
    assert mic_check_text({"heard": "Radio check.", "level_db": -25}) == \
        "Mic check. I heard: Radio check."
    assert mic_check_text({"heard": "", "level_db": -100}) == "Mic check. I didn't hear anything."
