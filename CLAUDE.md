# CLAUDE.md — race-engineer

Personal AI race engineer for EA SPORTS F1 25 (Windows, Python 3.12, asyncio). Listens to UDP
telemetry, keeps a live race model, and (Phase 1+) speaks like a race engineer. The owner is new to
this kind of project: explain decisions briefly in plain language, keep code readable over clever.

Read `docs/architecture.md` (full design) and `PROGRESS.md` (what's done / open questions) first.

## Non-negotiable rules

- **Packet layouts come only from `docs/spec/f1_25_udp_spec.md` / the PDF in `docs/spec/`.** Never from
  memory or older game years. Every dtype's size is asserted against the spec at import.
- **Record everything, replay anything.** Live and replay both go through `Pipeline.feed(raw, t_ns)`.
  Never add a code path that only works live.
- **Only the parser and `RaceState.apply()` write state.** Nothing else decodes bytes.
- **Never block the asyncio loop.** Heavy work (TTS, strategy sims, speech recognition) goes in worker
  processes.
- Wheel arrays: **RL, RR, FL, FR**. Unknown = NaN/None, never 0 (restricted telemetry, inactive cars).
- Don't build later-phase features early, but don't design them out.

## Phase plan

0 foundation (accepted) · 1 radio director + Piper TTS + lap delta (done; voice works on Windows) ·
2 features/feature tables (done) · 3 tyre coaching (done; checked on an 18-lap race) ·
4 traces + corner coaching (built) · 5 strategy sim (built) · 6 push-to-talk (built).

## Commands

```
uv sync
uv run pytest                    # everything (~4 min); run before pushing, and the release build does
uv run pytest -m "not slow"      # quick check while working (~1.5 min): skips 20 whole-race replays
uv run python -m engineer run
uv run python -m engineer.ingest.replay <file.zst> --speed max
uv run python -m engineer.bench <file.zst>
uv run python -m engineer.radio.setup_voice --test   # download + hear the Piper voice
uv run python -m engineer.features build             # recordings -> data/features/*.parquet
uv run python -m engineer.features tyres             # your wear per lap by track/compound
uv run python -m engineer.features corners           # your time per corner vs the AI
uv run python -m engineer.features strategy austria     # what the strategy model has learned
uv run python -m engineer.features query "select ..."   # SQL over laps/stints/traces/corners
uv run python -m engineer.voice.setup                # download the speech model, test LB + mic
uv run python -m engineer.ingest.replay <file.zst> --questions   # replay with the logged questions
uv run python -m engineer.ingest.share --upload      # push the newest recording to its own branch
uv run python -m engineer.ingest.share --pending     # push every recording not uploaded yet
uv run python -m engineer.report                     # driver report PDF (data/reports/), opens it
uv run python -m engineer.overlay --demo             # the lap delta overlay, move mode (no game)
uv run python -m engineer.overlay --replay <file.zst> --locked   # ... fed by a recording
```

## Radio rules (Phase 1)

- Analyzers never speak directly: they submit a `RadioCall` to the `RadioDirector`.
- The director runs on game time and estimates speech length from text, so replays produce the
  same calls. Keep it that way: no wall-clock time or audio feedback in director decisions.
  (Live-only exceptions: `idle_after_s` in menus, and the wall-clock cap on the answer hold.)
- Your answer goes first, before every other call whatever its priority (`_order`). While you're
  on the mic (`listen()` on button down, cli) and from the question until its answer is said
  (`listen(ANSWER_HOLD_S)` in the answerer, 8 s at most) nothing else is started; calls already
  queued wait with their expiry paused and restarted after the answer. A short press or nothing
  heard calls `stop_listening()`. Why: the main straight is where you ask, and it's where the lap
  is crossed and all the lap calls queue up (4 Nov).
- `must_say` calls (both box calls) don't expire after `max_wait_s`: they stop waiting for a
  straight and go at the next gap in speech, dropped only after `MUST_SAY_CAP` x the wait
  (Interlagos, 7 Nov: the half-lap "Box box" waited 10 s in the infield and was dropped). At
  SessionEnded HIGH calls AND the lap time (`END_FLUSH_TOPICS`) are said (one-shot qualifying
  ends the moment the lap is done).
- Ranking within a priority (`_RANK`): box/safety car, then race craft only worth saying now
  (countdown, pass/fight, defend, attack, penalty, damage), then battle encouragement, then the
  rest (corner tips, tyre status). "Last lap" is must_say. Jeddah, 9 Nov: a 13 s corner tip
  queued first won the gap after the line; "Last lap" and a fight call expired behind it.
- Lap times wait up to `radio.lap_delta_max_wait_s` 10 s for a straight (was 3: Baku's line is
  2.5 s from turn 1, so the lap time was dropped on most laps; now said ~7 s after the line).
  A lap re-driven after a flashback to just before the line isn't announced again when within
  `SAME_LAP_MS` (100) of the lap time already said (Brazil, 7 Nov: 12 flashbacks, mostly a spin
  at turn 1 rewound to 0.4 s before the line).
- Wiring lives in `engineer/app.py:build_engine` (shared by live and replay). Subscription order
  matters: LapHistory before analyzers.
- The golden test also checks `tests/fixtures/*.calls.txt`; update it deliberately (and check each
  call by hand) when phrasing changes.

## Features and tyres (Phases 2-3)

- `LapFeatureBuilder` is the one place lap features are computed, live and offline. Offline tables
  are rebuilt from the recordings (`features build`); never compute features a second way.
- `build_engine(cfg, out, priors=None)` loads tyre history from `data/features/stints.parquet`;
  tests pass `priors=WearPriors()` so they don't depend on the local tables.
- Tyre settings live in `config.yaml` `tyres:` (limit 60%, laps left from 25%, status every 3 laps).

## Corners (Phase 4)

- Traces come from `TraceRecorder` (wired in `build_engine`); compare laps only through
  `corner_deltas()` so live calls, tables and push-to-talk agree.
- Habits first: a corner losing time on `corners.focus_after_laps` (2) coached laps in a row is
  called ahead of bigger one-off losses, at NORMAL priority, as "third lap running", naming the
  cause that recurs over the streak (`_habit_reason`), not the biggest one this lap.
- "Your pace" (the reference when no car is near): your fastest clean lap on the same compound
  with tyre wear within `ref_wear_window_pct` (8 %), so tyre wear isn't blamed on a corner.
- Closing the loop: a habit corner that stops losing (your own corner time 0.1 s+ quicker than
  when called) gets "Turn four better" once. After 3 calls in a streak, every other lap unless
  worse. Within `battle_gap_s` (1 s) of a car, only calls vs the car ahead are said.
- Coaching mode (`CornerAnalyzer._coach`): each clean lap vs the fastest clean lap of any OTHER
  car this session (`bench`, replaced as it's beaten; traces can land after LapCompleted, so
  candidates wait in `_bench_todo`). One call, topic "coaching" (no cooldown): lap time, gap,
  up to two corners (habits first) with plain advice, "Better into ..." when a habit is fixed,
  "fastest lap on track, X still quicker through ..." when you're quickest. In a fight: one
  corner, not silence.
- `engineer/analyzers/coaching.py`: every clean lap vs the bench is logged in
  `CornerAnalyzer.coach_laps` in any tempo (the Live tab's Coaching card, `card()`). The summary
  (`summarize`: fixed = lost on 2+ of the first half, not on the last 2; open = still losing over
  the last 3; gaps against the fastest lap at the END) is said in coaching mode on your last race
  lap (wired after race craft, so after the finish call) or at SessionEnded
  (`RadioDirector.release_now`), and saved to data/coaching/ by live play only (`save_dir`, set in
  cli). The driver report lists the latest ones (section 07).
- Coaching from the driver report (`report/focus.py`): each report build writes
  data/reports/focus.json (per track up to 3 corners losing 0.05 s+ to the quickest AI with 4+
  laps, each with its theme and the live causes that theme shows as; your top 2 habits with a
  short radio line). `build_engine` loads it (`corners.report_focus`; the suite sets
  RACE_ENGINEER_REPORT_FOCUS=0). Live: a report corner losing time is called right after live
  habits from the first lap, NORMAL priority, "one of your report corners" the first time a
  session, with the theme's cause first (`_report_reason`); coaching mode orders them after
  habits. Once a session after your first coached lap: "From your report, the time's in turn six
  and turn two. Work on the throttle: ..." (topic coaching_focus, also the "what should I work
  on?" answer); once per corner when it's level with the fastest lap on track. Live tab Coaching
  card: "From your report" chips.
- Gears: `CornerStats.apex_gear` (gear at the slowest point); `CornerDelta.reasons()` adds
  "gear_low" (gears below the other car at the apex, only with your apex speed within
  `GEAR_SPEED_KPH` 5 kph of theirs, else the gear just follows the speed) when `gears` is on:
  `corner_deltas(..., gears=state.session.manual_gears)` (Session packet `m_gearboxAssist` 1/2;
  3 = automatic, unknown = automatic: no gear advice). Plain "take the apex a gear higher",
  specific "a gear lower at the apex". Report: "gear" in DELTA_FIELDS, theme `low_gear`, your
  gears dropped from the analysis on automatic (ANALYSIS_VERSION 3). Your recordings so far
  (Austria, Shanghai, Imola) were all on automatic.
- Ideal lap (`tools/ideal.py`, shared by live and the report): NOT the fastest lap of the race
  (fresh tyres make later laps look slow; 4 Nov). Your last 5 clean laps in the current stint
  (min 3; a pit stop starts again), split into corner segments (line -> first corner entry, then
  each corner's entry -> the next one's; they add up to the lap time), older laps moved to "as if
  driven now" by the stint's pace trend (Theil-Sen median slope, capped -0.05..+0.25 s/lap: more
  than fuel burn on the quick side is learning, not the car), spread over segments by share; the
  ideal is the best corrected time per segment. Live: `CornerAnalyzer.ideal` after each clean
  lap, Live tab card, "what's my ideal lap?" (intent ideal_lap, before best_lap), and coaching
  mode adds "Four tenths off your ideal, most of it turn nine." from 0.3 s (not repeating a corner
  the call named). Report (ANALYSIS_VERSION 4, `my_segments` per session): "Typical vs your ideal
  lap" tile and the corner table's "vs ideal" column (`insights.ideal_gaps`) replace the old
  median-minus-session-best spread.
- `data/tracks/<track>.json` is committed and hand-editable (official turn numbers). Without a
  file the map is learned from the first 2 clean laps and saved. The golden test uses
  `data/tracks/austria.json`.

## Race craft

- `engineer/analyzers/racecraft.py` (settings `racecraft:`): start summary, passes (from the
  game's OVTK event, only once they've stuck 4 s, grouped when close together, wording rotates
  in a fixed order), lost places (held `lost_hold_s` 8 s; not around your own stop; counted as
  said only once on the radio, so a flashback doesn't repeat or swallow one), fights (a car
  that passed you retaken within `fight_window_s`: "Good fight, ... couldn't make it stick" /
  "Straight back past ..."; a re-pass within 5 s of YOUR move is your overtake, not a fight),
  defending / attacking (only if you'd realistically catch them), countdown,
  finish, fastest lap (when taken from someone else), your penalties (IDs from the PDF spec),
  safety car in, damage after contact.
- Long battles (`_battle`): the same car within `battle_gap_s` (2 s since 12 Nov, was 1) ahead or behind at the line
  `battle_laps` (3) laps running -> encouragement (topic racecraft_battle, LOW, so not in quiet
  mode), again every `battle_repeat_laps` (3), at most 4 per car; "back and forth" wording after
  2+ swaps in this battle; not within 15 s of a defend/attack/pass call (next lap instead).
  Counted as said only once on the radio: a flashback or an expired wait puts it back (Brazil,
  7 Nov: the Russell battle call was cleared by a flashback and never came back).
- Push calls (12 Nov, "a mode push for undercuts and when I'm close to a battle"):
  `_push` (racecraft, topic racecraft_push, NORMAL, rank 2, left out in Quiet via
  `TERSE_DROPS`): the car ahead or behind inside `push_gap_s` (3 s) but outside DRS range
  (`drs_gap_s` 1 s), gap down `push_closing_s` (0.2 s) on the last lap -> "Push now! Norris is
  ... and you're catching" / "Push now, Russell is ... back and coming". Once per car and side
  per PUSH_REPEAT_LAPS (4), not within 15 s of another race craft call. Undercut (strategy,
  topic undercut_push, kept in Quiet): your stop with the car just ahead within
  `undercut_push_gap_s` (3 s) and no more stops than you (`_undercut_target`) -> the box call
  ends "Push in, it's the undercut on Norris"; at your pit exit, if they're still out, "Push
  now, big out lap..."; at their pit exit, "Undercut worked!" or "Norris stayed in front ...".
  A box call you don't follow forgets the target (worked out again at your real pit entry).
  The target is the nearest car ahead ON TRACK (cars in the pit lane skipped, gap from the
  leader gaps when not adjacent), remembered on every Lap Data packet while you're on track
  (`undercut_seen`); from the pit lane `_undercut_target` uses that if it's under
  UNDERCUT_SEEN_S (15 s) old (Miami, 11 Nov: at your pit entry Russell, out of his stop, was
  "just ahead" at a gap of 0, so the undercut on Antonelli 2.1 s ahead was never tracked).
  The car behind pitting (undercut on you) now adds "Push now, big laps on these."
- Moments that matter (12 Nov, "more energetic calls ... big battles, push laps, nice moves,
  even losing a spot, some encouragement helps"; every engineer):
  - Delivery: `RadioCall.excited` (None = topic in `director.EXCITED_TOPICS`: passes / lost
    places / fights, battle, push, gap, attack, defend, undercut push, cycle, penalty gap) ->
    `RadioMessage.excited` -> `AudioProcess.say(text, origin, excited)` -> Piper with
    `tts.EXCITED` (length_scale x0.9, noise_scale 0.85, noise_w 1.0, gain x1.15 under
    soft_limit). The director's timing ignores it (still from the text, so replays agree).
  - Wording: `phrases._shout` ("!") on passes ("Great move on Norris! P three."), fights,
    lost places with stronger encouragement ("Stay with Leclerc, you'll get another go!"),
    battles, defend/attack/DRS, start, last lap, undercut missed. GP's pass / battle add-ons lift
    ("Get in there!", "Mega, mate!", 50 % of good passes); Kevin already covers racecraft_*.
  - Battle updates (`_gap_update`, topic racecraft_gap, LOW, rank 1): at the end of YOUR
    sector one (not the line, where lap calls queue and the first try lost most to the wait),
    the nearest car within `battle_gap_s` (+ `chatty_extra_gap_s` 1 s in chatty), its gap and
    the change since the last update ("Antonelli behind, eight tenths, you're holding them.
    Great job, keep it up!"; change under 0.1 s = steady). Not when another race craft call
    was said in the last 15 s or is queued (`_racecraft_busy`), nor the lap after a battle call.
  - Stops shaken out (`_cycle`, racecraft_cycle, NORMAL, rank 2): at your line once every
    running car (result status not 4-7) has stopped as often as you and none is in the pit
    lane, dry only, once per stop count: "Stops all done for everyone. P six, up two places on
    the cycle. ...!" (vs your position at the line before your pit entry).
  - Your time penalty (`_penalty_lap`, racecraft_penalty_gap, NORMAL, rank 2): appended to the
    PENA time-penalty call, then every lap while `penalties_s` > 0 (not within 30 s of the
    penalty call): the nearest car behind within penalty + 1 s, the gap, how many are inside
    it ("Push, push!" / "just outside, keep it there!"); "Nobody within your three seconds
    behind" once when clear.
  - Chatty: a car within two places going into the pits (`_rival_pit`, racecraft_rival_pit,
    LOW; the car right behind is skipped while you still have a stop: the undercut call).
  Miami replay: normal 38 -> 46 calls said.
- Weather, rivals' penalties, rival information (12 Nov; the real radio's most common
  unprompted topics, see docs/gp-radio-study.md). Shared logic in `tools/rivals.py` (calls and
  answers agree): `gap_between` (intervals summed), `next_weather_change` (the Session packet's
  forecast, now in `state.session.forecast` as (session type, minutes, weather, rain %): rain
  when weather >= 3 or rain % >= `weather.rain_pct` 40 within `horizon_min` 30; "dry" when wet
  now), `minutes_to_lap`, `rival_report` (position, gap, compound, tyre age, stops, worst wear
  (None for online players), pace vs you over common clean laps, lap-time trend this stint,
  penalty), `penalised_near`. `analyzers/weather.py` (on Session packets, topic weather,
  NORMAL): rain coming, again at <= 5 min, rain starting, rain easing, "forecast's cleared" only
  once the chance is CLEAR_MARGIN (15) under the threshold. `analyzers/rivals.py` (your lap,
  races, lap 3+, no SC, one call a lap): a new time penalty on a car within 2 places (topic
  rival_penalty, NORMAL, rank 2, once per car and total, with what it means: "you're inside
  it" / "even if they get by, stay within five seconds"); the car just ahead/behind within 5 s
  slowing 0.3 s+ a lap ("struggling" / "dropping off", once per car per stint); or its worst
  tyre within 15 of your limit ("expect ... to pit soon"); both topic rivals, LOW. Racecraft:
  your track-limit warnings counted from PENA (infringements 7, 27-29: "that's two", from three
  "careful, the next one could be a penalty"); the countdown adds a penalty on the car just
  ahead/behind. State also has `total_warnings`, `corner_cutting_warnings` per car. Answers:
  weather adds the forecast; "How's Norris looking?" / "what tyres is the car ahead on" (rival);
  "any penalties?" / "how many warnings?" (penalties).
  Miami, 11 Nov (8 of 13 questions got "Say again"): "is this an undercut opportunity?" /
  "undercut Kimi?" (undercut: the car ahead on track, gap, tyres, pace vs you, close enough or
  not (`undercut_push_gap_s`), where you'd come out, the plan's lap; from the pit lane "big out
  lap"); "do you think Ocon will pit?", "how am I looking to Kimi?", "battle with / progress to
  / what about <driver>" (rival; asked about pitting and no stops yet in the dry adds "They still
  have to stop."); "chatty mode" / "quiet mode" by name; "will I need to pit again?" (plan).
  Spa, 13 Nov: "Confirm." (intent confirm: "Copy, box this lap, hards." on the box lap, else
  "Copy."); "At what?" / "Confirm percentages." = repeat; "Shorten that, log it." logs the last
  question with the words before "log it" as a note; an "unknown" question naming a driver is
  answered as rival ("Can I get handled? Or Leclerc?"); true_position names two cars then "and
  N others" (`phrases._few`; it read out eleven names).
- Passes, fights and lost places are `must_say` (Spa, 13 Nov: taking the lead from Leclerc in
  the middle sector expired waiting for a straight): after 8 s they go at the next gap.
- Undercut: a target that boxes with you (in the pit lane at your pit entry, or pitting while
  you're in the pits) is dropped (`StrategyAnalyzer._covered`; Spa: Antonelli pitted 0.5 s
  ahead and you heard "Antonelli stayed in front, fresher tyres").
- Passes: a car in the pit lane when the pass settles (4 s on) wasn't passed on track, so no call
  (Baku, 7 Nov: OVTK came on the pit entry road, before pit status changed; Antonelli's stop
  became "Good move, P two"). The "pace dropping" call is skipped on an in-lap.
- Game events reach analyzers as `ev.GameEvent` (`detectors.PASSED_THROUGH`).
- The director says waiting HIGH calls on SessionEnded: the game stops sending 0.1 s after your
  last lap, so the finish call would otherwise never be released.

- One-time welcome at start-up (`engineer/intro.py`, `cli.welcome`, remembered in
  data/once.json), not on the grid: a race session starts only ~6 s before lights out, and the
  game's session time stays 0.0 until lights out (real recordings).

## Lap delta overlay (14 Nov: the calculation, the window, the Settings switch)

- `state/live_delta.py` `LiveDelta` (wired in `build_engine` after traces, features and
  corners; `Engine.live_delta`): on every Lap Data packet, your current lap time minus the
  reference lap's time at the same distance (`current`, a `DeltaReading`; `lap_end` = each
  lap's furthest reading). + = slower. A reader of RaceState like TraceRecorder, game time
  only, so replays give the same numbers; ~30 us a packet.
- Reference (`overlay.reference`, `set_reference`): "previous" = your latest fair lap, "best" =
  your fastest fair lap this session; fair = usable trace and not invalid / in lap / out lap /
  standing start / safety car. Chosen when the lap starts and kept all lap. `Reference` pins the
  trace at (0 m, 0 s) and (the line, the lap time): the trace's clamped ends put the delta at
  the line 20-40 ms out.
- No reading: no fair lap yet, in the pit lane and the rest of that lap, once you've finished
  (result status 3+, or the distance going from the last 200 m back to the start on the same
  lap: at the flag the lap number doesn't change and the status stays "active" a few packets).
  A flashback (distance back 30 m+) re-measures corners after the rewound point.
- Corner splits (with `corners.track_map`): at each corner's exit the running delta and what the
  corner cost (delta at exit - at entry), held in `current.corner` for 3 s.
- Checked in the golden test: delta at the line within 25 ms of the real lap difference (5-17 ms
  on the Austria race, the gap between the last sample and the line).
- The window (`engineer/overlay/`): view.py (pure: `message` = the reading as a small dict,
  `view` = text "+0.23", colour red/green/white within 0.02, bar -1..1 of `overlay.bar_s`,
  "vs L4", "T3 +0.14"; `place` = the saved spot if still on a monitor, else top centre of the
  main one), window.py (`overlay_main`, own process: tkinter, borderless, topmost, `-alpha`
  `overlay.opacity`; on Windows click-through + no focus + no taskbar via WS_EX_TRANSPARENT /
  NOACTIVATE / TOOLWINDOW, DPI aware, topmost re-asserted every 2 s; hidden (alpha 0) with
  nothing to show; the number redrawn at most 10 Hz, the bar ~30 Hz; move mode = yellow outline,
  sample reading, drag / scroll to resize / double-click to lock -> ("placed", x, y, scale)),
  process.py (`OverlayProcess`: `send` never blocks, ~15 Hz and at once when the reading
  appears/disappears or a corner split comes; `OverlayFeed` after Lap Data packets;
  `save_placed` -> config.local.yaml). Wall-clock only for display, never for the race model.
- Look (14 Nov, "use F1 themes"): matches the app window: near-black panel, F1 red edge and the
  logo's diagonal stripes, a header strip "DELTA  VS L4" with the corner split as a timing-chip
  (white "T3" box + coloured gain; the "VS L4" label drops out when they'd touch), Bahnschrift
  (Windows 10/11's DIN face) picked from the installed fonts, bar ticks at half scale.
- Live (`overlay/control.py` `OverlayControl`, made in cli, passed to AppWindow and the idle
  loop): `set_enabled` starts/stops the window process off the loop (`asyncio.to_thread`), states
  off / starting / on / moving / failed; `move()` unlocks (turning it on first if off); `lock()`
  = {"locked": True, "save": True} -> the window reports its spot like a double-click
  (`_placed` updates cfg and config.local.yaml). The feed is wired once and does nothing while
  off. Windows only (`available`). Settings tab card "Lap delta overlay": switch
  (`/api/settings {"overlay": bool}`, saved), "Move overlay" / "Lock here" (`/api/overlay
  {"action": "move"|"lock"}`), status line; snapshot["overlay"]. The idle loop blanks it in
  menus and notices a window that closed. Needs the game in borderless windowed. Packaging keeps
  tkinter on Windows; `--check` opens a hidden Tk window. Tested: view/feed/control/API always
  (a fake window process), the real window where Tk has a display (xvfb-run here; the container
  needed python3.12-tk).
- Next: the reference choice (previous/best) in Settings, a full-screen page for console
  players, and trying it in the game.

## Driver report

- `engineer/report/` (`python -m engineer.report`): analysis.py (per-recording corner runs vs
  the five quickest AI, cached in data/reports/cache/, bump ANALYSIS_VERSION when a measurement
  changes), insights.py (combining sessions with recency weights; habits are rules over the
  numbers, nothing per track), render.py (one self-contained HTML page, Letter-size print),
  pdf.py (Edge/Chrome headless), daily.py (the command, and the once-a-day background build when
  `engineer run` stops; the installed app runs `RaceEngineer.exe --daily-report`).
- Corner runs use `corner_stats()` for the shared numbers, so the report agrees with live
  coaching.
- One entry per session (ANALYSIS_VERSION 2): a recording holding several sessions (online lobby,
  a restart, a weekend) is cached as {"parts": [...]}; read entries with `entries_of()`. Before
  this, every lap of a file was measured on its first track's corners (4 Nov: Abu Dhabi laps on
  the Imola map made Imola turn four "1.2 s").

## App window

- `engineer/ui/`: server.py (tiny HTTP server on 127.0.0.1, on the engineer's asyncio loop;
  POSTs need the `X-Engineer: 1` header so other web pages can't change settings), app.py
  (`AppWindow`: live state from RaceState/listener/recorder, radio calls and questions from the
  bus; settings applied live and saved with `config.save_local`), page.py (the page as a string,
  so the installed app needs no data files). Opened as an Edge/Chrome `--app` window by
  `cli._run`; `--no-ui` turns it off. It only reads the race, never writes state.
- First open asks the controller (Xbox LB / PlayStation L1 / none) and the tempo
  (`ui.setup_done`); the one-time welcome waits for it so it names the right button.
- Tempo = `radio.verbosity` (terse shown as "Quiet", normal, chatty, coaching). `Engine.set_tempo`
  changes it live (also by voice: "talk more", "keep it short", "back to normal", "coaching
  mode"); terse also drops LOW-priority calls except `TERSE_KEEPS`; coaching runs the analyzers
  at normal, drops `COACHING_DROPS` (lap delta, passes, attack/defend) and turns on the corner
  analyzer's coaching call. "Be quiet" is separate: radio silence.
- `radio.detail`: "plain" (default; "brake slightly later, then carry a bit more speed through the
  corner", phrases.advice: size words from the measurement vs REASON_THRESHOLDS, causes in
  driving order, late throttle covers a slow exit) or "specific" (the metres/kph).
  `Engine.set_detail`, Settings tab, or "give me the numbers" / "keep it simple".
- PlayStation push-to-talk: winmm joystick API (`PlayStationButton`, L1 = button 5), plus the
  Xbox LB through XInput for Steam Input. Not yet tried on a real DualSense.

## Strategy (Phase 5)

- Nothing about tyre behaviour is hard-coded: wear rate, deg and compound offsets are learned
  from lap rows (history + this session) and the game's Tyre Sets figures, blended by evidence,
  so your own laps take over as recordings pile up. `config.yaml` `strategy:` values are only
  fallbacks until data exists. Keep it that way.
- Strategy sims never run on the asyncio loop live: `cli.py` passes a `WorkerRunner`;
  `build_engine` defaults to `InlineRunner` so replays and tests stay deterministic.
- AI lap times are not used for deg (they barely slow with wear on real data); AI wear rates are
  used, scaled by your wear ratio vs the AI.
- The call (strategy/decision.py): every candidate (each stop count, each next tyre, and careful
  versions planned at limit - margin and limit - 2x margin, priced at the real limit) is checked
  stint by stint; safe = every stint ends `margin_pct` under the limit allowing for wear-rate
  uncertainty (`rate_sd_one_lap` / sqrt(laps of evidence)). The call is the fastest safe plan; a
  quicker unsafe one is named as "the gamble", or becomes the call (labelled tight) when it's
  `gamble_take_s` quicker. Wording: one lap and one tyre ("box end of lap nine for softs").
- The call is committed: box calls follow `StrategyAnalyzer.announced`, not the latest best. It
  only changes for a gain of `window_s` (two laps running) or `change_gain_s` within
  `commit_laps`, never for time once the stop is this lap or next (`freeze_laps`); at once if
  you stayed out past it, the tyres won't make it, or it's impossible. Plan wording is one lap,
  never a range, at every tempo.
  After a stop: "Copy, softs." + the rest of the race, at pit exit. Plan calls are NORMAL
  priority and are re-announced if they expire or a flashback drops them unsaid.
- Pre-race plan (strategy/prerace.py): the game sends nothing in the race setup menus (real
  recordings: only BUTN events until ~7 s before lights out; UID-0 menu packets carry no
  session), so the track is the one said / picked in the window / the last session's, and the
  laps are the ones said, else your last race here, else your usual distance (laps / full race
  laps, snapped to 25/35/50/100 %, rounded to the nearest lap like the game: Melbourne 35 % = 20).
  5-lap races never count ("your last race here" or the usual distance). History laps are the most any car
  completed, so snapping also fixes races left early. Each dry start is solved from lap 0 in the
  worker (`StrategyRequest.starts`); the pick is the quickest safe start. No history at a track =
  no plan. On the grid it uses the game's laps and fitted tyre. Said by itself when qualifying
  ends; "what's the plan?" before lights out gets it too.
- Pit loss (strategy/model.py): stops timed by the game's pit lane timer
  (`features/pit_loss.py` `PitLossMeter`, wired in build_engine and the feature tables ->
  data/features/pit_losses.parquet): time over a window from 300 m before the entry line to 300 m
  after the exit, minus the same car's median over that stretch on green laps ±4 (SC stops: the
  other cars at the same time). Stops held 2 s+ over the usual are left out (repairs, penalties).
  When any are timed at a track they replace the in/out-lap estimate; the real F1 figure
  (`strategy/real_pit_loss.py`, OpenF1 2023-25, data/features/real_pit_loss.json, fetched in the
  background when missing) counts like 3 stops; then the default. Melbourne 19.0 s (34 stops),
  Abu Dhabi 22.6 (38), Imola 22.9 (20, was 27.3 from in/out laps). One recording can hold
  several races under one UID: the meter keeps each finished session's stops (`done_rows`).
- Wear by race length: the game wears tyres about half as fast in 5-lap races (3 Nov, same track
  and compound, you and the AI alike), while 25 % and 35 % races wear alike. `runner.solve` uses
  `prerace.same_length_rows`: a 5-lap race's laps never feed a proper-distance race (or back);
  practice/qualifying rows stay. `WearPriors` (first laps of a stint only) still pools them.
- Buffer: a taken gamble (tight plan) becomes the safe call when its gain wouldn't change a place
  (car behind further back than the gain, and you lead or couldn't catch the car ahead with it;
  `StrategyAnalyzer._buffer_check`, races only).
- Menus: `RadioDirector.idle_after_s` (cli only) says calls at once when no packet has come for
  1 s (game time is frozen there, nothing would release the queue); `cli._idle_loop` calls
  `Engine.idle_tick` to pick up worker answers. Replays/tests keep the queued behaviour.

## Strategy dialogue and console-only mode (13 Nov)

- Your preferences (`strategy/prefs.py` DriverPrefs, read from speech by
  `voice/dialogue.parse_prefs`): finishing tyre, tyres to avoid, number of stops (from here),
  first-stop lap window ("go long" = 3+ laps after the engineer's stop; "box early" = 2+
  before; "box on lap N"), your wear limit, the next tyre (an accepted offer). Kept for the race
  until "back to your plan". They're rules in the simulator (`RaceSituation.avoid`,
  `final_compound`, `min_stops`, `first_stop_range`; the limit and next tyre reuse existing
  fields), applied to every solve (`StrategyAnalyzer._submit`); `_limit()` is your limit when
  set. `set_prefs` re-plans (reason "prefs"), commits quietly (`_on_prefs`) and the answer is
  the read-back: the plan + what it costs against the engineer's own plan (the worker also
  solves `free_situation`, the race without your rules; the new plan is priced on it with
  `plan_cost`) + pushback: past our limit ("Puncture risk, but it's your call"), tight, or a
  limit set past ours ("risky if we use it"). A plan that can't be made restores your previous
  preferences and says why (the two-compound rule for "no more stops").
- Offers: a gamble `offer_gain_s` (3 s)+ quicker than the call ends the plan call with "Want
  it?" (`StrategyAnalyzer.offer`, valid OFFER_TTL_S 120 s): "yes" = prefs for that plan's stops,
  next and final tyre; "no" = never offered again this race. Not in console-only mode.
- "Why?" (`why_phrase`: your preferences if set, else the last change's reason, the worst set,
  the quicker option); "Are you sure?" (`sure_phrase`).
- Race pace (`analyzers/pace.py` PaceAnalyzer): `target()` = the worst tyre against the planned
  stop / flag at limit - margin: save needed a lap, margin in hand. "How much do I need to save?",
  "Can I push?"; proactive "Manage the tyres, save about ... a lap to make lap N" (NORMAL, every
  3 laps, 2 in console mode); "I'm managing / pushing" is measured over the next two laps, never
  assumed (nothing about tyre behaviour hard-coded).
- Console only (`radio.console_only`, None = on when push-to-talk is off; the window's controller
  choice "Console only"; switched live): alternatives with pros and cons at each plan call and
  every `alt_every_laps` (5) when within `alt_window_s` (10 s) (`StrategyAnalyzer._alternative`),
  "you've got ... in hand, you can push" (12 %+, every 4 laps), gaps ahead/behind with their
  trend every 3 laps; console extras wait 45 s for a gap (they can't ask again).

## Online lobbies and session boundaries (3 Nov)

- After a session ends the game sends for a long time with session time = FLT_MAX (results
  screen, lobby). `Pipeline.feed` skips packets whose clock isn't a real one
  (`session_time_ok`, counted in `after_end_packets`); the recorder still saves them.
- An online lobby keeps ONE session UID for every session (three qualifying sessions, then three
  races, in one file each). Restart detection splits them; per-session things key on
  (UID, track) (the pre-race plan after qualifying), and the pit-loss meter keeps each session.
- Hidden online names arrive as "Player": `race_state._named` calls them by team from the spec's
  Team IDs (`f1_2025.TEAMS`): "FERRARI", "MERCEDES 24" when two share a team.
- Outside races, corner calls compare with the session's fastest lap (`bench`), never "the car
  ahead" (timing order, not track order).

## Uploads

- `ingest/uploader.py` `BackgroundUploader` (cli, with `upload.auto`): every recording not
  uploaded, or grown 1 MB+ since (`share.pending`; uploaded.txt keeps "name size"), goes up one at
  a time in a thread whenever no session is live (no real-clock packet for 20 s), so a big push
  never competes with an online race. A grown recording replaces its branch (`+` push). Closing
  the app waits for an upload under way (3 Nov: closing mid-upload lost three races). The Live
  tab shows what's waiting, with "Upload now" (`/api/upload`, also with auto off). Shutdown still
  uploads everything pending, any run.

## Wording variety

- `radio/variety.py`: calls that recur (start, passes, lost places, fights, defend/attack, DRS,
  last lap, finish, lap-time change words, tyre status/warnings, corner losses, coaching, plan
  head, box tyre line, SC stay/in, undercut, fastest lap) pick from a few wordings with
  `pick(key, options, n)`. Facts stay in every wording; "box this lap, box this lap" never
  varies. `avoid_count`: lists of up to 3 use every wording before one comes back, longer lists
  pick from the two least recent (7 Nov: stock lines came round too often; the race craft
  lists now have 5-8 wordings, the extras only with variety on). Remembered across races
  (data/radio/variety.json, live only; live seeds randomly per session, replays seed from the
  session UID). The suite runs with it off (tests/conftest.py) so option 0 = the old wording and
  the golden calls are unchanged; tests/test_variety.py runs the fixture with it on.

## Engineer voices

- Volume slider (Settings, Engineer voice card): `radio.volume_pct` 0-100, saved to
  config.local.yaml. 70 = the loudness before the slider (gain 1.0); above 70 up to +10 dB at 100
  (`tts.volume_gain`), with `tts.soft_limit` bending peaks above 0.8 so it never hard-clips (70 %
  sounds exactly as before). Changed live (`AudioProcess.set_volume` sends {"volume": gain} to the
  audio process, no voice reload), then says "Radio check. How's that?". `radio.volume` stays as
  an extra multiplier in the config file; `cfg.radio.gain` is the product.

- `radio/engineers.py`: six engineers (Settings tab), each a Piper voice + speed; Kevin (work
  experience) also has a personality that wraps calls with panic/apologies, seeded so replays
  repeat. Not too much of him (7 Nov): big line pools, never a line twice in a session (an
  empty pool = quiet), lines from recent races kept back (`KEVIN_MEMORY`,
  data/radio/kevin.json, live only, loaded/saved in cli like variety), one line per ordinary
  call at most and `QUIRK_GAP_S` (45 s game time) between them; box/SC/penalty calls always get
  his panic. The director passes game time (`persona(topic, text, t)`) and calls
  `persona.new_session(uid)` at SessionStarted (live: random shuffle; replay: from the UID). The call text is never changed: `RadioMessage.text` stays the call (racecraft and
  answers match on it), `RadioMessage.spoken` / `.said` is what's heard (audio, console, feed).
  `radio.engineer` picks one (`radio.voice_name` / `length_scale` follow it); unset = the old
  `radio.voice`. `radio/switch.py` downloads a voice on first pick and restarts the audio
  process off the loop.

- GP (`radio/gp.py`, 12 Nov): a seventh engineer, Gary's voice with a manner learned from 976
  broadcast engineer lines on Verstappen's car (formuladream.app archive, 2021 and 2023-25;
  OpenF1 timing for when they were said; study in docs/gp-radio-study.md; raw data never in the
  repo). Wraps calls like Kevin, never changes them: `radio.driver_name` (Settings "Your name";
  unset = "mate") on about a third of calls, never within NAME_GAP_S (25 s); "Understood." /
  "Okay mate," before half the answers; the last gap echoed in gap calls ("One point two.",
  "Eight tenths.", "Two seconds.", not within ECHO_GAP_S); "Box and confirm" after box calls;
  calm openers on lost places; measured praise on passes (30 %) and at the flag; a lift at five
  laps and one lap to go; add-ons that repeat the call are dropped (`_repeats`, echoes exempt).
  `current_key` skips personality engineers, so an old setup with Gary's voice is still Gary.

## Push-to-talk (Phase 6)

- Hold LB (XInput) or L1 (PlayStation, winmm), shared with the game -> speech process (faster-whisper, own process, default
  Windows mic) -> `QuestionAsked` event -> `QuestionAnswerer` (wired in `build_engine`, so tests
  and replays answer the same way) -> director topic "answer" (HIGH, said even in quiet mode, always first).
- Answers come from the same query functions/analyzer state as the proactive calls. Beyond the
  basics: rejoin ("where do I come out", same `_rejoin` as the box call, using your gap from
  before pit entry when asked in the pit lane), true_position (cars ahead that haven't stopped
  in a dry race drop a pit loss; cars behind that have, gain on your remaining stop),
  tyre_tips (limiting tyre, the wear you can afford to the stop/flag, hottest sector, axle
  advice), speed_tips (the two costliest corners last lap and why), "repeat the strategy".
- Speech runs on the CPU by default (small.en, 4 threads, beam 1): in a race F1 25 keeps the GPU
  busy and a question waited 41 s for it (real race, 27 Oct). Answers whose recognition took
  longer than `voice.max_answer_delay_s` (10 s) are logged but not said.
- GPU speech (`voice.device: auto`) needs Nvidia's cuBLAS/cuDNN: the `gpu` dependency group (Windows, default group, so
  `uv sync` installs it). `load_model` tests the GPU with a silent clip and falls back to the CPU;
  `SpeechProcess.start` restarts on the CPU if the process crashes while loading. The installer
  build skips the `gpu` group (friends run small.en on the CPU).
- Intents are keyword rules in `engineer/voice/intents.py`. Every question is logged to
  `data/ptt/questions.jsonl`; "unknown" entries are the phrasings to add next. Logs for a session
  travel with its recording upload (`<recording>.questions.jsonl`).
- Feedback log (`engineer/voice/feedback.py`): "log that" / "log the following question: ..."
  (intent `log_question`, first in RULES, only at the start of what was said) flags a question
  with a snapshot of the race into data/feedback/logged.jsonl; feedback-log.md is rebuilt on
  SessionEnded and at shutdown. Off when `voice.log` is off (replays of logged questions). When
  answering an uploaded flagged question, add the phrasing to intents.py and the answer to
  answers.py as usual; it then shows as "now goes to <intent>".
- `replay --questions` re-asks logged questions at their session time (record everything, replay
  anything). A recording with several sessions under one UID repeats session times: ask in order,
  per session (as the 3 Nov check did).
- 3 Nov additions: fastest_overall (the session's fastest lap, not yours), what_if_lap ("box at
  lap 10 for hards": `ask_what_if` with that lap and tyre), why_not ("why not hards?" / "should I
  go hards instead?": that tyre at the planned stop vs the plan, its end wear, the gap behind),
  compound_to_end (a new set of that tyre from the end of this lap), stints_end (the plan's
  stints and their end wear), driver_pitted ("has Albon pitted?"), catch (gap, pace over the last
  3 clean laps, laps left), sector (one sector vs your best or a named car's latest clean lap),
  last_corners, clarify ("off or up?" from the last call), damage cost (pace since the damage vs
  a stop; `RaceCraftAnalyzer.damage_lap`). Strategy follow-ups are logged (`_say_followup`).
  "Hearts" is heard for hards. Round-ten lap times: "one forty point four".
- 9 Nov additions: teammate (same `team_id`: position, gap from the intervals, tyres), hype (a
  lift built on position / places gained), pit_stop_time (your last stop from the pit-lane
  timer, `PitLossMeter.stops`: stationary and lane time vs the others' typical stationary time),
  "his/her/their lap ... mine" = compare_driver with the driver last named in a question or an
  answer (`_last_driver`, set by gap answers too), why_not also for "what do you think about
  hards", "how much slower would hards be", "run a strategy sim for hards" (not with "now":
  that's box_now), "pit strat" = plan, "tyre ... rest of the race" = tyres_to_end. Damage from
  before your first clean lap: compared with the 3 quickest cars on the same laps
  (`_damage_vs_field`; said as an upper bound) instead of "give me a clean lap" forever. Plan
  asked during / just after your stop: "working out the rest of the race after the stop".

## The installed app (for friends)

- `packaging/race_engineer.spec` (PyInstaller, one folder) + `packaging/installer.iss` (Inno
  Setup, per-user, no admin) -> `RaceEngineer-Setup-<version>.exe`, built by
  `.github/workflows/release.yml` on Windows: a `v*` tag publishes it on GitHub Releases;
  "Run workflow" builds a test artifact. The build runs the tests and `RaceEngineer.exe --check`.
- Entry: `engineer/launcher.py` (first-run setup: PC or console, voice + speech downloads,
  telemetry guide; `--setup` re-runs it). When frozen, `config.py` reads the bundled
  `config.yaml` and keeps settings/data in `%LOCALAPPDATA%\RaceEngineer` (`APP_HOME`).
- Console players (Xbox/PlayStation): telemetry over the LAN to the PC; push-to-talk off.
- Mac build (same workflow, `mac` job, Apple Silicon): PyInstaller folder wrapped by
  `packaging/macos/build_dmg.sh` into "Race Engineer.app" (a shell launcher that opens Terminal)
  and a .dmg; ad-hoc signed only. Always console mode (F1 25 isn't on macOS); data in
  ~/Library/Application Support/RaceEngineer; fallback voice is the `say` command.
- Moving from a terminal install: `engineer/migrate.py` copies a clone's data/ into APP_HOME
  (never overwriting) and merges its config.local.yaml, setting `upload.repo` to the clone so
  uploads keep going through it. Offered on first run (`launcher.offer_import` finds clones in
  home/Documents/Desktop) or `RaceEngineer.exe --import "<folder>"`.
- Updates (`engineer/updates.py`, installed app only: `engineer/version.py` is "0.0.0-dev" in a
  clone and the release workflow writes the real version before bundling): `Updater` asks GitHub
  Releases (`updates.repo`) 5 s after start and every 6 h, in a thread. Newer -> banner in the app
  window (`/api/update`: download, install, dismiss = `updates.skip_version`, check, auto).
  Windows: the setup .exe goes to data/updates/ (size + GitHub's sha256 checked); "Install and
  restart" stops the app normally (cli `stopping` event; uploads/history as usual, but NOT the
  daily report: its own process held the app's files and broke the install, 11 Nov), then as its
  last step `run_installer` starts a hidden PowerShell that waits for the app's process to exit
  (`Wait-Process`, 60 s cap) and runs the installer silently with `/CLOSEAPPLICATIONS` and
  `/LOG=data/updates/install.log`; installer.iss has `CloseApplications=force` and restarts the
  app after a silent install (`skipifnotsilent`). The next start reads install.log
  (`last_install`): no "Installation process succeeded" = a warning in the log and a red line
  in Settings > Updates. Fixes in the updater itself only apply from the version AFTER the one
  doing the updating; installer.iss changes apply to the installer being run. `updates.auto_download` fetches
  it ahead whenever no session is live; installing is always a click. Mac: a link to the .dmg.
  v0.1.4 and earlier have no updater: the first version with it has to be installed by hand.
  Install requested = no blocking work at shutdown (14 Nov: the window closed, then adding the
  run's races to the history and uploading every recording kept the installer waiting for
  minutes, so updates looked broken and were closed or re-launched mid-way): this run's
  recordings go in data/updates/after_update.json (`defer_history`) and are added in a thread
  on the next start (`cli._add_deferred_history`, joined before the shutdown writes); uploads
  are left to the next start's background uploader. Applies from the version after 0.1.8.
- The installed app adds each run's recordings to `laps`/`stints` when it closes
  (`features_auto_add`), so the tyre/strategy history grows without a manual build.
- Stints are per session UID, track, session type and "part" (a car's lap number dropping by 2+
  = a restart or the next race), not per UID alone (12 Nov: a lobby's Suzuka and Jeddah races
  were one set of stints filed as Suzuka). When `summarize_stints` changes, bump
  `tables.STINTS_VERSION`: `refresh_stints` (cli start-up and `add_recordings`) re-summarises
  old stints.parquet tables from laps.parquet (`LapFeatures.from_row`; marker
  stints_version.txt), no replays needed.

## Settings and uploads

- `config.local.yaml` (gitignored) overrides `config.yaml` on one PC. Auto-upload lives there:
  `upload: {auto: true}` pushes the run's recordings when `engineer run` stops.
- Uploads never touch the working tree: parts are committed with git plumbing (hash-object,
  mktree, commit-tree) and pushed to `recording-<time>`; `data/share/uploaded.txt` lists sent files.
- Recordings go to the PRIVATE repo ryanma05-droid/F1-engineer-recordings (`upload.remote`, a
  URL), never the public code repo: they hold push-to-talk transcripts, Windows paths and other
  players' names (5 Nov, before sharing on Reddit). `share.check_not_public` refuses any remote
  pointing at `updates.repo`. Never commit recordings, logged questions or feedback to this repo.
- To fetch one here: add the private repo to the session, `git fetch <its URL> <branch>`,
  `git show` each part into a folder, then `share --join <first part>`.

## Real-data facts (confirmed on tests/fixtures/austria_race_5laps.zst)

- Lap Data spec field order is correct; `m_gridPosition` is zero-based (pole = 0).
- At the chequered flag the lap number does NOT advance; the last-lap time changes on the same lap.
- `m_lastLapTimeInMS` updates on the same frame as the lap number.
- Computed S3 can differ from the game's by 1-2 ms (separate rounding).
- Tyre wear and tyre damage come as whole percents, and tyre damage equals wear in normal running.
- Surface temps swing ~55-150 C within a lap; carcass (inner) temps move smoothly (78-96 C, Softs).
- The recording has a real flashback on lap 3 (to 185.0 s at 194.6 s).
- Telemetry comes at 60 Hz for all 22 cars, AI speed/throttle/brake included.
- Austria (4323 m) has 7 braking corners: T1 T3 T4 T6 T7 T9 T10 (T2 T5 T8 are flat).
- The golden test (`tests/test_golden.py`) replays the fixture; keep it passing.
- "Restart race" keeps the session UID; the clock just jumps back to 0 (Imola, 29 Oct). RaceState
  treats a jump from past 60 s to under 5 s that isn't a flashback as a new session, so every
  analyzer and the director start fresh (before this fix the restarted race's calls and answers
  were held back or dropped until the clock passed the first race's end).

## Tests

- `tests/conftest.py`: variety and report focus off for the suite; the real Austria race analysed
  once per run (`data_dir`, `entry`, session-scoped: copy `data_dir` before adding to it); the
  `SLOW` list (whole-race replays, marked `slow`; a full run fails if a listed test no longer
  exists). Keep the golden test out of `SLOW`. A new test that replays a whole race and takes
  3 s+ goes in the list; prefer a shorter race (`duration_ms`) that still proves the point.
- 11 Nov streamlining: 4:41 -> 3:51 full, 1:36 quick. Nothing was dropped except one test
  superseded by a stronger one (variety's "never repeats the last two").

## Environment

Development happens in a Linux cloud container; the target runtime is Windows. Keep paths and
sockets cross-platform.
