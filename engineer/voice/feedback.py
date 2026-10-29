"""Your feedback log: questions you choose to flag on the radio ("log that", "log the following
question: ...") so they get looked at and answered in a later version.

Only what you flag goes in, so you decide which questions are worth building answers for. Each
entry keeps what was asked, what the engineer said back, and the race at that moment (lap,
position, tyres, gaps, the strategy call, the last radio calls), so it's clear afterwards whether
the data to answer it was there.

Files (data/feedback/, not committed):
    logged.jsonl       one line per flagged question; later lines add the race result, or mark
                       an entry dismissed (from the app window). Never rewritten, only appended.
    feedback-log.md    the readable list, rebuilt when the race ends (and when you dismiss one):
                       open questions first, grouped by race, each marked "now goes to <intent>"
                       once a newer version of the engineer handles it differently from when
                       it was logged.

The flagged question is also in data/ptt/questions.jsonl (intent "log_question"), so it travels
with the recording when it's uploaded.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from engineer.voice.intents import match

log = logging.getLogger(__name__)

LOG_FILE = "logged.jsonl"
MARKDOWN_FILE = "feedback-log.md"


class FeedbackLog:
    def __init__(self, folder: Path | str | None, sessions_path: Path | None = None):
        """folder None: nothing is written (replays, tests that don't care)."""
        self.folder = Path(folder) if folder else None
        self.sessions_path = sessions_path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        return self.folder / LOG_FILE if self.folder else None

    @property
    def markdown_path(self) -> Path | None:
        return self.folder / MARKDOWN_FILE if self.folder else None

    # -- writing ----------------------------------------------------------------------------------

    def _append(self, line: dict) -> None:
        if self.path is None:
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line) + "\n")
        except OSError as exc:
            log.warning("could not write the feedback log %s (%s)", self.path, exc)

    def add(self, entry: dict) -> str:
        """Log one flagged question; returns its id."""
        entry = {"id": entry.get("id") or time.strftime("%Y%m%d-%H%M%S"), "status": "open",
                 **entry}
        known = {e["id"] for e in self.entries()}
        n, base = 2, entry["id"]
        while entry["id"] in known:                  # two in the same second
            entry["id"] = f"{base}-{n}"
            n += 1
        self._append(entry)
        return entry["id"]

    def finish(self, session_uid: str, result: dict) -> int:
        """The race ended: add its result to the session's entries. Returns how many."""
        ids = [e["id"] for e in self.entries()
               if e.get("session_uid") == session_uid and "result" not in e]
        for i in ids:
            self._append({"id": i, "result": result})
        return len(ids)

    def dismiss(self, entry_id: str) -> bool:
        if not any(e["id"] == entry_id for e in self.entries()):
            return False
        self._append({"id": entry_id, "status": "dismissed"})
        return True

    # -- reading ----------------------------------------------------------------------------------

    def entries(self) -> list[dict]:
        """Every flagged question with its later updates merged in, oldest first."""
        if self.path is None or not self.path.exists():
            return []
        merged: dict[str, dict] = {}
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        for line in text.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or "id" not in d:
                continue
            if d["id"] in merged:
                merged[d["id"]].update(d)
            elif "question" in d:
                merged[d["id"]] = d
        return list(merged.values())

    def open_entries(self) -> list[dict]:
        return [e for e in self.entries() if e.get("status") != "dismissed"]

    # -- the readable list ------------------------------------------------------------------------

    def write_markdown(self) -> Path | None:
        if self.markdown_path is None:
            return None
        try:
            text = render_markdown(self.entries(), self.sessions_path)
            self.markdown_path.parent.mkdir(parents=True, exist_ok=True)
            # Written whole then swapped in, so a reader (or a second writer) never sees half.
            tmp = self.markdown_path.with_name(
                f".{MARKDOWN_FILE}.{threading.get_ident()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            with self._lock:
                tmp.replace(self.markdown_path)
        except OSError as exc:
            log.warning("could not write %s (%s)", self.markdown_path, exc)
            return None
        return self.markdown_path

    def write_markdown_in_background(self) -> None:
        """Rebuild feedback-log.md off the engineer's loop (it reads the whole log)."""
        if self.markdown_path is not None:
            threading.Thread(target=self.write_markdown, daemon=True).start()


def matches_now(question: str, intent_then: str | None = None) -> str | None:
    """The intent this version gives the question, when that's new: not "unknown" and not the
    one it got when it was logged ("what tyres is Norris on?" went to the tyres answer about
    your own tyres; that isn't an answer). None while it's handled as it was."""
    name = match(question).name
    return None if name in ("unknown", "log_question", intent_then) else name


def _recording(session_uid: str | None, sessions_path: Path | None) -> str | None:
    if not session_uid or sessions_path is None or not sessions_path.exists():
        return None
    found = sorted(sessions_path.glob(f"*{session_uid}*.zst"))
    return found[-1].name if found else None


def _context_lines(c: dict) -> list[str]:
    lines = []
    if c.get("position"):
        lines.append(f"P{c['position']}" + (f" of {c['cars']}" if c.get("cars") else ""))
    if c.get("tyre"):
        bits = [c["tyre"]]
        if c.get("tyre_age") is not None:
            bits.append(f"{c['tyre_age']} laps old")
        if c.get("wear_pct") is not None:
            bits.append(f"worst wear {c['wear_pct']}%")
        lines.append(", ".join(bits))
    gaps = []
    if c.get("gap_ahead_s") is not None:
        gaps.append(f"{c['gap_ahead_s']} s to {c.get('ahead') or 'the car ahead'}")
    if c.get("gap_behind_s") is not None:
        gaps.append(f"{c['gap_behind_s']} s to {c.get('behind') or 'the car behind'} behind")
    if gaps:
        lines.append("Gaps: " + "; ".join(gaps))
    if c.get("plan"):
        lines.append(f"Strategy call: {c['plan']}")
    if c.get("safety_car"):
        lines.append("Safety car out")
    for r in c.get("recent_radio") or []:
        lines.append(f"Radio before: \"{r}\"")
    return lines


def render_markdown(entries: list[dict], sessions_path: Path | None = None) -> str:
    open_ = [e for e in entries if e.get("status") != "dismissed"]
    dismissed = [e for e in entries if e.get("status") == "dismissed"]
    still = [e for e in open_ if matches_now(e["question"], e.get("intent_then")) is None]
    out = ["# Feedback log", "",
           "Questions you flagged on the radio (\"log that\", \"log the following question\"). "
           "Dismiss any you no longer want answered in the app window.", "",
           f"**{len(open_)} open** ({len(still)} handled as when logged, "
           f"{len(open_) - len(still)} handled differently now; check the answer) · "
           f"{len(dismissed)} dismissed", ""]
    if not open_:
        out += ["Nothing logged yet. On the radio: *\"log that\"* right after a question, or "
                "*\"log the following question: ...\"*.", ""]
    races: dict[str, list[dict]] = {}
    for e in open_:
        races.setdefault(e.get("session_uid") or "?", []).append(e)
    for uid, items in sorted(races.items(), key=lambda kv: kv[1][0].get("time", ""),
                             reverse=True):
        first = items[0]
        res = next((e["result"] for e in items if e.get("result")), None) or {}
        title = " · ".join(x for x in (first.get("track"), first.get("session"),
                                       (first.get("time") or "")[:10]) if x)
        out += [f"## {title or 'Unknown session'}", ""]
        meta = [f"session `{uid}`"]
        rec = _recording(uid if uid != "?" else None, sessions_path)
        if rec:
            meta.append(f"recording `{rec}`")
        if res.get("position"):
            meta.append(f"finished P{res['position']}")
        if not res:
            meta.append("race not finished yet")
        out += [" · ".join(meta), ""]
        for e in items:
            now = matches_now(e["question"], e.get("intent_then"))
            lap = e.get("lap")
            total = e.get("total_laps")
            where = f"lap {lap}" + (f"/{total}" if total else "") if lap else "before the start"
            tag = f"now goes to `{now}`, check the answer" if now else "open"
            out.append(f"### “{e['question']}”")
            out.append("")
            out.append(f"- **{where}** · {(e.get('time') or '')[11:19]} · {tag} · id `{e['id']}`")
            if e.get("source") == "last question":
                out.append(f"- Engineer answered: \"{e.get('answer') or '(nothing)'}\""
                           + (f" (matched `{e['intent_then']}`)" if e.get("intent_then") else ""))
            elif e.get("intent_then"):
                out.append(f"- When logged it went to `{e['intent_then']}`")
            elif e.get("note"):
                out.append(f"- Note: {e['note']}")
            for line in _context_lines(e.get("context") or {}):
                out.append(f"- {line}")
            out.append("")
    if dismissed:
        out += ["## Dismissed", ""]
        out += [f"- “{e['question']}” ({e.get('track') or '?'}, lap "
                f"{e.get('lap') or '-'}) · id `{e['id']}`" for e in dismissed]
        out.append("")
    return "\n".join(out)
