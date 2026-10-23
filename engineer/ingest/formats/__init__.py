"""Versioned packet formats.

The game can send UDP format 2023, 2024 or 2025 (set in its telemetry settings). Every packet starts
with a 2-byte little-endian `m_packetFormat`, so we read that first and route to the module for that
year. Each module exposes the same small interface: `parse(raw, header)`, `parse_header(raw)`,
`decode_event(record)` and its `PacketId` constants.
"""

from __future__ import annotations

from types import ModuleType

from . import f1_2025, f1_2026


class UnsupportedFormat(ValueError):
    """The packet's m_packetFormat has no parser module."""


FORMATS: dict[int, ModuleType] = {
    f1_2025.PACKET_FORMAT: f1_2025,
    f1_2026.PACKET_FORMAT: f1_2026,
}


def packet_format(raw: bytes) -> int:
    if len(raw) < 2:
        raise UnsupportedFormat(f"packet too short ({len(raw)} bytes) to hold a format field")
    return int.from_bytes(raw[:2], "little")


def module_for(raw: bytes) -> ModuleType:
    fmt = packet_format(raw)
    module = FORMATS.get(fmt)
    if module is None:
        raise UnsupportedFormat(
            f"UDP format {fmt} is not supported. In F1 25 set Settings > Telemetry Settings > "
            f"UDP Format to 2025."
        )
    return module


def dispatch(raw: bytes):
    """Parse a raw packet with the right format module.

    Returns (module, header, packet_id, record); record is None for unknown packet ids.
    """
    module = module_for(raw)
    header, packet_id, record = module.parse(raw)
    return module, header, packet_id, record
