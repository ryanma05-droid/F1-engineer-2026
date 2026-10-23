"""Placeholder for the F1 26 UDP format.

EA publishes a new spec each game year. When F1 26 ships, copy f1_2025.py, update the layouts
from the new spec PDF only, and update the size table. Until then this module refuses packets
with a clear message instead of misreading them with 2025 layouts.
"""

from __future__ import annotations

PACKET_FORMAT = 2026


class PacketId:  # filled in when the 2026 spec is implemented
    pass


def _not_implemented(*_args, **_kwargs):
    raise NotImplementedError(
        "Received a UDP format 2026 packet, but the F1 26 format is not implemented yet. "
        "Either set the game's UDP Format to 2025, or implement engineer/ingest/formats/f1_2026.py "
        "from the F1 26 spec (see the module docstring)."
    )


parse = parse_header = decode_event = _not_implemented
