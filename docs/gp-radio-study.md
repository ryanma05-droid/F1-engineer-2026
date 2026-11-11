# GP: what the real radio showed (12 Nov)

GP is Gary's voice with a race engineer's manner learned from broadcast team radio. This note
records the study behind it, so it can be redone or extended.

## Data

- formuladream.app's archive of broadcast team radio (public session pages; robots.txt allows
  them, its /api/ was not used): every message on Max Verstappen's car in races and sprints,
  2021 and 2023-25. 2022 isn't in the archive. 971 messages, 976 engineer lines, 649 driver
  lines, each line labelled engineer or driver (automatically: a few are swapped).
- OpenF1 (api.openf1.org) timing for 2023-25: lap, position, intervals, pit stops, race control
  at the moment of each message (sample of 286 messages / 314 engineer lines).
- Raw data stays out of this repo (it's someone else's archive and broadcast audio). The scripts
  that fetched and counted it were throwaway; re-running means fetching again, politely
  (1.5 s between pages).
- The archive only holds what was broadcast, mostly the dramatic moments: rates are skewed,
  so the study is used for manner and relative timing, not "how often to talk".

## What it showed

- Short and calm: median 11 words a line (quartiles 5 and 19).
- The driver's name in 44 % of lines; "mate" in about 1 in 10. Common openings:
  "Understood", "Well done", "Okay Max", "Copy that".
- The key number said twice ("gap at three point eight, three point eight"); box calls doubled
  and confirmed ("box and confirm ... box, confirm").
- What the engineer talks about (keyword buckets): acknowledgements 18 %, tyres 11 %, gaps 10 %,
  praise 10 %, car modes 5 %, driving notes 4 %, box 3 %, finish 3 %, strategy 2 %, safety car
  2 %, the rest procedures and chat.
- When: about half of the broadcast engineer lines are around the start (formation lap,
  start, lap 1) and a quarter after the flag (debrief, praise). Mid-race lines are rare and
  purposeful: rival information, strategy decisions, acknowledgements.
- Bad news without drama; praise specific and measured ("very well controlled", "clinical",
  "textbook"); dry humour now and then; a short lift in the closing laps.

## How GP uses it (engineer/radio/gp.py)

Wraps calls like Kevin, never changes them: your name (or "mate") on about a third of calls and
never twice in 25 s, an acknowledgement before half the answers, the gap echoed in gap calls,
"box and confirm" after box calls, calm openers on lost places, measured praise on passes and
at the flag, a lift with five laps and one lap to go. Seeded per session.

Not used: car modes (strat modes, engine and diff settings) have no equivalent the game sends;
the pre-race procedures; anything quoted at length.
