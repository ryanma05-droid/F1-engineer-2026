"""Recorder: saves every raw packet, one zstd-compressed file per game session.

File format (after zstd decompression) is a plain stream of records:

    uint64 arrival_time_ns   little-endian, from time.time_ns() in the listener
    uint16 length
    bytes  raw packet, exactly as received

We store raw bytes, never parsed objects, so a recording can be re-parsed later by a fixed or newer
parser. A new file starts whenever the header's session UID changes (the game assigns a new UID to
each session, e.g. Qualifying -> Race). Packets with UID 0 (sent from menus) are skipped.

A small SQLite database (index.sqlite, next to the recordings) lists every session with its track
and session type, so you can find recordings without opening them.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

import zstandard

from engineer.ingest import formats
from engineer.ingest.formats import f1_2025

log = logging.getLogger(__name__)

RECORD_HEADER = struct.Struct("<QH")  # arrival time ns, length
FLUSH_INTERVAL_NS = 1_000_000_000      # flush compressed data to disk about once a second
ZSTD_LEVEL = 3                         # fast; recordings are roughly 5-10x smaller than raw

# Session UID position in the header, taken from the parser's header dtype rather than hard-coded.
_UID_OFFSET = f1_2025.HEADER.fields["m_sessionUID"][1]
_UID_END = _UID_OFFSET + 8
_PACKET_ID_OFFSET = f1_2025.HEADER.fields["m_packetId"][1]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_uid    TEXT PRIMARY KEY,   -- hex string: SQLite integers are signed 64-bit
    start_time_ns  INTEGER NOT NULL,
    end_time_ns    INTEGER,
    track_id       INTEGER,
    session_type   INTEGER,
    file_path      TEXT NOT NULL,
    packets        INTEGER DEFAULT 0,
    bytes          INTEGER DEFAULT 0
);
"""


def session_uid_of(raw: bytes) -> int:
    return int.from_bytes(raw[_UID_OFFSET:_UID_END], "little")


@dataclass
class _OpenSession:
    uid: int
    path: Path
    file: BinaryIO
    writer: "zstandard.ZstdCompressionWriter"
    packets: int = 0
    bytes: int = 0
    last_flush_ns: int = 0
    have_session_info: bool = False


class Recorder:
    """Subscriber for the listener: `recorder(raw, t_ns)`. Call close() on shutdown."""

    def __init__(self, sessions_dir: Path | str):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "index.sqlite")
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        self._current: _OpenSession | None = None

    @property
    def current_path(self) -> Path | None:
        return self._current.path if self._current else None

    def __call__(self, raw: bytes, t_ns: int) -> None:
        if len(raw) < _UID_END:
            return
        uid = session_uid_of(raw)
        if uid == 0:
            return
        cur = self._current
        if cur is None or cur.uid != uid:
            self._close_current(t_ns)
            cur = self._open(uid, t_ns)

        cur.writer.write(RECORD_HEADER.pack(t_ns, len(raw)))
        cur.writer.write(raw)
        cur.packets += 1
        cur.bytes += len(raw)

        if not cur.have_session_info and raw[_PACKET_ID_OFFSET] == f1_2025.PacketId.SESSION:
            self._store_session_info(cur, raw)

        if t_ns - cur.last_flush_ns >= FLUSH_INTERVAL_NS:
            # FLUSH_BLOCK pushes buffered data to the file so a crash loses at most ~1 second.
            cur.writer.flush(zstandard.FLUSH_BLOCK)
            cur.file.flush()
            cur.last_flush_ns = t_ns

    def close(self) -> None:
        self._close_current(time.time_ns())
        self.db.close()

    # -- internals ---------------------------------------------------------------------------

    def _open(self, uid: int, t_ns: int) -> _OpenSession:
        row = self.db.execute(
            "SELECT file_path FROM sessions WHERE session_uid=?", (f"{uid:016x}",)
        ).fetchone()
        if row:  # same session seen earlier in this run or a previous one: keep appending to it
            path = Path(row[0])
        else:
            stamp = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = self.dir / f"{stamp}_{uid:016x}.zst"
        f = open(path, "ab")  # append: a session resumed after a restart adds a new zstd frame
        writer = self._compressor.stream_writer(f, closefd=False)
        self.db.execute(
            "INSERT OR IGNORE INTO sessions (session_uid, start_time_ns, file_path) VALUES (?,?,?)",
            (f"{uid:016x}", t_ns, str(path)),
        )
        self.db.commit()
        log.info("Recording session %016x to %s", uid, path)
        self._current = _OpenSession(uid, path, f, writer, last_flush_ns=t_ns)
        return self._current

    def _store_session_info(self, cur: _OpenSession, raw: bytes) -> None:
        try:
            _module, _header, _pid, rec = formats.dispatch(raw)
        except Exception:
            return
        if rec is None:
            return
        cur.have_session_info = True
        self.db.execute(
            "UPDATE sessions SET track_id=?, session_type=? WHERE session_uid=?",
            (int(rec["m_trackId"]), int(rec["m_sessionType"]), f"{cur.uid:016x}"),
        )
        self.db.commit()

    def _close_current(self, t_ns: int) -> None:
        cur = self._current
        if cur is None:
            return
        cur.writer.close()  # ends the zstd frame
        cur.file.close()
        self.db.execute(
            "UPDATE sessions SET end_time_ns=?, packets=packets+?, bytes=bytes+? "
            "WHERE session_uid=?",
            (t_ns, cur.packets, cur.bytes, f"{cur.uid:016x}"),
        )
        self.db.commit()
        log.info("Closed session %016x (%d packets)", cur.uid, cur.packets)
        self._current = None


def read_recording(path: Path | str) -> Iterator[tuple[int, bytes]]:
    """Yield (arrival_time_ns, raw_packet) from a recording.

    A file from a crashed run may end mid-record; we stop cleanly at the last complete packet.
    """
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as f:
        reader = dctx.stream_reader(f, read_across_frames=True)
        buf = b""
        while True:
            try:
                chunk = reader.read(1 << 20)
            except zstandard.ZstdError:
                log.warning("%s: truncated compressed data at end; stopping there", path)
                chunk = b""
            if not chunk:
                break
            buf += chunk
            pos = 0
            n = len(buf)
            while n - pos >= RECORD_HEADER.size:
                t_ns, length = RECORD_HEADER.unpack_from(buf, pos)
                end = pos + RECORD_HEADER.size + length
                if end > n:
                    break
                yield t_ns, buf[pos + RECORD_HEADER.size:end]
                pos = end
            buf = buf[pos:]
        if buf:
            log.warning("%s: %d trailing bytes of an incomplete record ignored", path, len(buf))


def list_sessions(sessions_dir: Path | str) -> list[dict]:
    db_path = Path(sessions_dir) / "index.sqlite"
    if not db_path.exists():
        return []
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM sessions ORDER BY start_time_ns").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
