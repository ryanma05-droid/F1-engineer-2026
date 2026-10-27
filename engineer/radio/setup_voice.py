"""Download the Piper voice and test it.

    python -m engineer.radio.setup_voice            # download the voice from config.yaml
    python -m engineer.radio.setup_voice --test     # then say "Radio check" through the radio

Voices come from the official Piper voice collection on Hugging Face (rhasspy/piper-voices).
Each voice is two files: the model (.onnx, ~60 MB) and its settings (.onnx.json).
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

from engineer.config import load_config
from engineer.radio.tts import AudioProcess, AudioSettings, voice_model_path

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_url(voice: str) -> str:
    """'en_GB-northern_english_male-medium' -> .../en/en_GB/northern_english_male/medium/<voice>"""
    lang_region, name, quality = voice.split("-")
    lang = lang_region.split("_")[0]
    return f"{BASE_URL}/{lang}/{lang_region}/{name}/{quality}/{voice}"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done / 1e6:5.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def download_voice(voice: str, voices_dir: Path, force: bool = False) -> Path:
    voices_dir.mkdir(parents=True, exist_ok=True)
    model = voice_model_path(voices_dir, voice)
    for suffix in (".onnx.json", ".onnx"):
        dest = voices_dir / f"{voice}{suffix}"
        if dest.exists() and dest.stat().st_size > 0 and not force:
            print(f"  {dest.name}: already downloaded")
            continue
        _download(voice_url(voice) + suffix, dest)
    if model.stat().st_size < 1_000_000:
        raise RuntimeError(f"{model} is suspiciously small; delete it and try again")
    return model


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="python -m engineer.radio.setup_voice")
    ap.add_argument("--voice", default=cfg.radio.voice, help="Piper voice name")
    ap.add_argument("--force", action="store_true", help="download again even if present")
    ap.add_argument("--test", action="store_true", help="say a test message afterwards")
    ap.add_argument("--no-effect", action="store_true", help="test without the radio effect")
    args = ap.parse_args(argv)

    print(f"Voice: {args.voice} -> {cfg.voices_path}")
    try:
        model = download_voice(args.voice, cfg.voices_path, args.force)
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print("Voice ready.")
    if args.test:
        audio = AudioProcess(AudioSettings(str(model), radio_effect=not args.no_effect,
                                           volume=cfg.radio.volume, show_latency=False))
        print("Loading voice...", flush=True)
        backend = audio.start(wait_ready_s=60)
        print(f"Speaking with: {backend}")
        audio.say("Radio check. One oh nine one, two tenths quicker, gained in sector one.")
        audio.status.get(timeout=60)
        time.sleep(0.2)
        audio.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
