"""The app window (server, settings, live state), the engineer tempo and the PlayStation button."""

import asyncio
import io
import json

import yaml

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio.director import Priority, RadioCall
from engineer.ui.app import AppWindow, report_insights
from engineer.voice.controller import PlayStationButton, XInputButton, button_label, make_button


def _engine(cfg=None):
    return build_engine(cfg or Config(), out=io.StringIO(), priors=WearPriors())


def _ask(engine, text):
    engine.pipeline.bus.publish(ev.QuestionAsked(0, 0.0, text=text))


def test_tempo_changes_by_voice():
    engine = _engine()
    assert engine.tempo == "normal"
    _ask(engine, "talk more")
    assert engine.tempo == "chatty" and engine.corners.verbosity == "chatty"
    _ask(engine, "keep it short")
    assert engine.tempo == "terse" and engine.director.tempo == "terse"
    _ask(engine, "be quiet")
    assert engine.director.quiet
    _ask(engine, "back to normal")                 # back to the default, radio silence over
    assert engine.tempo == "normal" and not engine.director.quiet


def test_quiet_tempo_leaves_out_low_priority_extras():
    engine = _engine()
    dropped = []
    engine.pipeline.bus.subscribe(ev.RadioDropped, lambda e: dropped.append((e.topic, e.reason)))
    engine.set_tempo("terse")
    engine.director.submit(RadioCall(topic="tyre_status", text="Tyres fine.",
                                     priority=Priority.LOW))
    engine.director.submit(RadioCall(topic="strategy_plan", text="Plan is one stop.",
                                     priority=Priority.LOW))
    assert dropped == [("tyre_status", "tempo")]


def test_playstation_button():
    assert button_label("playstation") == "L1" and button_label("xbox") == "LB"
    assert button_label("playstation", "LB") == "L1"      # an Xbox name doesn't apply
    assert PlayStationButton("L1").mask == 1 << 4           # L1 is the 5th DirectInput button
    assert isinstance(make_button("playstation"), PlayStationButton)
    assert isinstance(make_button("xbox"), XInputButton)


async def _http(port, method, path, body=None, header=True):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    head = f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n"
    if header:
        head += "X-Engineer: 1\r\n"
    writer.write(head.encode() + b"\r\n" + data)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    status = int(raw.split(b" ", 2)[1])
    return status, raw.split(b"\r\n\r\n", 1)[1]


def test_app_window_state_and_settings(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    cfg.ui.port = 21890
    engine = _engine(cfg)
    config_path = tmp_path / "config.yaml"
    window = AppWindow(cfg, engine, config_path=config_path)
    done = []
    window.on_setup_done = lambda: done.append(True)

    async def run():
        await window.start()
        port = window.server.port
        st, body = await _http(port, "GET", "/")
        assert st == 200 and b"Which controller do you race with?" in body
        st, body = await _http(port, "GET", "/api/state")
        state = json.loads(body)
        assert st == 200 and state["setup_done"] is False and state["tempo"] == "normal"
        assert state["controller"]["button"] == "LB"
        st, _ = await _http(port, "POST", "/api/settings", {"tempo": "chatty"}, header=False)
        assert st == 403                                 # only our own page may change settings
        st, body = await _http(port, "POST", "/api/settings",
                               {"tempo": "chatty", "controller": "playstation",
                                "setup_done": True})
        state = json.loads(body)
        assert st == 200 and state["tempo"] == "chatty" and state["setup_done"]
        assert state["controller"] == {**state["controller"], "type": "playstation",
                                       "button": "L1"}
        st, _ = await _http(port, "POST", "/api/settings", {"tempo": "loud"})
        assert st == 400
        st, _ = await _http(port, "GET", "/nope")
        assert st == 404
        window.close()
    asyncio.run(run())

    assert engine.tempo == "chatty" and engine.default_tempo == "chatty"
    assert done == [True]
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert saved == {"radio": {"verbosity": "chatty"},
                     "voice": {"enabled": True, "controller_type": "playstation"},
                     "ui": {"setup_done": True}}


def test_radio_calls_reach_the_window(tmp_path):
    engine = _engine(Config(data_dir=tmp_path))
    window = AppWindow(Config(data_dir=tmp_path), engine)
    engine.pipeline.bus.publish(ev.RadioMessage(0, 0.0, topic="box", text="Box this lap.",
                                                  priority=3, queued_at=0.0, origin_t_ns=None))
    assert window.snapshot()["radio"][0]["text"] == "Box this lap."


def test_report_insights_with_no_sessions(tmp_path):
    data = report_insights(Config(data_dir=tmp_path))
    assert data["sessions"] == 0 and data["themes"] == []
