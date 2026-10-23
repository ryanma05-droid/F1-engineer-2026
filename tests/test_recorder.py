from engineer.ingest.recorder import Recorder, list_sessions, read_recording
from tests.synth import PacketFactory


def _packets(pf: PacketFactory, n: int):
    out = []
    for i in range(n):
        out.append(pf.session(track_id=10, session_type=15) if i % 5 == 0
                   else pf.lap_data({0: {"currentLapNum": 1}}))
        pf.tick()
    return out


def test_records_and_reads_back_two_sessions(tmp_path):
    rec = Recorder(tmp_path)
    a = _packets(PacketFactory(session_uid=111), 50)
    b = _packets(PacketFactory(session_uid=222), 30)
    menu = PacketFactory(session_uid=0).session()
    t = 1_700_000_000_000_000_000
    stamped_a = [(t + i * 16_000_000, p) for i, p in enumerate(a)]
    stamped_b = [(t + 10**10 + i * 16_000_000, p) for i, p in enumerate(b)]
    rec(menu, t - 1)
    for ts, p in stamped_a:
        rec(p, ts)
    for ts, p in stamped_b:
        rec(p, ts)
    rec.close()

    sessions = list_sessions(tmp_path)
    assert [s["session_uid"] for s in sessions] == [f"{111:016x}", f"{222:016x}"]
    assert sessions[0]["packets"] == 50 and sessions[1]["packets"] == 30
    assert sessions[0]["track_id"] == 10 and sessions[0]["session_type"] == 15

    assert list(read_recording(sessions[0]["file_path"])) == stamped_a
    assert list(read_recording(sessions[1]["file_path"])) == stamped_b


def test_truncated_file_yields_complete_records(tmp_path):
    rec = Recorder(tmp_path)
    pkts = _packets(PacketFactory(session_uid=5), 200)
    for i, p in enumerate(pkts):
        rec(p, 1_700_000_000_000_000_000 + i * 2_000_000_000)  # 2 s apart -> a flush per packet
    path = rec.current_path
    rec.close()
    data = path.read_bytes()
    path.write_bytes(data[: len(data) * 2 // 3])
    got = list(read_recording(path))
    assert 0 < len(got) < 200
    assert [p for _, p in got] == pkts[: len(got)]
