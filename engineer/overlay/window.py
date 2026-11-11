"""The lap delta overlay window (runs in its own process: `overlay_main`).

A small dark panel, always on top, slightly see-through, and on Windows click-through (mouse
clicks go to the game underneath) and never taking focus from the game. It needs the game in
borderless windowed mode: true fullscreen hides every overlay.

Locked (normal): click-through, hidden when there's nothing to show (no reference lap yet, pit
lane, after the flag). Move mode (unlocked): a yellow outline and a sample reading; drag to
move, scroll to resize, double-click to lock. Locking sends the position back to the main
process, which saves it in config.local.yaml.

Inbox messages: {"reading": dict | None} (view.message), {"locked": bool, "save": bool}
(save = report the spot back, like a double-click),
{"settings": {"scale", "opacity", "bar_s"}}, or None to stop.
Outbox: ("ready",), ("placed", {"x", "y", "scale"}), ("closed",).
"""

from __future__ import annotations

import queue as queue_mod
import sys
import time

from engineer.overlay import view as v

POLL_MS = 33                  # read the inbox ~30 times a second (the bar moves this often)
TEXT_EVERY_S = 0.1            # the number changes at most 10 times a second, so it's readable
TOPMOST_EVERY_S = 2.0         # put it back on top (a game going borderless can cover it)
# The look matches the app window's F1 theme: near-black panel, F1 red accents (the red edge and
# the diagonal stripes of the app's logo), a lighter header strip, timing-screen style chips.
BASE_W, BASE_H = 260, 112
BG = "#0e0f14"                # the app window's background
HEADER = "#171821"            # its card colour
TRACK = "#262838"
RED = "#e10600"               # F1 red
INK, DIM = "#f2f2f5", "#7d8095"
MOVE = "#fab219"              # the app's warning yellow
# Bahnschrift: the DIN-style face that comes with Windows 10 and 11 (close to motorsport timing
# graphics); then Segoe UI; DejaVu where neither exists (Linux tests).
NUMBER_FONTS = ("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", "DejaVu Sans")
LABEL_FONTS = ("Bahnschrift SemiBold SemiConden", "Bahnschrift Condensed", "Bahnschrift",
               "Segoe UI Semibold", "DejaVu Sans Condensed", "DejaVu Sans")


def _pick(root, names: tuple[str, ...]) -> str:
    from tkinter import font as tkfont
    have = set(tkfont.families(root))
    return next((n for n in names if n in have), "TkDefaultFont")


class _Win32:
    """The Windows-only parts: click-through, no focus, no taskbar button, sharp text."""

    GWL_EXSTYLE = -20
    WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
    SWP = 0x0001 | 0x0002 | 0x0010            # no move, no size, no activate

    def __init__(self):
        import ctypes
        self.user32 = ctypes.windll.user32
        self.hwnd = None

    @staticmethod
    def dpi_aware() -> None:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)     # per monitor
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def attach(self, root) -> None:
        self.hwnd = self.user32.GetParent(root.winfo_id())

    def click_through(self, on: bool) -> None:
        if not self.hwnd:
            return
        style = self.user32.GetWindowLongW(self.hwnd, self.GWL_EXSTYLE)
        style |= self.WS_EX_LAYERED | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE
        style = style | self.WS_EX_TRANSPARENT if on else style & ~self.WS_EX_TRANSPARENT
        self.user32.SetWindowLongW(self.hwnd, self.GWL_EXSTYLE, style)

    def on_top(self) -> None:
        if self.hwnd:
            self.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, self.SWP)   # -1 = topmost

    def screen(self) -> tuple[int, int, int, int]:
        g = self.user32.GetSystemMetrics
        return g(76), g(77), g(78), g(79)       # the virtual screen: all monitors


class OverlayWindow:
    def __init__(self, inbox, outbox, settings: dict):
        import tkinter as tk
        self.inbox, self.outbox = inbox, outbox
        self.scale = float(settings.get("scale") or 1.0)
        self.opacity = float(settings.get("opacity") or 0.85)
        self.bar_s = float(settings.get("bar_s") or 1.0)
        self.locked = bool(settings.get("locked", True))
        self.win32 = _Win32() if sys.platform == "win32" else None
        if self.win32:
            _Win32.dpi_aware()
        self.root = tk.Tk()
        self.root.title("Race Engineer delta")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.num_font = _pick(self.root, NUMBER_FONTS)
        self.label_font = _pick(self.root, LABEL_FONTS)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.msg: dict | None = None
        self.shown: v.View | None = None
        self.text_at = 0.0
        self.top_at = 0.0
        self.running = True
        w, h = self._size()
        x, y = v.place(settings.get("x"), settings.get("y"), w, h, self._screen(),
                       self.root.winfo_screenwidth())
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.update_idletasks()
        if self.win32:
            self.win32.attach(self.root)
        self._drag = (0, 0)
        c = self.canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<Double-Button-1>", lambda e: self.set_locked(True, placed=True))
        c.bind("<MouseWheel>", lambda e: self._resize(1 if e.delta > 0 else -1))
        c.bind("<Button-4>", lambda e: self._resize(1))         # Linux scroll
        c.bind("<Button-5>", lambda e: self._resize(-1))
        self.set_locked(self.locked)
        self.outbox.put(("ready",))

    # -- geometry ------------------------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        return int(BASE_W * self.scale), int(BASE_H * self.scale)

    def _screen(self) -> tuple[int, int, int, int]:
        if self.win32:
            return self.win32.screen()
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _press(self, e) -> None:
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _motion(self, e) -> None:
        if not self.locked:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _resize(self, step: int) -> None:
        if self.locked:
            return
        self.scale = round(min(3.0, max(0.6, self.scale + 0.1 * step)), 2)
        w, h = self._size()
        self.root.geometry(f"{w}x{h}")
        self.shown = None
        self._draw(force=True)

    def set_locked(self, locked: bool, placed: bool = False) -> None:
        self.locked = locked
        if self.win32:
            self.win32.click_through(locked)
        self.shown = None
        self._draw(force=True)
        if placed:
            self.outbox.put(("placed", {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                                        "scale": self.scale}))

    # -- drawing -------------------------------------------------------------------------------

    def _draw(self, force: bool = False) -> None:
        msg = self.msg if self.locked or self.msg is not None else v.SAMPLE
        vw = v.view(msg, self.bar_s)
        if vw is None:
            self.root.attributes("-alpha", 0.0)      # hidden, but never loses its place
            self.shown = None
            return
        self.root.attributes("-alpha", self.opacity if self.locked else 0.92)
        now = time.monotonic()
        text_due = force or self.shown is None or now - self.text_at >= TEXT_EVERY_S
        shown = vw if text_due else v.View(self.shown.text, self.shown.colour, vw.bar, vw.ref,
                                           vw.corner, vw.corner_colour)
        if text_due:
            self.text_at = now
        if shown == self.shown and not force:
            return
        self.shown = shown
        self._paint(shown)

    def _paint(self, vw: v.View) -> None:
        c, k = self.canvas, self.scale
        w, h = self._size()
        c.delete("all")
        def px(n: float) -> int:                       # a size at this scale
            return max(1, round(n * k))

        def label(n: float) -> tuple:
            return (self.label_font, max(6, px(n)), "bold")

        edge, pad, head = px(5), px(12), px(26)
        # Header strip with the red edge and the logo's diagonal stripes.
        c.create_rectangle(0, 0, w, head, fill=HEADER, width=0)
        c.create_rectangle(0, 0, edge, h, fill=RED, width=0)
        x = edge + px(8)
        for _ in range(3):
            c.create_polygon(x + px(4), px(7), x + px(8), px(7), x + px(4), head - px(7),
                             x, head - px(7), fill=RED, width=0)
            x += px(6)
        t = c.create_text(x + px(6), head // 2, text="DELTA", anchor="w", fill=INK,
                          font=label(11))
        x2 = c.bbox(t)[2] if c.bbox(t) else x + px(50)
        ref = c.create_text(x2 + px(7), head // 2, text=vw.ref.upper(), anchor="w", fill=DIM,
                            font=label(9))
        ref_end = (c.bbox(ref) or (0, 0, x2, 0))[2]
        # The corner split as a timing-screen chip: white turn box, then the gain in colour.
        if vw.corner:
            turn, gain = vw.corner.split(" ", 1)
            g = c.create_text(w - pad, head // 2, text=gain, anchor="e", fill=vw.corner_colour,
                              font=label(11))
            gx = c.bbox(g)[0] if c.bbox(g) else w - pad - px(40)
            chip = c.create_text(gx - px(10), head // 2, text=turn, anchor="e", fill=BG,
                                 font=label(10))
            b = c.bbox(chip)
            if b:
                c.create_rectangle(b[0] - px(5), px(5), b[2] + px(5), head - px(5), fill=INK,
                                   width=0)
                c.tag_raise(chip)
                if b[0] - px(5) < ref_end + px(6):     # no room for both: the split wins
                    c.delete(ref)
        # The delta, big, in the middle of the body.
        bar_top = h - px(17)
        c.create_text(edge + (w - edge) // 2, (head + bar_top) // 2 + px(1), text=vw.text,
                      fill=vw.colour, font=(self.num_font, max(12, px(34)), "bold"))
        if not self.locked:
            c.create_rectangle(1, 1, w - 2, h - 2, outline=MOVE, dash=(4, 3), width=2)
            size = 8
            while True:                                 # the hint, as big as fits
                hint = c.create_text(edge + (w - edge) // 2, h - px(10), fill=MOVE,
                                     text="DRAG · SCROLL TO RESIZE · DOUBLE-CLICK TO LOCK",
                                     font=label(size))
                b = c.bbox(hint)
                if size <= 5 or not b or b[2] - b[0] <= w - edge - px(10):
                    break
                c.delete(hint)
                size -= 0.5
            return
        # The bar: right (red) when slower, left (green) when quicker, ticks at half scale.
        x0, x1 = edge + pad, w - pad
        mid, half = (x0 + x1) // 2, (x1 - x0) // 2
        y0, y1 = bar_top, bar_top + px(6)
        c.create_rectangle(x0, y0, x1, y1, fill=TRACK, width=0)
        end = mid + int(half * vw.bar)
        if end != mid:
            c.create_rectangle(min(mid, end), y0, max(mid, end), y1, fill=vw.colour, width=0)
        for tick in (x0 + half // 2, x1 - half // 2):
            c.create_line(tick, y1, tick, y1 + px(3), fill=DIM)
        c.create_line(mid, y0 - px(3), mid, y1 + px(3), fill=INK, width=max(1, px(1.5)))

    # -- the loop ------------------------------------------------------------------------------

    def _poll(self) -> None:
        changed = False
        try:
            while True:
                item = self.inbox.get_nowait()
                if item is None:
                    self.running = False
                    break
                if "reading" in item:
                    self.msg, changed = item["reading"], True
                if "locked" in item:
                    self.set_locked(bool(item["locked"]), placed=bool(item.get("save")))
                if "settings" in item:
                    s = item["settings"]
                    self.opacity = float(s.get("opacity", self.opacity))
                    self.bar_s = float(s.get("bar_s", self.bar_s))
                    if s.get("scale"):
                        self.scale = float(s["scale"])
                        w, h = self._size()
                        self.root.geometry(f"{w}x{h}")
                    self.shown, changed = None, True
        except queue_mod.Empty:
            pass
        if not self.running:
            self.root.destroy()
            return
        if changed or self.shown is not None:
            self._draw()
        now = time.monotonic()
        if self.win32 and now - self.top_at >= TOPMOST_EVERY_S:
            self.top_at = now
            self.win32.on_top()
        self.root.after(POLL_MS, self._poll)

    def run(self) -> None:
        self.root.after(POLL_MS, self._poll)
        self.root.mainloop()


def overlay_main(inbox, outbox, settings: dict) -> None:
    """Entry point of the overlay process (workers.pool.start_worker)."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # the main process stops us with None
    try:
        OverlayWindow(inbox, outbox, settings).run()
    finally:
        outbox.put(("closed",))
