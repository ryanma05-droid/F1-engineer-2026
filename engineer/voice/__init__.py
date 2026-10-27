"""Push-to-talk (Phase 6): hold LB on the Xbox controller, ask, and the engineer answers.

- `controller.py` reads the button (XInput, shared with the game).
- `speech.py`     records the microphone and turns speech into text (faster-whisper), in a worker
                  process.
- `intents.py`    works out what was asked from the words.
- `answers.py`    answers from the same query functions the engineer's own calls use, and logs
                  every question (data/ptt/questions.jsonl) so new phrasings can be added.
"""
