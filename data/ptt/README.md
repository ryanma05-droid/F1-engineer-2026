# data/ptt

Push-to-talk (Phase 6). Not committed, except this README.

- `questions.jsonl`: every question you asked: what was heard, which intent it matched, the
  answer. Questions marked `"intent": "unknown"` are the phrasings to teach the engineer next.
- `clips/`: the audio of each question (WAV, 16 kHz), for testing new rules on your real voice.
- `models/`: the speech recognition model (downloaded once by `python -m engineer.voice.setup`).
