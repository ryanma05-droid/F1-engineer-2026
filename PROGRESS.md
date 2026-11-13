# Progress

## Phase 0 — foundation

- [x] 1. Project setup (pyproject, .gitignore, config.yaml, CLAUDE.md, docs/architecture.md, spec saved)
- [x] 2. Versioned parser (`engineer/ingest/formats/f1_2025.py`): all 16 packet dtypes, sizes asserted
- [x] 3. UDP listener (arrival timestamps, fan-out, forwarding on a separate socket)
- [x] 4. Recorder (zstd raw streams per session UID) + SQLite session index
- [x] 5. Race state (session, 22-car arrays, player view, restricted data and inactive cars as NaN)
- [x] 6. Event bus + detection (SessionStarted/Ended, LapCompleted, SectorCompleted, PitEntry/Exit, Flashback)
- [x] 7. Replay runner (in-process at 1x / Nx / max, or `--udp`)
- [x] 8. CLI lap lines (`python -m engineer run`, `python -m engineer sessions`)
- [x] 9. Tests (63 passing, including the golden test) and benchmark
- [x] Golden test with a real recording (`tests/fixtures/austria_race_5laps.zst`)
- [x] Acceptance run in the real game on Windows

**Phase 0 accepted.**

## Real-game results (Austria, 5-lap race, 20 cars, Windows, 26 Oct 2026)

- `engineer run` on Windows: port bound, 114,689 packets, 0 rejected, clean Ctrl+C.
- Replay of the recording prints identical lap lines to the live run.
- Every lap of all 20 cars (5 each, no duplicates) matches the game's own Session History lap times.
  The player's 5 lap times, S1 and S2 match the game exactly; lap 5 matches the game's
  fastest-lap event (1:09.126).
- A flashback on lap 3 was handled: the lap was reported once, with the right time.
- Benchmark on the real recording: mean 31 us, p50 14 us, p99 139 us, max 3.0 ms per packet.

## Questions settled by the real recording

- **Lap Data field order**: spec order is CORRECT (not swapped). `m_gridPosition` is zero-based in
  practice (pole = 0), which likely explains the community "grid reads 0" report.
- **Final race lap**: the game does NOT advance `m_currentLapNum` at the chequered flag; the
  last-lap time changes on the same lap and result status becomes Finished a frame later. The
  detector now completes a lap on that signal. (Live run printed only 4 of 5 laps before this fix.)
- **Last-lap time timing**: updates on the same frame as the lap number, so lap lines are instant.
- **Restricted telemetry**: the player's own `m_yourTelemetry` can be 0 (restricted); the player car
  is still treated as visible, as the spec says.

## Still open

- **Sector 3 is 1-2 ms off the game's value.** We compute S3 = lap - S1 - S2 the moment the lap ends;
  the game rounds each sector separately (e.g. ours 21.660, game's 21.659). The game's exact S3
  arrives in Session History up to ~1.1 s later. Fine for radio calls; revisit if exact S3 matters.
- **Final Classification** was not in this recording (the session was stopped before the results
  screen), so packet 8 is untested on real data. Not needed in Phase 0.
- **Flashbacks** only reset lap/sector tracking; fuller rollback can wait until Phase 4 traces.

## Phase 1 — radio director, voice, lap delta

- [x] Lap history (`engineer/features/laps.py`) and lap delta query (`engineer/tools/queries.py`)
- [x] Spoken phrases with verbosity terse / normal / chatty (`engineer/radio/phrases.py`)
- [x] Radio director: priority, expiry, per-topic cooldowns, quiet zones (talk on straights)
- [x] Lap delta analyzer (`engineer/analyzers/lap_delta.py`)
- [x] Audio process: Piper voice + radio effect, Windows-voice fallback (`engineer/radio/tts.py`)
- [x] Voice download / test command (`python -m engineer.radio.setup_voice --test`)
- [x] CLI: `engineer run` speaks (`--no-voice`, `--verbosity`); replay prints calls, `--voice` speaks
- [x] Tests (106 passing) incl. golden radio calls on the Austria recording
- [x] Hear the real Piper voice and radio effect on Windows (works, through the Astro MixAmp)
- [ ] Acceptance: voice within 1 s of crossing the line, live. The readout
      "(voice started X.XXs after the packet arrived)" after each call measures exactly this.

### Verified so far

- Austria replay: all five lap calls are released on the same frame the line is crossed (0.0 s
  held), so the latency left is synthesis + playback start on your PC.
- The quiet-zone map learned on Austria marks exactly the braking zones/corners (T1, T3, T4, T6-7,
  T9-10) and leaves the flat kinks (T2, T5, T8) as straights.
- Replay produces the same radio calls at the same game times as live (golden calls file).

### Phase 1 decisions

- Lap times are spoken truncated to the tenth, like a timing screen (1:09.162 = "one oh nine one").
  Deltas are rounded ("four tenths"); under 0.05 s is "same again".
- Race lap 1 is never compared (standing start) and never used as a reference; neither are invalid
  laps, in-laps or out-laps. When the reference isn't the previous lap, the call says "on your last
  clean lap".
- Lap deltas wait up to 3 s for a straight, then are dropped (config `radio.lap_delta_max_wait_s`).


## Phases 2 and 3 — feature tables and tyre coaching

- [x] Lap feature record per car per lap (`engineer/features/lap_features.py`)
- [x] Tyre temperatures per sector and wheel during each lap; flashback keeps the earlier sectors
- [x] Stints (tyre changes, box before/after the line, drive-throughs ignored)
- [x] Offline tables: `python -m engineer.features build`, `query`, `tyres` (Parquet + DuckDB)
- [x] Wear history per track and compound, used while a stint is under 3 laps
- [x] Queries: `tyre_status`, `pace_trend_ms_per_lap`, `stint_summary`
- [x] Tyre radio calls (`engineer/analyzers/tyres.py`), settings in `config.yaml` `tyres:`
- [x] Tests (119 passing), incl. the Austria golden calls with the new lap-3 tyre call
- [x] Fixed: an out-lap starting in the pit lane was also flagged as an in-lap
- [x] Real race: Austria, 18 laps, pit on lap 2 (Hard -> Medium) under a full safety car
      (laps 2-4), recording `20261027T010920Z_cdb2205fbdb7a7db` on branch
      `recording-20261027t010920z`. Stint split, wear warnings (40% lap 14, 50% lap 17) and laps
      left (7 at 30%, 5 at 42%, 2 at 52%; actual 56% at the flag) all behaved.
- [x] Fixed from it: safety car / VSC laps are no longer lap-delta references (lap 5 was called
      "twenty seven seconds quicker" than the lap behind the safety car); drop-off ignores mistake
      laps (>1 s off the recent median) and must show on two laps running; equal wear on all four
      is said as "tyres all even at five percent".
- [x] Share command for big recordings: `python -m engineer.ingest.share` (90 MB parts)
- [ ] Tune the temperature window (80-110 C carcass is a first guess). Real data so far: Softs
      86-96 C, Mediums 87-98 C in clean running, 63-75 C behind the safety car, 76-79 C on the
      restart lap (the one cold call). No hot call yet.

### Tyre calls (your choices)

| Call | When |
| --- | --- |
| Status: "Tyres: front right worst at nineteen percent, about four percent a lap." | every 3 laps of a stint |
| ... once the worst tyre is 25%+: "..., about ten laps left on these." | laps left = laps until a tyre reaches 60% |
| Warning: "Front right at fifty percent, look after it, ..." | worst tyre passes 40 / 50 / 60% |
| Temperature: "Rears running hot in sector three." | carcass outside 80-110 C for 60% of a sector; not on lap 1, in/out laps, SC |
| Drop-off: "Pace dropping two tenths a lap, tyres are going." | fuel-corrected pace fades > 0.15 s/lap over 4 clean laps |
| Damage / blisters | blistering, or damage beyond wear, up 10+ points |

### Decisions

- "Laps left starting at 25%" is read as: laps left is spoken once the worst tyre reaches 25%;
  laps left counts to a 60% limit. Both are in `config.yaml` (`laps_left_from_pct`, `limit_pct`).
- In F1 25 tyre damage equals wear in normal running, so damage calls only count damage beyond wear.
- Temperatures use the carcass (inner) temperature; the surface is too spiky to judge.
- Wear rate = straight-line fit over the last 8 laps of the stint (in-laps left out).

### Findings from the 18-lap race

- Your Medium (C4) stint: front right 3.9 %/lap, front left 3.3, rears 3.0. The AI cars on the same
  compound wore the front right at 1.6 %/lap (median of 15 stints): you're about 2.4x harder on it.
- Pace stepped from ~1:09.8 (laps 6-8) to ~1:11.2-1:12.4 from lap 9 on and stayed there; with the
  new rules that's not called as tyre drop-off (it's a step, not a steady fade).

### Open questions

- Is 60% the right "limit" for laps left on your setup and difficulty? Easy to change.
- Real carcass temperature window per compound: needs a longer race (and a wet one for inters/wets,
  which get no temperature calls for now).

## Phase 4 — traces and corner coaching

- [x] Distance traces for all 22 cars in 5 m sections (`engineer/state/traces.py`)
- [x] Corner detection and track maps (`engineer/state/track_maps.py`, `data/tracks/`)
- [x] Austria map with official turn numbers: T1 T3 T4 T6 T7 T9 T10, found identically on both
      Austria recordings
- [x] Corner comparison queries (`corner_stats`, `corner_deltas`)
- [x] Corner radio calls (`engineer/analyzers/corners.py`), settings in `config.yaml` `corners:`
- [x] Feature tables: `traces.parquet`, `corners.parquet`; `python -m engineer.features corners`
- [x] Bench times the whole engine now: 68 us mean per packet on real data (target 1 ms); the
      slowest packet (~40 ms) is a lap completion that also learns the corner map, once
- [x] Tests (129 passing), incl. two new golden corner calls on Austria
- [ ] Hear it in a real race; check the calls feel right and come at a good moment
- [ ] Corner maps for other tracks: learned automatically the first time you drive them; rename
      turns in `data/tracks/<track>.json` if the numbers don't match

### Corner calls

| Call | When |
| --- | --- |
| "Turn three, losing two tenths to the car ahead, braking ten metres earlier." | car ahead within 2 s, same lap, both laps clean |
| "Turn one, losing five tenths to the car behind, ..." | no car ahead within 2 s, car behind within 2 s (waits up to 5 s for its lap) |
| "Turn four, three tenths off your best, minimum speed eight k p h down." | otherwise, vs your best clean lap |
| "Turn four again, ..." | same corner called within the last 3 laps |
| "Gaining a tenth on the car ahead through turn nine." | chatty verbosity only, when nothing lost 0.1 s |

### Verified on real data

- Austria 5-lap, lap 3 vs the car ahead: lost 0.20 s in T7, back on full throttle 75 m later,
  exit 229 vs 248 km/h. Lap 4: lost 0.15 s in T4 with minimum speed 10 km/h down.
- 18-lap race: T4 is your most frequent loss vs your best lap (6 of 11 calls). Your median
  corner times beat the AI's median in every corner; closest in T7 (5.51 vs 5.52 s).

### Decisions

- The cause named is the biggest relative to its threshold (10 km/h down beats braking 10 m early).
- Corner maps are learned per track and saved, rather than shipped for every track: only Austria
  has real data so far.
- Speed is said as "k p h" for the voice.

## Uploading recordings

- [x] `python -m engineer.ingest.share --upload` pushes a recording to its own branch directly
      (no branch switching; your folder is untouched); `--pending` sends everything not sent yet
- [x] Auto-upload when `engineer run` stops, switched on per PC in `config.local.yaml`
      (`upload: {auto: true}`); off by default so other people's copies don't try to push

## Phase 5 — strategy

- [x] Strategy model (`engineer/strategy/model.py`): wear rate, deg and compound offset per
      compound, pit loss green and under safety car, all learned from lap rows and blended by
      evidence with the game's own Tyre Sets figures; nothing about the tyres is fixed in code
- [x] Simulator (`engineer/strategy/sim.py`): every plan up to 3 stops, two-compound rule, only
      the sets you have, soft wear limit, pit window; ~5 ms for a 71-lap race
- [x] Runs in a worker process live, in-process for replays/tests (same calls every replay)
- [x] Radio: plan, plan change, box calls (one lap and half a lap before the pit entry, with the
      rejoin), safety car box / stay out, undercut reminder (information only)
- [x] `python -m engineer.features strategy [track]` shows what the model has learned
- [x] Tests (158 passing), incl. a brute-force check of the simulator and a worker-process run
- [x] Checked on the real 18-lap Austria race (replay and live over UDP)
- [ ] Hear it in a real race of 15+ laps with a stop
- [ ] Wet races (inters/wets) and weather changes: not covered yet

### Strategy calls

| Call | When |
| --- | --- |
| "Plan is one stop, box lap eleven to thirteen for hards. Two stops is four seconds slower." | after lap 2, and after each of your stops |
| "Plan change: one stop, box lap nine to ten for mediums." | stops/compounds change, or the stop moves 2+ laps; seen on 2 laps running |
| "Box this lap, box this lap. Hards. You'll come out behind Sainz, about two seconds back." | one lap before the pit entry (start of the pit lap) |
| "Box box, end of this lap. Hards." | half a lap before the pit entry |
| "Safety car, box this lap, box this lap. Mediums, it's a cheap stop." | SC/VSC out and stopping under it beats the plan |
| "Safety car, stay out. Stopping now doesn't pay." | SC/VSC out, you have a stop to make, but not now |
| "Norris behind has pitted, watch the undercut." | car right behind (within 3 s) pits while you still have a stop to make |

### How the tyre model learns (and keeps learning)

- Wear rate per compound: this stint + your past stints at the track + the AI field scaled by
  your wear ratio vs the AI (learned over every track; 1.5x on your recordings so far) + the game's
  tyre life figures for compounds with no data.
- Deg (time per % of wear): your clean laps (fuel corrected, each stint its own baseline) and the
  game's figure, each weighed by its precision (standard error); a curve is added once your laps
  reach 40%+ wear. Austria mediums: your laps say 0.053 s per %, the game 0.050.
- Compound offsets: the same car on two compounds in one race + the game's figure.
- Pit loss: in-lap + out-lap vs the car's laps around them (any car, history + this race); under
  SC vs the cars that stayed out.

### Checked on the real 18-lap Austria race

- Lap 2 safety car: "Safety car, box this lap, box this lap. Mediums" (what you and the field did).
- After the stop: "Plan is no stop, tyres will make the end. One stop is eight point five seconds
  slower." Right call: your mediums finished at 56%.
- Your laps 9-11 were ~2 s slower (no traffic: you led). Before the fixes below that briefly made
  the plan box for softs.

### Decisions

- The wear limit (60%) is soft for strategy: each lap past it costs 1 s per % over. Finishing 2%
  over shouldn't cost a 22 s stop.
- An extra stop must save 3 s to become the plan; a change in stop count needs 2 laps to confirm;
  deg estimates are weighed by precision so a few odd laps barely move them.
- AI lap times aren't used for deg: on real data they barely slow with wear.
- No plan is made from an in-lap (it ends in the pit lane on the old tyres).
- Pit loss is 22 s (SC: half) until a stop at the track is measured. Your Austria recording only
  has stops under the safety car, which measured 17.4 s.

### Open questions

- Real green-flag pit loss per track: measured automatically from the first race with a stop (yours
  or the AI's) that isn't under the safety car.

## Phase 6 — push-to-talk

- [x] LB on the Xbox controller (XInput, read-only, works alongside the game)
- [x] Speech process: Windows default microphone, faster-whisper `medium.en` (GPU if you have an
      Nvidia card, else CPU), in its own process; each question's audio kept in `data/ptt/clips/`
- [x] Questions understood by keyword rules (`engineer/voice/intents.py`); answers from the same
      numbers the engineer's own calls use
- [x] Quiet mode ("Quiet" / "Talk to me"): only box calls, safety car calls and answers
- [x] "What if I box now?" runs the strategy simulator with the stop forced on this lap
- [x] Question log `data/ptt/questions.jsonl`, uploaded with each recording; unknown questions are
      the list of phrasings to add
- [x] `replay --questions` asks the logged questions again at the same moment
- [x] `python -m engineer.voice.setup`: downloads the model, lists mics, live LB test
- [x] Tests (194 passing); speech recognition itself is not tested here (the model can't be
      downloaded in the cloud container): check it with `voice.setup` on Windows
- [ ] First run on Windows: model download, LB, mic, recognition speed with medium.en
- [ ] Add phrasings from the question log as they come up

### What you can ask

| You say (examples) | Answer |
| --- | --- |
| "Radio check" | "Loud and clear." |
| "How are the tyres?" | "Tyres: front right worst at thirty two percent, about four percent a lap, about seven laps left on these." |
| "What's the plan?" / "When do I box?" | "Plan is one stop, box lap fourteen to sixteen for hards. ..." |
| "What if I box now?" / "Should I pit?" | "Box now costs about three seconds over the plan, hards. You'll come out behind Sainz, ..." |
| "Gap ahead?" / "Gap behind?" | "Norris ahead, one point two seconds." |
| "How was that lap?" | lap time and delta |
| "Where am I losing time?" | "Turn four, three tenths off your best, exit speed eight k p h down." |
| "Fuel?" | "Fuel is fine, plus zero point four laps." |
| "Position?" / "How many laps left?" | "P three, eight laps to go." |
| "Quiet" / "Talk to me" | quiet mode on / off |
| anything else | "Say again." (and logged) |

### Decisions

- Keyword rules, not a chatbot: instant, offline, predictable; grown from the question log.
- Answers are said straight away (no waiting for a straight): you chose the moment by pressing.
- The game also sees LB: unbind LB in the F1 25 controls if it does something there.

## Shareable app (Windows installer)

- [x] One-folder app (PyInstaller) wrapped in an installer (Inno Setup): per-user install, Start
      menu shortcuts (Race Engineer, Race Engineer setup, Telemetry guide), optional desktop icon
- [x] GitHub Actions builds it on Windows, runs the tests and a self-check (`--check`); a `v*`
      tag publishes `RaceEngineer-Setup-<version>.exe` on the Releases page
- [x] First-run setup: PC or Xbox/PlayStation, voice download, speech model (`small.en`) for PC
      players who want questions, telemetry settings with this PC's IP for console players
- [x] Settings and data in `%LOCALAPPDATA%\RaceEngineer`; auto-upload off for friends
- [x] Console players: telemetry over Wi-Fi to the PC, voice on the PC, push-to-talk off
- [x] Each run is added to the tyre/strategy history when the app closes (no manual build)
- [x] Telemetry guide: `docs/telemetry-guide.md` (also in the Start menu)
- [x] Window output saved to `%LOCALAPPDATA%\RaceEngineer\logs\race-engineer.log` (send it if something breaks)
- [x] Linux test build: first-run setup in console mode + a real recording over UDP gave the same
      lap lines and calls as the golden test
- [ ] First Windows install from the Releases page
- [ ] Not code-signed: Windows SmartScreen shows "More info > Run anyway" the first time

## Mac app (for console players)

- [x] Mac build in the same GitHub workflow (Apple Silicon runner): tests, PyInstaller bundle,
      `--check`, then "Race Engineer.app" in `RaceEngineer-<version>-mac.dmg`
- [x] Always console mode (F1 25 isn't on macOS): telemetry over Wi-Fi, voice on the Mac,
      push-to-talk off; setup shows the Mac's IP and where to find it
- [x] Data in `~/Library/Application Support/RaceEngineer`; fallback voice is the Mac's `say`
- [x] "READ ME FIRST" in the dmg: Open Anyway in Privacy & Security, plus the `xattr` fallback
- [ ] First open on a real Mac (the app is only ad-hoc signed; untested by a person yet)
- [ ] Intel Macs aren't built (Apple Silicon only)

### First real race with push-to-talk (27 Oct)

- Recording and answering worked, but "Gap behind" took **41 s** to recognise: on the GPU, the
  speech model waits for F1 25, which keeps the GPU busy in a race. Back in the menu it took
  0.18 s. Fix: speech on the CPU by default (small.en, 4 threads, beam size 1), and an answer
  that would come more than 10 s late isn't said (it's still logged).
- [ ] Check CPU recognition time with the game running (voice.setup prints it; target < 1.5 s)

### Questions from the 27 Oct Abu Dhabi race (added)

| Asked | Was | Now |
| --- | --- | --- |
| "How are the tyres?" on lap 1 | "No tyre numbers yet" | live wear: "Rear left at two percent, too early for a wear rate." |
| "How was that exit?" (heard "How does that exit?") | "Say again" | new `corner` question: the corner you just left (or "turn four"), vs your best lap |
| "Where am I gaining on Lando?" / "... speed to Kimi Antonelli?" | position answer | new `compare_driver`: last clean lap vs that driver, biggest gain and loss (corners, or sectors before the corner map exists); first names known |
| "Where can I improve pace?" | "Nothing to compare yet" (new track) | sector fallback: "Sector two, losing two tenths to the car ahead." |

Also added: gap to a named driver, gap to the leader / who's leading, "where am I gaining",
damage, best lap, weather, "what lap is it". Speech model now `base.en` (small.en took ~3 s per
question on the CPU during that race), with drivers' names in its vocabulary.

### Corner coaching: habits first (28 Oct)

- [x] A corner that loses time 2+ coached laps running is called before a bigger one-off loss,
      at NORMAL priority (was LOW), as "Turn four, third lap running, ..."
- [x] During a streak the cause named is the one that keeps recurring, not the biggest this lap.
      18-lap Austria replay: turn four lost time 11 laps running; the cause named was exit speed
      on 9 of 11 laps (it used to change almost every lap)
- [ ] Turn four is now called every lap of a long streak: see the open proposals (tyre-matched
      reference, back off after 3 calls, confirm when fixed)

### Corner coaching: fair, closes the loop, quiet in fights (28 Oct)

- [x] "Your pace" reference: fastest clean lap on the same compound within 8 % tyre wear (was:
      best lap of the session, usually on fresh tyres). 18-lap Austria replay: turn four's
      11-lap "habit" mostly disappears (it was tyre wear); calls spread over T1, T3, T6, T9, T10
- [x] "Turn four better, two tenths found." once a habit corner is fixed (your own corner time,
      not the delta: the car ahead can change). Only after habits; one-off losses don't count
- [x] After 3 calls on a corner in a streak: every other lap, unless it gets worse
- [x] Within 1 s of a car: only calls vs the car ahead (where to pass); none vs your own pace
- [x] Golden calls unchanged (5-lap Austria)

## Race craft calls (28 Oct)

- [x] Start: "Great start, up nine places." / "Lost two places at the start, long way to go."
- [x] Passes: "Great move on Norris, P four." only once a pass has stuck 4 s (the game reports
      every swap: 472 in the 18-lap race), grouped ("Two cars in one go, P six"), varied wording;
      "Lost P three to Leclerc. Stay with Leclerc."
- [x] Defending: "Leclerc behind, eight tenths and closing, DRS range. Defend." then "Good
      defending" when it drops back past 2 s
- [x] Attacking, only when realistic: "Norris ahead, two point one. Catching three tenths a lap,
      on Norris in about four laps. Keep pushing." / "DRS range on Norris. Go get Norris."
- [x] Countdown (five laps to go, last lap), finish ("P one! You've won it!"), fastest lap
      (when taken), penalties and warnings, "Safety car in this lap", damage after contact
- [x] Golden calls updated (checked by hand against lap-end positions and the game's events)
- [ ] Hear it in a race: is the amount right?

## One-time welcome (28 Oct)

- [x] The first time the engineer starts (with a working voice), after "Radio check": "This is
      your race engineer. I'll call lap times, tyres, strategy and corners, and keep you posted
      in battles. Hold L B any time to ask me something, like how are the tyres. Good luck out
      there." Never again (data/once.json). No L B line without push-to-talk (console players)
- Not on the grid: real races start only ~6 s before lights out (SSTA, then the five lights
  straight away; the game's clock stays at 0 until lights out), so it would talk over the start

## Driver report (28 Oct)

`uv run python -m engineer.report` builds a PDF of your strengths and weaknesses from every
recording (data/reports/driver-report-<date>.pdf, plus driver-report-latest.pdf) and opens it.
When `engineer run` stops and there are sessions the last report hasn't seen, and no report
today, it builds one in a background process (`report.daily`, `report.open_when_ready`).

- Each recording is analysed once (corner runs from the distance traces, you vs the median of
  the five quickest AI cars in that session) and cached in data/reports/cache/; the cache is
  redone if the recording, the analysis version or the track map changes.
- Sessions are combined with recency weights (half every 30 days), so the picture follows you.
- Sections: tracks ranked, corner types (slow/medium/fast, onto a straight or into a corner),
  habits that cost time ranked by s/lap with a per-session trend, strengths, session history,
  every corner. "Since last report" compares with the last report from an earlier day.
- Habits are rules over the measured numbers (winning the entry / losing the exit, slow squeeze
  to full throttle, braking too early, over-slowing the apex, coasting, exit speed onto
  straights); a habit only shows when it costs 0.03 s a lap or more.
- PDF: printed by Edge (comes with Windows) or Chrome headless; no browser: the HTML is kept.
- First report from the 3 real races: quickest in slow corners onto straights (Austria T3), the
  biggest habit is winning the entry and losing the exit in medium corners (~0.18 s/lap).

## App window (28 Oct, for 0.1.2)

`engineer run` (and the installed app) now opens an app window (Edge/Chrome app mode, a page
served on this PC only) while it listens and records:
- First time: which controller (PlayStation: hold L1 / Xbox: hold LB / no push to talk), then the
  engineer tempo (Quiet / Normal / Chatty) with the tip that it can be changed by voice.
- Live: session (track, lap, position, last/best lap, tyres), connection and recording status,
  team radio feed, push-to-talk status (lights up while the button is held) and recent questions,
  tempo switch.
- Driver report: headline numbers, the major themes as cards (cost per lap, trend, what to try,
  where it shows), strengths, tracks, corner types, and the full PDF; "Build new report".
- Settings: controller, default tempo, telemetry steps. Changes apply live and are saved to
  config.local.yaml.
- Tempo by voice: "talk more" (chatty), "keep it short" (quiet), "back to normal". The Quiet
  tempo now also leaves out low-priority extras (routine tyre status, one-off corner tips,
  race-craft chatter) but keeps strategy plans and tyre warnings.
- Open question: the PlayStation L1 reading (winmm, button 5) is untested on a real
  DualSense/DualShock; Steam Input users are covered by also reading LB.

## Questions from the 28-29 Oct races (Melbourne, Imola x2, Abu Dhabi)

Replayed every logged question against its recording (what the engineer knew at that moment):
- 19 "say again" (18 had the data to answer) and 13 off-target answers. Added: plan for "when/which lap
  should I pit", "do I have to stop again", "what tyres should I pit for / be on", "we're on
  hards now"; box-now for "you sure we shouldn't pit", "box", "what happens if I pit for hards"
  (now forces that compound in the what-if); new answers: "are we boxing now" (yes / not yet,
  lap X), "repeat that", "how are we looking" (position, gap, tyres, next stop), "will these
  tyres make the end / get over 45%" (wear at the flag), "how many laps on these tyres", "who's
  pitted" (stops of the cars around you); coaching phrasings -> where you're losing time;
  "where's Hamilton winning"; "talk to me more" / "give me some chatter" -> chatty; "talk to me
  about tyre wear" -> tyres.
- Bug: "Restart race" keeps the session UID, so the restarted Imola race was treated as the same
  session: calls and answers were held back or dropped (cooldowns / "speaking" times from the
  first race) until about lap 17. Fixed (RaceState restart detection).
- Box-now follow-up answers weren't in the uploaded question logs (no session_uid): fixed.
- "No strategy calls in this race" on the grid was wrong (the game hadn't sent the race yet):
  now "no plan yet". A plan before the start would need the strategy request before lap 1: open.
- Not answerable: "About what time?" (no context).

## Strategy: committed calls, safe vs gamble (29 Oct)

From the 28-29 Oct races: the best stop lap crept a lap later every lap so the box call never came
(Melbourne, Abu Dhabi), the call was a window ("lap six to eleven"), the tyre flip-flopped
(Imola), and plans that ran to the limit weren't flagged.
- One lap, one tyre, committed: "Plan is one stop, box end of lap nine for softs." Locked within
  3 laps unless moving is 1.5 s+ better, the tyres won't make it, or you stayed out ("Change, box
  lap nine instead of seven, you stayed out."). Box calls follow the committed lap.
- Safe call vs gamble (strategy/decision.py), with uncertainty from how many laps the wear rate is
  based on: "Softs are the gamble: about three seconds quicker, enough to catch Norris, but
  sixty one percent at the flag, you'd have to look after them." / "Mediums will be tight, look
  after them. The safe call is ..." / "Softs should make it, but it's early, keep an eye on
  them."
- After a stop, at pit exit: "Copy, softs. Plan is no more stops, these need to last six laps,
  about twenty six percent at the flag."
- Push-to-talk: "what if I go a lap later / two laps earlier?" (cost vs the committed stop and
  the wear when you'd box); "are we boxing now?" follows the committed lap.
- Plan calls are NORMAL priority (the Abu Dhabi first plan expired behind coaching at LOW) and are
  said again if they expire or a flashback drops them.
- Checked by replaying Melbourne, Imola and the Abu Dhabi restart lap by lap.

## Feedback log: "log that" (29 Oct)

You choose which unanswered questions get worked on, by flagging them on the radio:
- "Log that" (also "flag that", "note that", "add that to the feedback log") right after a
  question flags it, with what the engineer said back. "Log the following question: what tyres
  is Norris on?" flags it in one go. "Log the following question" on its own: "Go ahead, what's
  the question?" and the next question is logged (not answered). Reply: "Copy, logged."
- Each flagged question keeps the race at that moment (lap, position, tyre and age, worst wear,
  gaps and names ahead/behind, the committed strategy call, safety car, the last three radio
  calls), so it's clear afterwards whether the data to answer it was there.
- data/feedback/logged.jsonl (append-only); data/feedback/feedback-log.md is rebuilt when the race
  ends (SessionEnded adds the result) and when `engineer run` stops. Questions that a newer version
  handles differently from when they were logged are marked "now goes to <intent>".
- App window, Live tab: "Logged questions" with Dismiss (drops it from the list to answer).
- Also in data/ptt/questions.jsonl (intent `log_question`, with the context), so flagged questions
  go up with the recording's upload: the loop is race -> upload -> answered in the next version.

## Moving to the installed app (2 Nov)

- `RaceEngineer.exe` first run finds a terminal install (a clone with engineer/ and data/ in the
  home folder, Documents or Desktop) and offers to copy its data (recordings, features, tracks,
  reports, push-to-talk log and models, voice, feedback log) and settings; `--import "<folder>"`
  does it by hand. Never overwrites, safe to repeat; the old folder is untouched.
- `upload.repo`: the installed app pushes recordings through that clone, so auto-upload (and the
  feedback loop) keeps working.

## From the 2-3 Nov races (Melbourne, Shanghai)

- Strategy: Melbourne said "Change, box lap ten instead of seven" on lap seven for a 2.3 s gain.
  Now a stop due this lap or next only moves for safety (`freeze_laps: 2`); replayed, lap seven
  holds and the box call comes. Plan wording is one lap at every tempo (no "anywhere from").
- Race craft, both ways: a lost place waits 8 s (`lost_hold_s`); taking it back within 20 s is
  praised ("Good fight, Hamilton couldn't make it stick" / "Straight back past Hamilton, great
  response"); places lost around your stop aren't called (ten were in Melbourne); a flashback
  no longer repeats a lost-place call or swallows one. Golden calls updated and checked.
- Questions: rejoin, true track position (got Melbourne lap 9 right: "P seven on the road ...
  about P three", P3 a lap later), tyre management tips, speed tips, "are these tyres going to
  make it", "be definitive", "repeat the strategy", tyres-vs-strategy phrasing.
- Open: the pit loss estimate looks high at Melbourne (26 s from two measured stops); the rejoin
  answer in the pit lane leans on it.

## Coaching mode and plain wording (3 Nov)

- Plain wording is the default for every corner call and answer: "Turn four, losing two tenths
  to the car ahead. Brake slightly later, then carry a bit more speed through the corner."
  `radio.detail: specific` (Settings tab, or "give me the numbers") brings back the metres/kph.
- Coaching, the fourth tempo: each lap against the fastest lap on track by another car. "One
  twenty three nine, seven tenths off Piastri. Still turn two, three tenths. Brake slightly
  later, then get on the power much earlier." Habits first, "Better into turn two, keep doing
  that" when fixed; when you're quickest, where the next car is still quicker. Lap-time calls,
  passes and attack/defend chatter are left out; strategy, box, safety car, tyres, start/finish
  and answers stay. "How far off the fastest car?" / "Where's the time?" on the radio.
- Replayed Melbourne (2 Nov): a coaching call on every clean lap but the first two (corner map
  still being learned); nine of fifteen laps had a car within a second, so fights shorten the
  call to one corner instead of silencing it.
- Next (not built): pick focus corners from the driver report's habits, an end-of-session
  coaching summary into the report, and a Coaching card on the Live tab.
- Built (3 Nov): end-of-session coaching summary ("Session done. Best lap seven tenths off
  Russell, down from one point nine seconds on your first. Turn two sorted, three tenths found.
  Still to work on: turn seven, ..." on the Melbourne replay), saved to data/coaching/ and listed
  in the driver report; Coaching card on the Live tab (gap to the fastest lap, each corner's
  loss with its trend, up to two focus corners with their advice, "fixed" badges).
- Still next: choosing focus corners from the driver report's habits.

## Pre-race tyre plan (3 Nov)

- Before the race, during car and tyre setup: "what tyres should I start on?", "strategy for
  Suzuka, fifteen laps", or a plain "what's the plan?" before lights out. "Melbourne, fifteen
  laps, same as your last race here. Start on mediums, one stop, box end of lap eight for
  hards. Starting on hards is about the same." Also said by itself when qualifying ends, and on
  the Live tab's Pre-race tyre plan card (pick a track and laps, each start tyre as a stint bar
  with box laps and wear).
- The game sends nothing in the setup menus (checked on the 2 Nov recordings: only button
  presses until seven seconds before the start), so it uses your history: the track you name or
  last drove, your last race length there or your usual race distance.
- Checked on your uploaded races: Melbourne, Austria, Shanghai: start mediums, one stop for
  hards; Imola: hards then softs; Abu Dhabi (29 laps): two stops. No history at a track (e.g.
  Suzuka) = "no tyre numbers yet", never a guess.
- Fixed on the way: questions asked in the menus were only answered once (game time stands still
  there, so the radio queue never moved on); "what if I go a lap later?" also said "Say again."
  before its answer.

## Engineer voices (3 Nov)

- Settings tab, Engineer voice: Gary (Northern, the original), Alan (Scottish), Cori (English),
  Sophie (Southern, lo-fi), Ryan (American), and Kevin, first day on work experience: "Okay don't
  panic. I'm not panicking. Box this lap, box this lap. Hards. I wrote it on my hand." Kevin's
  calls are all still there word for word; he only adds to them.
- A voice downloads the first time it's picked; picking one says a line in that voice. The
  voices couldn't be listened to from the build machine (no voice downloads there): check each
  one on the PC.

## Wording variety (3 Nov)

- Calls that come up every race now have a few wordings each, chosen so the same call doesn't
  repeat its last two wordings, and that memory carries over between races. Same facts, same
  structure, different words: "Great start, up nine places." / "Brilliant launch, up nine
  places." / "Up nine places off the line, great start." Box calls keep "Box this lap, box this
  lap" word for word; only the tyre line after it varies ("Hards." / "Hards are ready." /
  "Going onto hards.").
- Three replays of the same Austria race: 14 of 19 calls worded differently between them, every
  position, gap, name and lap time the same. Settings: `radio.variety: false` brings back the
  fixed wording.

## Pit loss from the game's pit lane timer (3 Nov)

- F1 25 sends every car's time in the pit lane and time stationary; checked on every stop of
  the 2 Nov Melbourne race (entry 5040 m, exit 44 m, ~18.3 s in the lane). The loss is now the
  time from 300 m before the pit entry to 300 m after the exit, against the same car's normal
  laps over that stretch: traffic, cold-tyre out-laps and mistakes no longer count.
- From your recordings: Melbourne 19.0 s (34 stops), Abu Dhabi 22.6 s (38), Imola 22.9 s (20;
  the old in/out-lap estimate said 27.3). Austria's lap 2 safety car stops can't be measured:
  every car stopped, so nobody was left to compare with. Stops held in the box 2 s longer than
  usual (repairs, penalties) are left out.
- Real F1 pit loss per track (OpenF1, 2023-25 Grands Prix) as a starting value for tracks with
  no timed stop yet: fetched in the background on your PC, or
  `uv run python -m engineer.strategy.real_pit_loss`. Not checked against the live service yet
  (blocked from the build machine).
- Run `uv run python -m engineer.features build` once to time the stops in your existing
  recordings; new races are added automatically.

## Session of 3 Nov evening (v0.1.4, online lobby): findings and fixes to build

Uploaded: a 5-lap Melbourne race (01:19 UTC) and one file with three one-shot qualifying laps
(Melbourne, Shanghai, Texas, ~40 min apart, same session UID). The three races between the
qualifying sessions were not uploaded (see 1); uploaded by hand on 4 Nov: one file
(20261104T014307Z, 525 MB) holds all three 20-lap races under one UID: Melbourne P2 from 19th,
Shanghai P1, Texas P3, with 47 push-to-talk questions.

Fixes (all built 4 Nov, see the end of this section):
1. Recordings lost at upload (confirmed: the races were on the PC, in one file): both uploads were pushed 22 s apart at 03:47 UTC, i.e. at the
   end of one app run that covered the whole evening, and they are exactly the first two in
   the run's upload order (race, qualifying file, then the three races). The races were most
   likely recorded but the upload was cut short when the app was closed (Ctrl+C again /
   closing the window during the 3rd upload), and auto-upload only ever sends that run's
   recordings, so they'd never go later. To build: upload each recording in the background
   when its session ends (not only at shutdown); auto-upload sends every recording not in
   uploaded.txt; an upload status + "Upload now" in the app window. To confirm on the PC:
   %LOCALAPPDATA%\RaceEngineer\data\sessions for files stamped 01:43-03:47 UTC.
2. Tyre wear depends on race length: the game wears tyres about half as fast in 5-lap races
   (Melbourne softs: you 3.3 vs 6.2 %/lap, AI 2.3 vs 4.3; your ratio to the AI unchanged;
   offline Brazil 5-lap matches). The model pools every race at a track, so 5-lap rows make
   25 % plans optimistic. Key wear by the game's m_sessionLength (5 laps = 3 "short", 25 % = 4
   "medium") or the race's share of full distance; check how practice wear scales.
3. The game sends session time = FLT_MAX after a session ends (results screen, ~38 min of
   packets): the pre-race plan queued at the end of qualifying expired, and a jump from FLT_MAX
   back to 0 looked like a restart. Ignore those times in RaceState.
4. Pre-race plan after qualifying: once per (session UID, track), not per UID (one lobby kept
   the same UID for all three); say "no tyre numbers for Texas yet" instead of silence.
5. Online lobbies: hidden human names arrive as "Player" ("Good move on Player"); call them by
   team ("the Ferrari").
6. Practice/qualifying corner calls compared with "the car ahead" ("Turn eight, losing one
   point nine seconds to the car ahead" after a one-shot lap): use your own best / the session's
   fastest lap outside races.
7. Questions that got "Say again" or the wrong answer (20-lap races, 3 Nov):
   - "Has Albon pitted?" (who_pitted with a name) -> unknown.
   - "What is the overall fastest lap? Not just mine." / "What's the fastest lap right now?"
     -> answered your own best; should be the session's fastest (who, time, your gap).
   - "How will they be at the end of the second stint?" / "What will they be by the end?"
     -> unknown (tyres_to_end).
   - "Will hards make it to the end?" -> answered about the fitted tyres; should answer for a
     new set of hards from now.
   - "If I box at lap 10 for hards, how am I looking?" / "What does the strategy look like if I
     change for hards at lap seven?" -> unknown / the generic plan; should be a what-if with
     that lap and that tyre (ask_what_if takes both).
   - "Why not hard(s)?" -> unknown; say what hards cost or why they don't work.
   - "Should I pit later for hards?" -> box_shift ignores the tyre named; its follow-up isn't
     logged.
   - "If I'm six seconds up, should I go hards?", "Can I catch the leader?" -> unknown.
   - Sectors: "How did sector one look relative to Lewis?", "What was the sector two
     differential to my best?", "How was that last couple turns?" -> unknown.
   - "You say off or up?" -> unknown (clarify the last call).
   - "How should I be thinking about this damage?" -> only the number; add whether to stop.
   - Logged notes: "re-evaluate the pit crew", "driver pin".
8. Lap times with a round ten seconds are ambiguous: 1:40.4 is said "one forty four" (heard as
   1:44). Say "one forty point four".
9. Risk when leading: Shanghai, the call was softs at lap 11 ("tight, sixty percent"); you went
   hards at lap 10 from a lead and won with them at 35 %. With a lead bigger than the gamble's
   gain, the safe call should be the call.
10. Pre-race laps: the game gave 20 laps at 35 % for Melbourne (58 x 0.35 = 20.3), so it rounds
    to nearest, not up (Shanghai 19.6 -> 20, Austria 25 % 17.75 -> 18, Melbourne 25 % 14.5 -> 15).
11. Coaching against a hidden online player says "nine tenths off Player" (same fix as 5).

Measured in these races (pit lane timer): Melbourne 16.8 s (17 stops, quicker than the 19.0 s
offline), Shanghai 23.4 s (21), Texas 18.7 s (19). 35 % races wear like 25 % ones (Melbourne
softs ~7 %/lap); only the 5-lap race is the outlier.

Built 4 Nov (all of 1-11): background uploads while not racing (every recording not sent, or
grown since; "Upload now" on the Live tab); wear never mixed between 5-lap and proper races;
FLT_MAX results-screen packets skipped; the pre-race plan after every qualifying in a lobby;
hidden players by team ("FERRARI"); practice/qualifying corners vs the fastest lap; the new
answers in item 7 (checked by re-asking every logged question in the replays: "Sector one,
gaining five tenths on Hamilton", "A new set of hards from the end of this lap, about fifty eight
percent at the flag ... they'd make it", "Fastest lap is Mercedes 2, one thirty eight six");
"one forty point four"; a tight plan gives way to the safe one when its gain changes no place;
pre-race laps rounded to the nearest. Also found: answers expired behind other top-priority
calls waiting for a straight; your answer now goes right after box/safety car calls and waits up
to 15 s.


4 Nov, answers first: anything you ask on the mic is answered before any other call. While the
button is down, and from the question until the answer is said (8 s at most), the engineer starts
nothing else; the lap time, tyre and strategy calls that queue up as you cross the line on the main
straight wait (not dropped) and follow the answer. Box and safety car calls come right after it.

4 Nov, volume slider: Settings -> Engineer voice has a volume slider. 70 % is the old loudness;
100 % is about three times the level (measured 2.9x on the radio sound) with peaks squeezed by a
soft limiter instead of clipping. It changes at once and the engineer says a line at the new level.

4 Nov, coaching from the driver report: the report now hands live coaching its findings
(data/reports/focus.json): your usual weak corners at each track and the habit behind them. Live,
those corners are coached first from lap one ("Turn six, your usual one from the report, ...
Get on the power much earlier."), the engineer says what to work on after your first lap, and
says it once when a report corner is level with the fastest lap. "What should I work on?" asks
for it. Found while building it: the report measured every session of a multi-session recording
on the first track's corners (the 29 Oct file: Imola, Abu Dhabi and Bahrain; the 3 Nov lobby:
Melbourne, Shanghai and Texas). Fixed: one entry per session. With your recordings the report's
top habits changed from "exit speed onto straights" and "braking too early" to "slow squeeze to
full throttle" (0.22 s a lap) and "winning the entry, losing the exit" (0.20 s).

4 Nov, updates: the installed app checks GitHub Releases at start-up and every 6 hours. A newer
version shows a banner in the app window ("Race Engineer 0.1.6 is out ... Download and install /
Not now", with "What's new"). On Windows one click downloads the installer (checked against
GitHub's checksum), a second ("Install and restart") closes the engineer normally, installs
silently and starts the new version. Settings -> Updates: "Download new versions automatically"
(never during a session; installing is still a click), "Check now", the version. Mac: a download
link. Not tried on Windows yet (needs a release with it, then the next one to update to).

4 Nov, gear coaching: the engineer reads your gearbox setting from the game. On manual gears
(with or without the suggested-gear hint), a corner where you're a gear lower than the other car
at the apex, at about the same speed, gets "take the apex a gear higher", and the report has a
"A gear too low at the apex" habit. On the automatic gearbox it says nothing about gears (the
gear isn't your choice). Every recording so far was driven on automatic.

4 Nov, ideal lap: not your fastest lap of the race. Your best corners of your last five clean
laps this stint, with older laps moved to "as if driven now" by how the stint's pace is fading
(a sturdy median slope, capped at +0.25 s/lap; getting quicker counts only up to fuel burn,
0.05 s/lap, since beyond that it's you learning the track). Ask "what's my ideal lap?"; the Live
tab's Coaching card shows it; coaching mode adds "four tenths off your ideal, most of it turn
nine" when you're 0.3 s+ off. The report's "Typical vs your best" and per-corner "Spread" now use
it ("vs ideal"). Checked on the 18-lap Austria race: gaps of 0.05-0.9 s on normal laps, 2+ s on
the messy ones.

## Going public (5 Nov)

- [x] MIT licence; README rewritten for players (download, SmartScreen "More info > Run anyway",
      game settings, features, privacy: what goes online), not developers first
- [x] Recordings moved off the public repo: uploads go to the private
      `ryanma05-droid/F1-engineer-recordings` (`upload.remote`), and an upload aimed at the public
      code repo is refused (`share.check_not_public`)
- [x] The 14 `recording-*` branches and `recordings` copied to the private repo (same commits,
      checked branch by branch)
- [ ] Delete them from the public repo (the cloud session isn't allowed to delete branches: done
      from the PC)
- [x] Two-factor login on the GitHub account (the updater installs whatever is released)
- The committed test recording (tests/fixtures/austria_race_5laps.zst) is an offline race:
  only AI driver names (you drove as Gasly), no push-to-talk, so it stays public.

## Diagnostic of the 3 and 7 Nov recordings (7 Nov)

Jeddah 5 laps (left after lap 1), Baku one-shot qualifying, Baku 13-lap race, Interlagos 18 laps.
Replayed with the logged questions. Fixed:
- [x] Baku lap times dropped on 5 of the first 6 laps (line 2.5 s from turn 1; 3 s wait): wait is
      now 10 s, said ~7 s after the line
- [x] Interlagos half-lap "Box box" dropped (no straight in the infield): box calls are must-say
- [x] A lap re-driven after a flashback was announced again (Brazil laps 6 and 14: 2 and 3 times)
- [x] A car diving into the pits counted as a pass ("Good move on Antonelli, P two" while P7)
- [x] "Pace dropping, they're starting to go" on the in-lap
- [x] One-shot qualifying lap time never said (session ends at once)
- [x] Race craft closing-rate divide by zero on a repeated lap
- [x] "When am I pitting?" (heard "What am I pitting?") and "Kimmy" for Kimi weren't understood
Checked and fine: the plan's end wear (Baku: "about forty five percent at the flag", it was 46 %);
speech 1.0-1.4 s per question; "Has Antonelli pitted?" works in the current code (the Baku race
ran an older copy: it also uploaded to the public repo).
Not changed: Russell re-passed 4.6 s after your move and your re-pass got a fresh "pass" call
rather than "back past him" (inside the 5 s window that treats a quick swap back as your move).

## Engineers: less repetition, more encouragement in battles (7 Nov)

- [x] Kevin: three times the lines; never the same line twice in a race; lines from recent races
      kept back; a joke on far fewer calls (one line, 45 s apart; box calls still panic); a new
      shuffle every race (it was the same seed, so the same jokes in the same order, every race).
      Brazil with Kevin: 13 of 65 calls had a line, all different.
- [x] All engineers: short wording lists go round in full before repeating; more wordings for
      lost places, fights, defending, attacking, DRS, last lap, finishes
- [x] Long battles: encouragement after 3 laps within a second of the same car, every 3 laps
      while it lasts. Real races: "Back and forth with Norris for three laps now. Great
      racecraft, stay patient." (Baku); "Brilliant battle with Russell, three laps and counting.
      Keep your head, pick your moment." (Interlagos)

## Diagnostic of the 9-10 Nov online lobby (10 Nov)

Suzuka, Jeddah and Bahrain races (P1 in all three), qualifying and practice; 36 questions.
Fixed:
- [x] Jeddah: 26 calls dropped; "Last lap", "Got it back from Russell", defend calls lost behind
      long corner tips. Urgent race craft now ranks above corner/tyre calls; "Last lap" is
      must-say
- [x] "Should I fix the wing?" (damage on lap 1) got "give me a clean lap" four times: now
      compared with the quickest cars ("on their pace, it isn't costing much, I'd leave it")
- [x] "No plan yet, give me a couple of laps" asked on the in-lap after the stop
- [x] New answers: teammate, hype, pit stop time, "his last lap relative to mine"
- [x] Phrasings: "pit strat", "what do you think about hards", "how much slower would hards be",
      "run a strategy sim for hards", "this tyre for the rest of the race"
Logged as ideas (not built): coaching tips that depend on the gap ahead/behind; a "push now"
call when you need to make up time (undercut, closing a gap).
Learned: soft wear at Jeddah was overestimated (7 laps: 61 % predicted, 51 % at the flag), a
first race there; the plan at Bahrain switched hards -> mediums (tight, 59 %) when the softs
lasted better; you chose hards (box-now answer gave the cost) and won.

## Update install snag (11 Nov)

A friend's update (from an older version) hit a snag; no error text yet. Checked: the release
check and download work (v0.1.6 found, 155 MB, SHA-256 matches, 6 s); old settings files still
load (only additions since 0.1.3); no library changes since 0.1.3. Found and fixed:
- [x] "Install and restart" started the daily driver report in its own process at shutdown,
      right before the silent installer: it holds the app's files for minutes. Skipped now.
- [x] Fixed 4 s wait -> waits for the app's process to exit; installer closes any other copy
      (/CLOSEAPPLICATIONS, CloseApplications=force)
- [x] Installs write data/updates/install.log; a failed one shows in Settings > Updates
- [ ] Not tried on Windows yet; v0.1.6's own updater still has the old behaviour (the
      installer-side close applies from the next setup file)
- [ ] Need from the friend: what they saw, and %LOCALAPPDATA%\RaceEngineer\logs\race-engineer.log

## Push calls (12 Nov)

- [x] Undercut: "Push in, it's the undercut on Norris" on the box call when the car just ahead
      (within 3 s) hasn't stopped; "Push now, big out lap" while they're still out; "Undercut
      worked!" / "Norris stayed in front, ..." when they come out
- [x] Undercut on you: "... watch the undercut. Push now, big laps on these."
- [x] Closing in on a battle (ahead or behind, 1-2 s and closing 0.2 s+ a lap): "Push now!"
      hype; once per car per 4 laps; not in Quiet
- Last night's races, replayed: one battle push (Jeddah, Russell), one undercut cover (Bahrain),
  one push-in (Brazil, a stop you then skipped). Gaps at the line jump around (1.8 s one lap,
  0.45 s the next), so pushes stay rare; widen `push_gap_s` if you want more of them.
- [ ] Not tried in a live race yet

## GP engineer (12 Nov)

- [x] Studied 971 broadcast messages on Verstappen's car (2021, 2023-25 races and sprints;
      formuladream.app; 2022 not in the archive) and their race situations (OpenF1, sample of
      286): docs/gp-radio-study.md
- [x] GP in Settings: Gary's voice, that manner on top; "Your name" setting for what GP calls you
- [x] Checked on the 7 Nov Interlagos race (68 calls)
- [ ] Not heard live yet; tune by ear (how often the name and the add-ons come)

## Weather, penalties, rival information (12 Nov)

From the real radio study's most common unprompted topics.
- [x] Weather: "Rain expected in about ten minutes, around lap twelve. Sixty percent chance.",
      a five-minute reminder, rain starting, rain easing, forecast cleared; "Will it rain?"
      answers with the forecast
- [x] Penalties: your track-limit warnings counted ("that's two"); a time penalty on a car within
      two places and what it means for you; repeated in the countdown; "Any penalties?" /
      "How many warnings?"
- [x] Rivals: the car ahead/behind struggling or about to pit; "How's Norris looking?" (tyres,
      age, wear, stops, pace vs you, penalty)
- Replays of the 7 and 9 Nov races: one or two rival calls a race; your real track-limit
  warnings counted. No rain or rival time penalty in any recording yet: those are tested on
  simulated data only.
- [ ] Not heard live yet; wet-weather strategy (when to box for inters) is still open

## Strategy as a dialogue; console-only mode (13 Nov)

- [x] Preferences steer the plan: finish on a tyre, max wear, number of stops, avoid a tyre, go
      long / box early / box on lap N; read back with the time against our plan; pushback when
      risky ("past our limit ... puncture risk, but it's your call"); "back to your plan"
- [x] Offers ("Want it?") when the gamble is 3 s+ quicker; yes / no
- [x] "Why?", "Are you sure?"
- [x] Pace: "How much do I need to save?", "Can I push?", manage calls, "I'm managing / pushing"
      measured two laps later
- [x] Console only: options with pros and cons, push / manage calls, gaps; the controller choice
      "Console only (no push to talk)"
- Real Bahrain race (9 Nov): a "finish on softs" dialogue re-planned and the box calls followed;
  console-only mode added about six calls over 20 laps.
- [ ] Not tried live yet


## Tyre history: stints split per race (12 Nov)

- [x] A lobby's several races under one session UID were summarised as one set of stints, filed
      under the first track (Suzuka and Jeddah together: Suzuka mediums looked like 3.2 %/lap,
      really 4.3; Jeddah had no history). Stints now split by track, session type and restart.
- [x] Existing tables are fixed by themselves on the next start (re-summarised from the stored
      lap rows, once; no replays)

## Lap delta overlay (14 Nov)

- [x] Step 1, the calculation: live delta to your previous (or best) fair lap at every point of
      the lap, plus a split per corner. On the real Austria race the delta at the line matches
      the real lap difference within 5-17 ms; flashbacks, pit laps and the flag handled
- [x] Step 2: the overlay window: always on top, see-through, click-through, never takes focus;
      move mode (drag, scroll to resize, double-click to lock, spot saved); hidden with
      nothing to show. `python -m engineer.overlay --demo` / `--replay <file>` to try it.
      Rendered and replayed here under a virtual display; not yet seen on Windows
- [x] Step 3 (part): Settings switch and "Move overlay" / "Lock here" button, live; the overlay
      restyled to the app's F1 look (red edge and stripes, header strip, timing-chip corner
      split, Bahnschrift). The demo works on Windows (14 Nov)
- [ ] Still to do: reference choice in Settings, full-screen page for console players
- [ ] Step 4: try it in the game (borderless windowed) and pick the screen area

## Miami race review (11 Nov)

- [x] Reviewed the 14-lap Miami race: 38 calls, 3 dropped; 8 of 13 questions got "Say again"
- [x] Undercut on Antonelli now tracked through the pit-lane shuffle ("Push now, big out lap",
      then whether it worked); before, nothing was said
- [x] New questions understood: undercut, "will <driver> pit?", "how am I looking to <driver>",
      battle / progress with a driver, "chatty mode" / "quiet mode"
- [x] Built (12 Nov): a call when the stops shake out, the gap you need after a time penalty,
      gap updates every lap in a battle (battles now within 2 s), chatty adds cars around you
      pitting and battle updates a second further out
- [x] Moments that matter said with energy by every engineer: faster, livelier, louder voice
      settings and "!" wording on passes, fights, lost places (with encouragement), battles,
      push laps; GP lifts too. Miami replay: 38 -> 46 calls in normal
- [ ] Hear the excited voice on Windows and tune `tts.EXCITED` if it's too much or too little

## Spa race diagnostic (13 Nov)

- [x] Taking the lead (Leclerc) and the Hamilton pass were dropped waiting for a straight: pass
      calls now always come out
- [x] "Undercut on Antonelli" when he pitted with you, then "stayed in front, fresher tyres"
- [x] Questions: "Confirm.", "At what?", "Confirm percentages.", "Shorten that, log it.", a
      misheard question naming Leclerc; "track position" listed eleven names (now two + count)
- [ ] Live, the strategy had no plan on laps 3-4 ("No plan yet") and called mediums for lap 5
      with no warning; on replay the plan comes on lap 2 (hards, mediums as the gamble).
      Solves take 0.06 s with 2,000 laps of history here, so it's the PC: need the log lines
      around "strategy" from that race
