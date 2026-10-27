import io

from engineer.app import build_engine
from engineer.config import Config
from engineer.ingest.recorder import Recorder
from engineer.ingest.replay import replay
from tests.synth import FakeRace


def test_replay_prints_identical_lines_to_live(tmp_path):
    race = FakeRace({0: [32345, 31876, 33001], 1: [33000, 32000, 31000]},
                    invalid_laps={0: {2}}, extra_packets=False)
    live_out = io.StringIO()
    live = build_engine(Config(), out=live_out).pipeline
    rec = Recorder(tmp_path)
    path = None
    for t_ns, raw in race.frames():
        rec(raw, t_ns)          # same fan-out the listener does
        live.feed(raw, t_ns)
        path = rec.current_path
    rec.close()

    replay_out = io.StringIO()
    replay(path, speed=None, out=replay_out, cfg=Config())
    live_lines = live_out.getvalue().splitlines()
    assert live_lines == replay_out.getvalue().splitlines()
    lap_lines = [line for line in live_lines if line.startswith("Lap")]
    assert len(lap_lines) == 3
    assert lap_lines[0].startswith("Lap  1  0:32.345  S1 9.704  S2 12.938  S3 9.703  Soft (C3)")
    assert "wear RL 5.5% RR 6.0% FL 4.0% FR 4.5%" in lap_lines[0]
    assert "[INVALID]" in lap_lines[1] and "INVALID" not in lap_lines[2]
    radio = [line for line in live_lines if line.startswith("RADIO")]
    assert len(radio) == 4                 # same lines live and replay, radio calls included
    assert "Lap invalid" in radio[1]
    assert "Tyres: rear right worst at six percent" in radio[3]    # stint lap 3 status call
