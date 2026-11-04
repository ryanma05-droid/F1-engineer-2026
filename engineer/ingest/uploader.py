"""Uploads recordings in the background while you're not racing, so closing the app can't lose
them.

3 Nov: three 20-lap races (one 525 MB file) never reached GitHub. Uploads only ran when the app
stopped, one after another, and only for that run's recordings; the window was closed during
the third, and the next run would never have sent them.

Now, with `upload: {auto: true}`:
- every recording not uploaded yet (any run), or grown since it was uploaded (an online lobby
  keeps one session UID, so later sessions are added to the same file), goes up;
- only while no session is live (no packet with a real clock for `QUIET_S`: menus, lobby,
  results screen), so a big upload never competes with an online race for bandwidth;
- one at a time, in a thread (git runs as a subprocess), never on the asyncio loop.

The app window shows what's waiting and uploading, and its "Upload now" button sends everything
waiting straight away (also when auto-upload is off). Stopping the app still uploads whatever is
left, as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from engineer.ingest import share

log = logging.getLogger(__name__)

CHECK_EVERY_S = 15.0
QUIET_S = 20.0           # no live packet for this long: not racing


class BackgroundUploader:
    def __init__(self, cfg, pipeline, recorder=None, upload_fn=None, pending_fn=None,
                 clock=time.monotonic):
        """upload_fn(path, progress) and pending_fn(): replaceable in tests."""
        self.cfg, self.pipeline, self.recorder = cfg, pipeline, recorder
        self._upload = upload_fn or self._git_upload
        self._pending = pending_fn or (lambda: share.pending(cfg))
        self.clock = clock
        self.uploading: str | None = None
        self.done: list[str] = []            # uploaded this run
        self.error: str | None = None
        self.waiting: list[str] = []
        self._now_requested = False
        self._last_live = -1e9
        self._seen_packets = pipeline.packets - pipeline.after_end_packets   # none live yet
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- state -----------------------------------------------------------------------------------

    def racing(self) -> bool:
        """A session is live: packets with a real clock came in the last QUIET_S seconds."""
        n = self.pipeline.packets - self.pipeline.after_end_packets
        if n != self._seen_packets:
            self._seen_packets, self._last_live = n, self.clock()
        return self.clock() - self._last_live < QUIET_S

    def status(self) -> dict:
        return {"auto": bool(self.cfg.upload.auto), "waiting": self.waiting,
                "uploading": self.uploading, "done": self.done[-5:], "error": self.error,
                "racing": self.racing()}

    def request_now(self) -> None:
        """The window's "Upload now": everything waiting, even mid-session or with auto off."""
        self._now_requested = True

    # -- running ---------------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    async def close(self) -> None:
        """Stop, letting an upload that's under way finish first (it's a git push in a thread:
        cutting it off is what lost the 3 Nov races)."""
        self._stopping = True
        if self._task is not None:
            if self.uploading:
                print(f"Finishing the upload of {self.uploading} first...", flush=True)
            await self._task

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick()
            except Exception as exc:                 # never let the uploader take the app down
                log.warning("background upload: %s", exc)
            wait = 1.0 if self._now_requested else CHECK_EVERY_S
            end = self.clock() + wait
            while not self._stopping and self.clock() < end:
                await asyncio.sleep(0.25)

    async def tick(self) -> None:
        self.waiting = [p.name for p in await asyncio.to_thread(self._pending)]
        wanted = self._now_requested or (self.cfg.upload.auto and not self.racing())
        if not self.waiting or not wanted or self.uploading:
            if not self.waiting:
                self._now_requested = False
            return
        path = self.cfg.sessions_path / self.waiting[0]
        if self.recorder is not None and self.recorder.current_path == path:
            self.recorder.flush()                    # the open file: everything so far
        self.uploading = path.name
        try:
            await asyncio.to_thread(self._upload, path, lambda m: log.info("%s", m))
            self.done.append(path.name)
            self.error = None
        except Exception as exc:
            self.error = f"{path.name}: {exc}"
            log.warning("upload of %s failed: %s", path.name, exc)
            self._now_requested = False              # don't hammer a failing upload
        finally:
            self.uploading = None
        self.waiting = [n for n in self.waiting if n != path.name]

    def _git_upload(self, path: Path, progress) -> None:
        repo = Path(self.cfg.upload.repo) if self.cfg.upload.repo else share.PROJECT_ROOT
        if not (repo / ".git").exists():
            raise share.UploadError(f"uploads need a git clone of the project; {repo} isn't one")
        share.upload(path, self.cfg.data_path / "share", self.cfg.upload.remote, repo=repo,
                     progress=progress, questions_log=self.cfg.ptt_path / "questions.jsonl")
