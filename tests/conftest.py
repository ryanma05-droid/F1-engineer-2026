"""Test-wide settings.

Radio wording variety (engineer/radio/variety.py) is off for the suite, so expected texts are
the fixed wording; tests/test_variety.py turns it on to test it."""

import os

os.environ["RACE_ENGINEER_VARIETY"] = "0"

import pytest  # noqa: E402

from engineer.radio.variety import VARIETY  # noqa: E402


@pytest.fixture(autouse=True)
def _variety_off():
    VARIETY.enabled, VARIETY.live, VARIETY.path, VARIETY.recent = False, False, None, {}
    yield
    VARIETY.enabled, VARIETY.live, VARIETY.path, VARIETY.recent = False, False, None, {}
