"""The installed app's entry point (RaceEngineer.exe).

First launch: a short setup in the window (where you play, voice and speech downloads, the
telemetry settings to use). After that it starts the engineer straight away, like
`python -m engineer run`. `RaceEngineer.exe --setup` runs the setup again.

Settings and data live in %LOCALAPPDATA%\\RaceEngineer (config.local.yaml, recordings, voices).
"""

from __future__ import annotations

import multiprocessing
import shutil
import socket
import sys
from pathlib import Path

import yaml

from engineer.config import APP_HOME, BUNDLE_DIR, load_config

LOCAL_CONFIG = APP_HOME / "config.local.yaml"
LOG_FILE = APP_HOME / "logs" / "race-engineer.log"


class _Tee:
    """Writes everything shown in the window to the log file too, so a friend can send it."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        try:
            self.log.write(text)
            self.log.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def start_log() -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Keep the previous run's log too (the one that went wrong, if this is a retry).
        if LOG_FILE.exists():
            LOG_FILE.replace(LOG_FILE.with_suffix(".previous.log"))
        log = open(LOG_FILE, "w", encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
FRIEND_SPEECH_MODEL = "small.en"


def lan_ip() -> str | None:
    """This PC's address on the home network (what a console sends telemetry to)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))          # no packet is sent; just picks the interface
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


MAC = sys.platform == "darwin"
COMPUTER = "Mac" if MAC else "PC"


def telemetry_guide(platform: str, port: int) -> str:
    ip = (lan_ip() or f"<this {COMPUTER}'s IP, see below>") if platform == "console" \
        else "127.0.0.1"
    lines = [
        "",
        "In F1 25: Game Options > Settings > Telemetry Settings",
        "    UDP Telemetry ........ On",
        "    UDP Broadcast Mode ... Off",
        f"    UDP IP Address ....... {ip}",
        f"    UDP Port ............. {port}",
        "    UDP Send Rate ........ 60Hz",
        "    UDP Format ........... 2025",
        "    Your Telemetry ....... Public",
    ]
    if platform == "console" and MAC:
        lines += [
            "",
            "The Mac must be on the same Wi-Fi / router as the console. If the IP above is",
            "missing or wrong: System Settings > Wi-Fi > Details (next to your network) > IP",
            "address.",
            "When macOS asks to allow incoming connections or to find devices on your local",
            "network, click Allow, otherwise the console's telemetry is blocked.",
            "The engineer's voice plays on this Mac (speakers, or AirPods paired to the Mac).",
        ]
    elif platform == "console":
        lines += [
            "",
            "The PC must be on the same Wi-Fi / router as the console. If the IP above is",
            "missing or wrong: Start > type cmd > Enter > type ipconfig > use the 'IPv4 Address'.",
            "When Windows asks about the firewall, tick 'Private networks' and click Allow,",
            "otherwise the console's telemetry is blocked.",
            "The engineer's voice plays on this PC (speakers, or earbuds paired to the PC).",
        ]
    else:
        lines += ["", "When Windows asks about the firewall, click Allow."]
    return "\n".join(lines)


def _ask(prompt: str, options: dict[str, str], default: str) -> str:
    while True:
        answer = input(prompt).strip().lower() or default
        if answer in options:
            return options[answer]
        print("  Please type one of: " + ", ".join(options))


def seed_tracks(cfg) -> None:
    """Copy the corner maps that ship with the app (e.g. Austria) into the user's data folder."""
    src = BUNDLE_DIR / "data" / "tracks"
    cfg.tracks_path.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.json"):
        dest = cfg.tracks_path / f.name
        if not dest.exists():
            shutil.copy2(f, dest)


def first_run_setup() -> None:
    print("=" * 64)
    print(" Race Engineer setup")
    print("=" * 64)
    if MAC:
        # F1 25 doesn't run on macOS: the Mac is the engineer for an Xbox/PlayStation player.
        print("\nThis Mac will be the race engineer for F1 25 on your Xbox or PlayStation.")
        platform = "console"
    else:
        platform = _ask("\nWhere do you play F1 25?\n  [1] On this PC\n  [2] On an Xbox or "
                        "PlayStation\nType 1 or 2 and press Enter: ",
                        {"1": "pc", "2": "console"}, "1")
    ptt = False
    if platform == "pc":
        ptt = _ask("\nAsk the engineer questions by holding LB on an Xbox controller? "
                   "[Y/n]: ", {"y": True, "yes": True, "n": False, "no": False}, "y")
    local = {"platform": platform, "upload": {"auto": False},
             "voice": {"enabled": ptt, "model": FRIEND_SPEECH_MODEL}}
    APP_HOME.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG.write_text(yaml.safe_dump(local, sort_keys=False), encoding="utf-8")
    cfg = load_config()
    seed_tracks(cfg)

    print("\nDownloading the engineer's voice (about 60 MB)...")
    try:
        from engineer.radio.setup_voice import download_voice
        download_voice(cfg.radio.voice, cfg.voices_path)
    except Exception as exc:
        print(f"  Could not download the voice ({exc}). The {'Mac' if MAC else 'Windows'} "
              f"voice will be used; run setup again later to retry.")
    if ptt:
        print(f"\nDownloading speech recognition ({FRIEND_SPEECH_MODEL}, about 250 MB)...")
        try:
            from engineer.voice.speech import SpeechSettings, load_model
            _model, desc = load_model(SpeechSettings(
                model=cfg.voice.model, device=cfg.voice.device,
                compute_type=cfg.voice.compute_type, models_dir=str(cfg.ptt_path / "models")))
            print(f"  Ready: {desc}")
        except Exception as exc:
            print(f"  Could not download it ({exc}). Questions are off until setup is run "
                  f"again.")
            local["voice"]["enabled"] = False
            LOCAL_CONFIG.write_text(yaml.safe_dump(local, sort_keys=False), encoding="utf-8")

    print("\n" + "=" * 64)
    print(" Set up the game's telemetry like this (once):")
    print(telemetry_guide(platform, cfg.listen.port))
    print("=" * 64)
    input("\nPress Enter to start the engineer...")


def banner(cfg) -> None:
    print("=" * 64)
    print(" Race Engineer")
    if cfg.platform == "console":
        print(f" Console mode: set UDP IP Address in F1 25 to {lan_ip() or '(see setup)'}, "
              f"port {cfg.listen.port}")
    print(f" Your data: {APP_HOME}   (log: logs/race-engineer.log)")
    if MAC:
        print(" To run setup again: delete config.local.yaml in the folder above, then reopen.")
    else:
        print(" Run 'Race Engineer setup' from the Start menu to change settings.")
    print("=" * 64)


def self_check() -> int:
    """`RaceEngineer.exe --check`: load every part of the app and run one strategy sim in a worker
    process, then exit. The build uses it to catch anything the bundle is missing."""
    import io
    import time

    import faster_whisper  # noqa: F401
    import onnxruntime  # noqa: F401
    import piper  # noqa: F401
    import pyarrow  # noqa: F401
    from engineer.app import build_engine
    from engineer.strategy.runner import WorkerRunner
    from engineer.voice.controller import XInputButton
    if sys.platform == "win32":                # Windows bundles PortAudio and the SAPI voice
        import pyttsx3  # noqa: F401
    if sys.platform in ("win32", "darwin"):     # PortAudio ships inside sounddevice there
        import sounddevice  # noqa: F401
    cfg = load_config()
    build_engine(cfg, out=io.StringIO())
    runner = WorkerRunner(None)
    from engineer.strategy.model import ModelSettings
    from engineer.strategy.runner import StrategyRequest
    from engineer.strategy.sim import RaceSituation
    sit = RaceSituation(lap_done=2, total_laps=20, compound=17, wear=8.0,
                        sets={16: [0.0], 18: [0.0]}, used=frozenset({17}), require_two=True,
                        limit_pct=60.0, pit_loss_s=22.0)
    runner.submit(StrategyRequest(1, "check", 17, "0", (), (), None, sit, ModelSettings()))
    got, deadline = [], time.monotonic() + 60
    while not got and time.monotonic() < deadline:
        got = runner.poll()
        time.sleep(0.05)
    runner.close()
    print(f"XInput available: {XInputButton().available}")
    if not got or got[0].error:
        print(f"CHECK FAILED: strategy worker {got[0].error if got else 'no answer'}")
        return 1
    print(f"CHECK OK: strategy worker answered ({got[0].sim.best})")
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()            # the worker processes start this exe too
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        return self_check()
    start_log()
    code = 0
    try:
        if "--setup" in args or not LOCAL_CONFIG.exists():
            first_run_setup()
        cfg = load_config()
        seed_tracks(cfg)
        banner(cfg)
        from engineer.cli import main as cli_main
        code = cli_main(["run"])
    except KeyboardInterrupt:
        pass
    except Exception as exc:                    # keep the window open so the error can be read
        import traceback
        traceback.print_exc()
        print(f"\nSomething went wrong: {exc}")
        print(f"A log of this window is saved in {LOG_FILE}")
        code = 1
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
