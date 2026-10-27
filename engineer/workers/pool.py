"""Starting worker processes for heavy work (speech now; strategy sims and speech recognition later).

We always use the "spawn" start method, which is what Windows uses anyway, so behaviour is the
same on every platform: the worker starts a fresh Python, imports the target function by name, and
talks to the main process only through queues.
"""

from __future__ import annotations

import multiprocessing as mp
from typing import Any, Callable

_ctx = mp.get_context("spawn")


def start_worker(target: Callable[..., Any], *args) -> tuple[mp.Process, mp.Queue, mp.Queue]:
    """Start `target(inbox, outbox, *args)` in a daemon process. Returns (process, inbox, outbox).

    `target` must be a module-level function (spawn imports it by name).
    """
    inbox, outbox = _ctx.Queue(), _ctx.Queue()
    proc = _ctx.Process(target=target, args=(inbox, outbox, *args), daemon=True)
    proc.start()
    return proc, inbox, outbox
