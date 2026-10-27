"""Set up and test push-to-talk.

    uv run python -m engineer.voice.setup            # download the model, then hold LB and talk
    uv run python -m engineer.voice.setup --mics     # list microphones (default marked *)
    uv run python -m engineer.voice.setup --wav <file.wav>   # what the model hears in a recording

The test loop prints what was heard and which question it understood, without the game running.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave

import numpy as np

from engineer.config import load_config
from engineer.voice.intents import match


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.voice.setup")
    ap.add_argument("--mics", action="store_true", help="list microphones")
    ap.add_argument("--wav", help="transcribe a 16 kHz mono WAV file and show the intent")
    args = ap.parse_args(argv)
    cfg = load_config()
    v = cfg.voice

    from engineer.voice.speech import (SpeechProcess, SpeechSettings, list_input_devices,
                                       load_model, transcribe)
    if args.mics:
        for i, name, default in list_input_devices():
            print(f"{'*' if default else ' '} {i:>3}  {name}")
        print("* = Windows default recording device (used unless voice.input_device is set)")
        return 0
    settings = SpeechSettings(model=v.model, device=v.device, compute_type=v.compute_type,
                              input_device=v.input_device,
                              models_dir=str(cfg.ptt_path / "models"),
                              clips_dir=str(cfg.ptt_path / "clips") if v.keep_audio else None,
                              beam_size=v.beam_size, cpu_threads=v.cpu_threads)
    if args.wav:
        with wave.open(args.wav, "rb") as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
        model, desc = load_model(settings)
        t0 = time.perf_counter()
        text = transcribe(model, audio / 32768.0, v.beam_size)
        print(f"{desc}: heard {text!r} in {(time.perf_counter() - t0) * 1000:.0f} ms "
              f"-> {match(text).name}")
        return 0

    from engineer.voice.controller import PushToTalk, XInputButton
    button = XInputButton(v.button, v.controller)
    if not button.available:
        print("XInput not available (push-to-talk needs Windows and an Xbox controller).")
        return 1
    print(f"Loading {v.model} (the first time downloads it: ~150 MB for base.en)...",
          flush=True)
    speech = SpeechProcess(settings)
    if not speech.start():
        print(f"Could not start: {speech.error}")
        return 1
    print(f"Ready: {speech.description}")
    print(f"Controllers connected: {button.connected() or 'none'}")
    print(f"Hold {v.button}, ask a question, let go. Ctrl+C to stop.")
    print("Tip: test with F1 25 running (in a session): that's when speed matters.")
    ptt = PushToTalk(button, lambda: (speech.press(), print("  listening...", flush=True)),
                     lambda: (speech.release(), print("  working it out...", flush=True)),
                     speech.cancel, v.min_press_s, v.max_question_s)
    try:
        while True:
            ptt.poll(time.monotonic())
            for msg in speech.poll():
                if msg[0] == "heard":
                    _, _qid, text, wav, ms = msg
                    print(f"  heard: {text!r} ({ms:.0f} ms) -> {match(text).name}"
                          + (f"   [{wav}]" if wav else ""), flush=True)
                elif msg[0] == "error":
                    print(f"  error: {msg[1]}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        speech.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
