"""The PC's microphones and speakers, for the app window's pickers (Settings > Audio devices).

14 Nov: a PlayStation player holding L1 got his headset's sound (the game and the engineer)
instead of his voice: Windows' default recording device wasn't his microphone. Picking the
microphone and the engineer's output by name fixes that without touching Windows settings.

Windows lists every device several times, once per audio system (MME, DirectSound, WASAPI,
WDM-KS). Only the system the default devices belong to is shown (MME on Windows: it converts
sample rates for us, which the 16 kHz microphone stream needs), each name once. A device is
saved by name, matched by name when opened (voice.speech.resolve_input_device /
radio.tts.resolve_output_device), so it survives replugging and reordering.
"""

from __future__ import annotations

# Recording devices that capture what the PC plays, not a microphone.
LOOPBACK_WORDS = ("stereo mix", "what u hear", "wave out", "loopback", "monitor of",
                  "what you hear", "rec. playback")


def is_loopback(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in LOOPBACK_WORDS)


def group(devices: list[dict], default_in: int, default_out: int) -> dict:
    """{"inputs": [...], "outputs": [...]}, each {"name", "default", "loopback"}: the devices
    of the default devices' audio system, each name once, the default first."""
    def api_of(i):
        return devices[i].get("hostapi") if 0 <= i < len(devices) else None
    api = api_of(default_in)
    if api is None:
        api = api_of(default_out)
    out = {"inputs": [], "outputs": []}
    for kind, chans, default in (("inputs", "max_input_channels", default_in),
                                 ("outputs", "max_output_channels", default_out)):
        seen: set[str] = set()
        rows = []
        for i, d in enumerate(devices):
            name = str(d.get("name", "")).strip()
            if d.get(chans, 0) <= 0 or not name or name in seen:
                continue
            if api is not None and d.get("hostapi") != api:
                continue
            seen.add(name)
            rows.append({"name": name, "default": i == default,
                         "loopback": kind == "inputs" and is_loopback(name)})
        rows.sort(key=lambda r: not r["default"])
        out[kind] = rows
    return out


def list_devices() -> dict:
    """The PC's devices right now (blocking, ~0.1 s: call from a thread). PortAudio is
    restarted first so a headset plugged in since start-up shows up."""
    import sounddevice as sd
    try:
        sd._terminate()
        sd._initialize()
    except Exception:              # an older sounddevice: the list from start-up
        pass
    default_in, default_out = (int(x) if x is not None else -1 for x in sd.default.device)
    return group([dict(d) for d in sd.query_devices()], default_in, default_out)
