"""Golden test against real recordings.

Put a real F1 25 recording in tests/fixtures/<name>.zst and the lap lines you have checked against
the game's own lap times in tests/fixtures/<name>.expected.txt (one "Lap ..." line per lap). This
test replays each recording and checks the printed lap lines match exactly.

Recordings are large and ignored by git by default; add a chosen fixture with `git add -f`.
"""

import io
from pathlib import Path

import pytest

from engineer.ingest.replay import replay

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(p for p in FIXTURES.glob("*.zst") if p.with_suffix(".expected.txt").exists())


@pytest.mark.skipif(not CASES, reason="no real recording with expected lap lines in tests/fixtures/")
@pytest.mark.parametrize("recording", CASES, ids=lambda p: p.stem)
def test_golden_lap_lines(recording):
    out = io.StringIO()
    pipeline = replay(recording, speed=None, out=out)
    got = [line for line in out.getvalue().splitlines() if line.startswith("Lap")]
    expected = [line for line in recording.with_suffix(".expected.txt").read_text().splitlines()
                if line.startswith("Lap")]
    assert got == expected
    assert pipeline.bad_packets == 0
