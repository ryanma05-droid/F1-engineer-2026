"""Background uploads (ingest/uploader.py): while no session is live, everything waiting."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from engineer.config import Config
from engineer.ingest.uploader import QUIET_S, BackgroundUploader


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, auto=True, fail=False):
    cfg = Config(data_dir=tmp_path)
    cfg.upload.auto = auto
    pipeline = SimpleNamespace(packets=0, after_end_packets=0)
    waiting = ["a.zst", "b.zst"]
    sent = []

    def upload(path: Path, progress):
        if fail:
            raise RuntimeError("no network")
        sent.append(path.name)
        waiting.remove(path.name)
    clock = Clock()
    up = BackgroundUploader(cfg, pipeline, upload_fn=upload,
                            pending_fn=lambda: [cfg.sessions_path / n for n in waiting],
                            clock=clock)
    return up, pipeline, clock, sent


def test_waits_while_racing_then_sends_one_at_a_time(tmp_path):
    up, pipe, clock, sent = make(tmp_path)
    pipe.packets = 500                                    # a session is live
    asyncio.run(up.tick())
    assert sent == [] and up.status()["racing"] and up.status()["waiting"] == ["a.zst", "b.zst"]
    clock.t += QUIET_S + 1                                # quiet: menus / lobby
    asyncio.run(up.tick())
    assert sent == ["a.zst"]
    asyncio.run(up.tick())
    assert sent == ["a.zst", "b.zst"] and up.status()["done"] == ["a.zst", "b.zst"]


def test_results_screen_packets_dont_count_as_racing(tmp_path):
    up, pipe, clock, sent = make(tmp_path)
    up.racing()
    clock.t += QUIET_S + 1
    pipe.packets = pipe.after_end_packets = 900           # FLT_MAX clock: not a live session
    asyncio.run(up.tick())
    assert sent == ["a.zst"]


def test_auto_off_only_on_request_and_failures_are_shown(tmp_path):
    up, pipe, clock, sent = make(tmp_path, auto=False)
    clock.t += QUIET_S + 1
    asyncio.run(up.tick())
    assert sent == []
    up.request_now()
    asyncio.run(up.tick())
    asyncio.run(up.tick())
    assert sent == ["a.zst", "b.zst"]
    bad, _, clock2, _ = make(tmp_path, fail=True)
    clock2.t += QUIET_S + 1
    asyncio.run(bad.tick())
    assert "no network" in bad.status()["error"] and bad.uploading is None


def test_close_lets_the_upload_finish(tmp_path):
    up, pipe, clock, sent = make(tmp_path)
    clock.t += QUIET_S + 1

    async def run():
        up.start()
        await asyncio.sleep(0.05)
        await up.close()
    asyncio.run(run())
    assert sent[:1] == ["a.zst"]
