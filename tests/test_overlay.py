"""The lap delta overlay (engineer/overlay/): what it shows, how it's fed, where it sits, and the
window itself where a display and Tk exist (skipped otherwise)."""

import itertools
import queue

import pytest
import yaml

from engineer.overlay import view as v
from engineer.overlay.process import OverlayFeed, OverlayProcess, save_placed
from engineer.state.live_delta import CornerSplit, DeltaReading


def reading(d, corner=None, lap=5, ref=4):
    return DeltaReading(lap=lap, ref_lap=ref, distance_m=1000.0, delta_s=d, session_time=1.0,
                        corner=corner)


def test_what_it_shows():
    assert [v.delta_text(d) for d in (0.234, -0.4129, 0.0004, -0.0004, 12.34, 250.0)] == \
        ["+0.23", "-0.41", "0.00", "0.00", "+12.3", "+99.9"]
    assert v.colour(0.3) == v.SLOWER and v.colour(-0.3) == v.QUICKER and v.colour(0.01) == v.EVEN
    msg = v.message(reading(0.5, CornerSplit(3, "turn three", 0.5, -0.141, 1.0)))
    assert msg == {"delta": 0.5, "lap": 5, "ref": 4, "corner": {"turn": 3, "gain": -0.141}}
    got = v.view(msg, bar_s=1.0)
    assert (got.text, got.ref, got.corner, got.corner_colour) == ("+0.50", "vs L4", "T3 -0.14",
                                                                  v.QUICKER)
    assert got.bar == 0.5 and v.view({**msg, "delta": -3.0}).bar == -1.0
    assert v.message(None) is None and v.view(None) is None
    assert v.message(reading(float("nan"))) is None


def test_where_it_sits():
    screen = (0, 0, 1920, 1080)
    assert v.place(None, None, 240, 104, screen, 1920) == (840, 80)        # top centre
    assert v.place(100, 900, 240, 104, screen, 1920) == (100, 900)
    # A second monitor on the left that's since been unplugged: back to the default.
    assert v.place(-1500, 200, 240, 104, screen, 1920) == (840, 80)
    assert v.place(-1500, 200, 240, 104, (-1920, 0, 3840, 1080), 1920) == (-1500, 200)


class _Proc:
    def is_alive(self):
        return True


def fake_overlay(clock_step=0.01, on_placed=None):
    ov = OverlayProcess({}, on_placed=on_placed, clock=itertools.count(0, clock_step).__next__)
    ov._proc, ov._inbox, ov._outbox = _Proc(), queue.Queue(), queue.Queue()
    return ov


def sent(ov):
    return [m["reading"] for m in ov._inbox.queue]


def test_feed_is_throttled_but_never_misses_a_change_that_matters():
    ov = fake_overlay(clock_step=0.01)              # 100 sends a second offered
    for i in range(100):
        ov.send(reading(i / 1000))
    assert 13 <= len(sent(ov)) <= 17                # ~15 a second
    ov._inbox.queue.clear()
    ov.send(None)                                   # disappears: at once
    ov.send(None)                                   # ... once
    ov.send(reading(0.1))                           # appears: at once
    ov.send(reading(0.1, CornerSplit(2, "turn two", 0.1, 0.05, 1.0)))   # a corner: at once
    got = sent(ov)
    assert got[0] is None and len(got) == 3 and got[2]["corner"]["turn"] == 2


def test_feed_sends_after_lap_data_only():
    from engineer.ingest.formats.f1_2025 import PacketId

    class LD:
        current = reading(0.2)
    ov = fake_overlay(clock_step=1.0)
    feed = OverlayFeed(LD(), ov)
    feed.after_packet(PacketId.CAR_TELEMETRY)
    assert not sent(ov)
    feed.after_packet(PacketId.LAP_DATA)
    assert sent(ov)[0]["delta"] == 0.2


def test_locking_after_a_move_saves_the_spot(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{}\n")
    (tmp_path / "config.local.yaml").write_text("upload: {auto: true}\n")
    ov = fake_overlay(on_placed=save_placed(cfg))
    ov._outbox.put(("placed", {"x": 120, "y": 40, "scale": 1.3}))
    ov.poll()
    local = yaml.safe_load((tmp_path / "config.local.yaml").read_text())
    assert local == {"upload": {"auto": True}, "overlay": {"x": 120, "y": 40, "scale": 1.3}}
    assert ov.settings["x"] == 120
    ov._outbox.put(("closed",))
    ov.poll()
    assert not ov.running
    ov.send(reading(0.1))                           # closed: nothing queued, no error
    assert not sent(ov)


@pytest.fixture
def tk_ok():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
        root.destroy()
    except tkinter.TclError as exc:
        pytest.skip(f"no display for Tk: {exc}")


def test_the_window(tk_ok):
    """The real window, in this process: shows a reading, hides with nothing to show, a move
    then lock reports the spot, and None closes it."""
    from engineer.overlay.window import OverlayWindow
    inbox, outbox = queue.Queue(), queue.Queue()
    w = OverlayWindow(inbox, outbox, {"locked": False, "x": 30, "y": 40, "scale": 1.0})
    assert outbox.get_nowait() == ("ready",)
    assert w.shown is not None and w.shown.text == "+0.23"      # move mode: the sample
    w.root.geometry("+50+60")
    w.root.update()
    w.set_locked(True, placed=True)
    kind, pos = outbox.get_nowait()
    assert kind == "placed" and pos["scale"] == 1.0
    inbox.put({"reading": v.message(reading(-0.42))})
    inbox.put({"settings": {"scale": 1.5}})
    w._poll()
    assert w.shown.text == "-0.42" and w.scale == 1.5
    inbox.put({"reading": None})
    w._poll()
    assert w.shown is None                                       # hidden
    inbox.put(None)
    w._poll()                                                    # destroys the window
    assert not w.running
