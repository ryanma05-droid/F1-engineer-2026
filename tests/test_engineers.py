"""Engineer voices (radio/engineers.py, radio/switch.py): who's on the radio, and Kevin."""

import asyncio
import io

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio.director import Priority, RadioCall
from engineer.radio.engineers import BY_KEY, ENGINEERS, Intern, personality
from engineer.radio.switch import VoiceControl, current_key


def test_line_up_and_config():
    assert len(ENGINEERS) == 6 and sum(e.personality is not None for e in ENGINEERS) == 1
    assert len({e.voice for e in ENGINEERS}) == len(ENGINEERS)
    cfg = Config()
    assert cfg.radio.voice_name == "en_GB-northern_english_male-medium"
    assert current_key(cfg) == "northern"              # existing setups: Gary
    cfg.radio.engineer = "intern"
    assert cfg.radio.voice_name == BY_KEY["intern"].voice and cfg.radio.length_scale < 1
    assert personality("scottish") is None and isinstance(personality("intern"), Intern)


def test_kevin_keeps_every_call_word_for_word_and_is_repeatable():
    calls = [("box", "Box this lap, box this lap. Hards."), ("racecraft_pass", "P five."),
             ("racecraft_pass", "Norris got you, P six."), ("answer", "Gap ahead is a second."),
             ("strategy_plan", "Plan is one stop, box end of lap nine for softs."),
             ("lap_delta", "One twenty three four."), ("corner", "Turn two, brake later.")]
    a, b = Intern(), Intern()
    out = [a(t, x) for t, x in calls]
    assert out == [b(t, x) for t, x in calls]          # seeded: replays say the same
    for (_, text), said in zip(calls, out):
        assert text[1:] in said and text[0].lower() in said.lower()   # "Okay so, turn two"
    assert out[0] != calls[0][1]                       # urgent calls always get the panic
    assert sum(s != t for (_, t), s in zip(calls, out)) >= 4


def test_director_says_kevins_version_but_events_keep_the_call():
    cfg = Config()
    cfg.radio.engineer = "intern"
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    got = []
    eng.pipeline.bus.subscribe(ev.RadioMessage, got.append)
    eng.director.submit(RadioCall(topic="box", text="Box this lap, box this lap. Hards.",
                                  priority=Priority.HIGH, wait_for_straight=False))
    e = got[-1]
    assert e.text == "Box this lap, box this lap. Hards."     # what other code matches on
    assert e.spoken and e.text in e.spoken and e.said == e.spoken
    plain = build_engine(Config(), out=io.StringIO(), priors=WearPriors())
    got.clear()
    plain.pipeline.bus.subscribe(ev.RadioMessage, got.append)
    plain.director.submit(RadioCall(topic="box", text="Box box.", priority=Priority.HIGH,
                                    wait_for_straight=False))
    assert got[-1].spoken is None and got[-1].said == "Box box."


class FakeAudio:
    backend = "piper"

    def __init__(self):
        self.said, self.settings = [], None

    def restart(self, settings, wait_ready_s=30.0):
        self.settings = settings
        return "piper"

    def say(self, text, origin_t_ns=None):
        self.said.append(text)


def test_switching_downloads_the_voice_restarts_and_says_a_line(tmp_path):
    cfg = Config(data_dir=tmp_path)
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    audio = FakeAudio()
    vc = VoiceControl(cfg, eng, audio)
    fetched = []

    def download(voice, folder):
        fetched.append(voice)
        (folder).mkdir(parents=True, exist_ok=True)
        (folder / f"{voice}.onnx").write_bytes(b"x")
    asyncio.run(vc.switch("scottish", download))
    assert fetched == ["en_GB-alan-medium"] and vc.status()["state"] == "ready"
    assert audio.settings.voice_model.endswith("en_GB-alan-medium.onnx")
    assert audio.said == [BY_KEY["scottish"].sample] and eng.director.persona is None
    asyncio.run(vc.switch("scottish", download))       # already downloaded
    assert fetched == ["en_GB-alan-medium"]
    asyncio.run(vc.switch("intern", download))
    assert isinstance(eng.director.persona, Intern) and audio.settings.length_scale < 1
    assert vc.status()["engineer"] == "intern"

    def offline(voice, folder):
        raise OSError("no internet")
    asyncio.run(vc.switch("american", offline))
    assert vc.status()["state"] == "failed" and "Ryan" in vc.status()["error"]


def test_settings_api_picks_the_engineer(tmp_path):
    from engineer.ui.app import AppWindow
    cfg = Config(data_dir=tmp_path)
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    win = AppWindow(cfg, eng, config_path=tmp_path / "config.yaml")

    async def go():
        r = await win._api_settings({"engineer": "intern"})
        await win._voice_task
        return r
    assert asyncio.run(go()).status == 200
    assert cfg.radio.engineer == "intern" and win.snapshot()["voice"]["engineer"] == "intern"
    assert "engineer: intern" in (tmp_path / "config.local.yaml").read_text(encoding="utf-8")
    assert asyncio.run(win._api_settings({"engineer": "nobody"})).status == 400


def test_volume_slider_changes_the_voice_live_and_is_saved(tmp_path):
    import yaml

    from engineer.radio.tts import volume_gain
    from engineer.ui.app import AppWindow
    cfg = Config(data_dir=tmp_path)
    assert cfg.radio.volume_pct == 70 and cfg.radio.gain == 1.0
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    audio = FakeAudio()
    audio.gains = []
    audio.set_volume = audio.gains.append
    win = AppWindow(cfg, eng, config_path=tmp_path / "config.yaml",
                    voice=VoiceControl(cfg, eng, audio))
    assert win.snapshot()["voice"]["volume"] == 70
    r = asyncio.run(win._api_settings({"volume": 100}))
    assert r.status == 200 and audio.gains == [volume_gain(100)]
    assert audio.said == ["Radio check. How's that?"]           # hear the new level
    assert win.snapshot()["voice"]["volume"] == 100
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert saved["radio"]["volume_pct"] == 100
    assert asyncio.run(win._api_settings({"volume": 101})).status == 400
    assert asyncio.run(win._api_settings({"volume": "loud"})).status == 400
