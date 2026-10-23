import pytest

from engineer.ingest import formats
from engineer.ingest.formats import f1_2025 as f
from tests.synth import SESSION_UID, PacketFactory


def test_header_is_29_bytes():
    assert f.HEADER.itemsize == 29


@pytest.mark.parametrize("pid", sorted(f.PACKETS))
def test_dtype_sizes_match_spec(pid):
    name, dtype, size = f.PACKETS[pid]
    assert dtype.itemsize == size, name


@pytest.mark.parametrize("pid", sorted(f.PACKETS))
def test_round_trip_every_packet(pid):
    pf = PacketFactory()
    arr = pf.blank(pid)
    # Fill every byte after the header with a deterministic pattern so a field shifted by even
    # one byte would come back different.
    raw = bytearray(arr.tobytes())
    for i in range(29, len(raw)):
        raw[i] = (i * 7 + 3) % 256
    raw = bytes(raw)
    module, header, packet_id, rec = formats.dispatch(raw)
    assert module is f
    assert packet_id == pid
    assert int(header["m_sessionUID"]) == SESSION_UID
    assert rec is not None
    assert rec.tobytes() == raw


def test_known_field_offsets_lap_data():
    pf = PacketFactory()
    raw = pf.lap_data({3: {"lastLapTimeInMS": 91234, "carPosition": 5, "lapDistance": 1234.5,
                           "gridPosition": 7, "driverStatus": 4}})
    _, _, _, rec = formats.dispatch(raw)
    car = rec["m_lapData"][3]
    assert car["m_lastLapTimeInMS"] == 91234
    assert car["m_carPosition"] == 5
    assert car["m_lapDistance"] == pytest.approx(1234.5)
    assert car["m_gridPosition"] == 7
    assert car["m_driverStatus"] == 4
    # Hand-computed from the spec: 2 u32 (8) + 4 x (u16 + u8) (12) + 3 floats (12) = offset 32
    assert f.LAP_DATA.itemsize == 57
    assert f.LAP_DATA.fields["m_carPosition"][1] == 32
    assert raw[29 + 3 * 57 + 32] == 5


def test_header_little_endian_offsets():
    pf = PacketFactory()
    raw = pf.session()
    assert raw[0:2] == (2025).to_bytes(2, "little")
    assert raw[6] == f.PacketId.SESSION
    assert raw[7:15] == SESSION_UID.to_bytes(8, "little")


def test_unknown_packet_id_passes_through():
    pf = PacketFactory()
    arr = pf.blank(f.PacketId.SESSION)
    raw = bytearray(arr.tobytes())
    raw[6] = 99  # unknown packet id
    module, header, pid, rec = formats.dispatch(bytes(raw))
    assert pid == 99
    assert rec is None


def test_wrong_size_raises():
    pf = PacketFactory()
    raw = pf.session()[:-1]
    with pytest.raises(f.PacketSizeError):
        formats.dispatch(raw)


def test_unsupported_format_is_helpful():
    raw = (2024).to_bytes(2, "little") + bytes(27)
    with pytest.raises(formats.UnsupportedFormat, match="2025"):
        formats.dispatch(raw)


def test_2026_stub_raises_helpful_error():
    raw = (2026).to_bytes(2, "little") + bytes(27)
    with pytest.raises(NotImplementedError, match="2026"):
        formats.dispatch(raw)


def test_decode_event_union():
    pf = PacketFactory()
    _, _, _, rec = formats.dispatch(pf.event("FTLP", vehicleIdx=4, lapTime=88.5))
    code, d = f.decode_event(rec)
    assert code == "FTLP" and d["vehicleIdx"] == 4 and d["lapTime"] == pytest.approx(88.5)

    _, _, _, rec = formats.dispatch(pf.event("FLBK", flashbackFrameIdentifier=1234,
                                             flashbackSessionTime=56.25))
    code, d = f.decode_event(rec)
    assert d == {"flashbackFrameIdentifier": 1234, "flashbackSessionTime": 56.25}

    _, _, _, rec = formats.dispatch(pf.event("SSTA"))
    assert f.decode_event(rec) == ("SSTA", {})


def test_lap_positions_is_50_by_22():
    assert f.PACKET_LAP_POSITIONS.fields["m_positionForVehicleIdx"][0].shape == (50, 22)
