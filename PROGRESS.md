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
