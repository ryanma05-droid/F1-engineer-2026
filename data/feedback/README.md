# data/feedback

Your feedback log. Not committed, except this README.

Flag a question on the radio and it's kept here with the race at that moment, so it can be
answered in a later version. You decide what goes in: only the questions you flag.

- Right after a question: **"log that"** (or "flag that", "note that").
- In one go: **"log the following question: what tyres is the car ahead on?"**
- "Log the following question" on its own: the engineer says "Go ahead" and logs the next one.

Files:
- `logged.jsonl`: every flagged question with what the engineer said, the lap, position, tyres,
  gaps, the strategy call and the last radio calls. The race result is added when it ends.
- `feedback-log.md`: the readable list, rebuilt when the race ends. Questions a newer version
  recognises are marked "matches ... now". Dismiss the ones you don't want answered in the app
  window (Live tab, Logged questions).

Flagged questions are also in `data/ptt/questions.jsonl`, so they go up with the recording when
it's uploaded.
