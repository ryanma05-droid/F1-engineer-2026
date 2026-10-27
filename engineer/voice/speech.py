"""Speech process: records the microphone while the button is held and turns it into text.

Runs in its own process (CLAUDE.md: speech recognition never on the main loop). The main process
sends "start" when the button goes down and "stop" when it comes up; this process sends back what
it heard.

- Microphone: the Windows default recording device (the one the game uses), unless
  `voice.input_device` names another. The stream stays open and keeps the last 0.3 s, so the
  first word isn't clipped when you start talking as you press.
- Speech to text: faster-whisper, English model (`voice.model`, default medium.en), on the GPU if
  there's a CUDA one, else the CPU (int8). The model downloads once into data/ptt/models/.
- Every question's audio is saved to data/ptt/clips/ (`voice.keep_audio`) so replays and new
  intent rules can be tested on your real voice.
"""

from __future__ import annotations

import logging
import queue as queue_mod
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engineer.workers.pool import start_worker

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000              # what whisper wants
PRE_ROLL_S = 0.3
# Words the model should expect (helps with "box", compound names, "undercut", ...).
PROMPT = ("Radio check. How are the tyres? What's the plan? When do I box? What if I box now? "
          "Gap ahead? Gap behind? Fuel? Softs, mediums, hards. Undercut. Laps to go. Quiet.")


@dataclass
class SpeechSettings:
    model: str = "medium.en"
    device: str = "auto"             # "auto", "cuda" or "cpu"
    compute_type: str = "auto"       # "auto": float16 on GPU, int8 on CPU
    input_device: str | int | None = None
    models_dir: str | None = None
    clips_dir: str | None = None     # None: don't keep audio
    beam_size: int = 3
    backend: str = "whisper"         # "whisper" or "null" (tests: hears nothing)


def list_input_devices() -> list[tuple[int, str, bool]]:
    """(index, name, is_default) for every microphone."""
    import sounddevice as sd

    default_in = sd.default.device[0]
    return [(i, d["name"], i == default_in)
            for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def resolve_input_device(spec: str | int | None) -> tuple[int | None, str]:
    devices = list_input_devices()
    if spec is None or spec == "":
        default = next((d for d in devices if d[2]), None)
        return None, (default[1] if default else "system default")
    if isinstance(spec, int) or str(spec).isdigit():
        match = next((d for d in devices if d[0] == int(spec)), None)
    else:
        match = next((d for d in devices if str(spec).lower() in d[1].lower()), None)
    if match is None:
        print(f"[voice] microphone {spec!r} not found; using the default. "
              f"List them with: python -m engineer.voice.setup --mics", flush=True)
        return resolve_input_device(None)
    return match[0], match[1]


def load_model(settings: SpeechSettings):
    from faster_whisper import WhisperModel

    device = settings.device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute = settings.compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(settings.model, device=device, compute_type=compute,
                         download_root=settings.models_dir)
    return model, f"{settings.model} on {device} ({compute})"


def transcribe(model, audio: np.ndarray, beam_size: int = 3) -> str:
    segments, _info = model.transcribe(audio, language="en", beam_size=beam_size,
                                       initial_prompt=PROMPT, condition_on_previous_text=False,
                                       vad_filter=False)
    return " ".join(s.text.strip() for s in segments).strip()


def save_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def speech_main(inbox, outbox, settings: SpeechSettings) -> None:
    """Worker process. Messages in: ("start", id), ("stop", id), ("cancel", id), None = quit.
    Out: ("ready", description), ("error", message), ("heard", id, text, wav_path, stt_ms)."""
    logging.basicConfig(level=logging.INFO)
    model = None
    try:
        if settings.backend == "whisper":
            model, desc = load_model(settings)
        else:
            desc = "null"
        import sounddevice as sd

        device, mic = resolve_input_device(settings.input_device)
        pre = deque(maxlen=int(PRE_ROLL_S * SAMPLE_RATE / 512) + 1)
        chunks: list[np.ndarray] = []
        recording = {"on": False}

        def callback(indata, frames, t, status):
            block = indata[:, 0].copy()
            if recording["on"]:
                chunks.append(block)
            else:
                pre.append(block)

        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                blocksize=512, device=device, callback=callback)
        stream.start()
        outbox.put(("ready", f"{desc}; microphone: {mic}"))
    except Exception as exc:
        outbox.put(("error", f"{type(exc).__name__}: {exc}"))
        return
    while True:
        msg = inbox.get()
        if msg is None:
            stream.stop()
            stream.close()
            return
        kind, qid = msg
        if kind == "start":
            chunks.clear()
            chunks.extend(pre)
            recording["on"] = True
        elif kind == "cancel":
            recording["on"] = False
            chunks.clear()
        elif kind == "stop":
            recording["on"] = False
            audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
            chunks.clear()
            wav = None
            if settings.clips_dir and audio.size:
                wav = str(Path(settings.clips_dir) / f"{time.strftime('%Y%m%dT%H%M%S')}_{qid}.wav")
                save_wav(Path(wav), audio)
            t0 = time.perf_counter()
            try:
                text = transcribe(model, audio, settings.beam_size) \
                    if model is not None and audio.size > SAMPLE_RATE * 0.2 else ""
            except Exception as exc:
                log.exception("speech recognition failed")
                outbox.put(("error", f"recognition failed: {exc}"))
                continue
            outbox.put(("heard", qid, text, wav, (time.perf_counter() - t0) * 1000))


class SpeechProcess:
    """Main-process side: start(), then press()/release() from the button; poll() for results."""

    def __init__(self, settings: SpeechSettings):
        self.settings = settings
        self.proc = self.inbox = self.outbox = None
        self.description: str | None = None
        self.error: str | None = None
        self._id = 0

    def start(self, wait_ready_s: float = 600.0) -> bool:
        """Starts the process and waits until the model is loaded (the first run downloads it)."""
        self.proc, self.inbox, self.outbox = start_worker(speech_main, self.settings)
        try:
            msg = self.outbox.get(timeout=wait_ready_s)
        except queue_mod.Empty:
            self.error = "speech process did not start in time"
            return False
        if msg[0] == "ready":
            self.description = msg[1]
            return True
        self.error = msg[1]
        return False

    def press(self) -> None:
        self._id += 1
        self.inbox.put(("start", self._id))

    def release(self) -> None:
        self.inbox.put(("stop", self._id))

    def cancel(self) -> None:
        self.inbox.put(("cancel", self._id))

    def poll(self) -> list[tuple]:
        out = []
        while self.outbox is not None:
            try:
                out.append(self.outbox.get_nowait())
            except queue_mod.Empty:
                break
        return out

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.inbox.put(None)
            self.proc.join(timeout=3)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()
