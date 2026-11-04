# Feature ideas

Ideas waiting for a decision. When one is built, move it to PROGRESS.md and delete it here.
Newest pins first within each group.

## Pinned (asked to come back to)

- **Racing line visualizer (pinned 4 Nov).** Report tab first, radio later. Plan:
  1. Add the car's world position (motion packet, all 22 cars) to the traces, so every past
     recording can be replayed into lines.
  2. Learn each track's centre line and edges from all cars' laps (~400 laps per 20-lap race);
     kept in the data folder, never committed (like replay-learned track maps).
  3. Reference line = the fastest car through each corner on clean laps (similar tyres/fuel),
     stitched together. Per corner: brake point, turn-in, apex distance from the inside, early or
     late apex, minimum speed, full throttle point, share of the track width used on exit. Ranked
     by time lost (existing corner deltas), turned into plain advice.
  4. Report tab: track map coloured by time gained/lost, corner list, click to zoom (both lines,
     markers, speed/throttle/brake graph). Top 3 corners in the PDF.
  5. Later: a calculated minimum-curvature line inside the learned edges as a guide overlay;
     radio calls through the corner coaching ("use all the kerb on exit at Turn 4").
  First step when picked up: draw your best Melbourne lap against the fastest AI lap from the
  3 Nov lobby recording and judge whether the differences are useful. Check: other players'
  positions in online lobbies, the telemetry send rate, same coordinates across sessions.

- **Tyre wear at tracks you haven't raced (4 Nov).**
  - Use the AI cars' wear from every race: you wear about 1.4-1.6x the AI, so one race at a track
    gives ~20 cars of wear on every compound used.
  - F1Laps per-track wear (https://www.f1laps.com/f1-25/tyre-wear/) as a first guess scaled to
    you. Comparison against your 25 %+ races not done yet: www.f1laps.com has to be allowed in the
    cloud session's network settings (or paste their table). Your figures are ready
    (tracks with 25 %+ races: Melbourne, Shanghai, Texas, Abu Dhabi, Austria, Imola).
  - Check: practice wear matches race wear at the same race length; whether F1 25 lets you
    watch the AI finish after retiring.

- **Shared tyre estimates for tracks and race lengths you haven't raced (4 Nov).** Everyone's
  races count toward a starting estimate; your own laps take over as you race.
  - Pool the AI cars' wear (the same yardstick for everyone), scaled to each driver by their own
    you-vs-AI ratio; keep 5-lap and proper-length races apart (already done for your own data).
  - Pooled numbers count as limited evidence (like the real F1 pit loss counting as 3 stops), so
    a few of your own laps outweigh them.
  - Step 1: a starter tyre table built from every uploaded recording, shipped with each release
    (tyre numbers only, no names). Step 2: friends on the installed app contribute a small
    summary after each race (needs a way to send it without putting a GitHub key in the app).
  - Check: game settings that change wear (difficulty, assists), track temperature, car setup.

## Recommended earlier (not built)

- **Wet-weather strategy.** Rain forecast warnings, when to go to inters/wets and back to slicks.
- **Pre-race plan final pass.** Show what the plan assumed; a short "plan is..." call on the grid.
- **Qualifying engineer.** Gap in traffic on the out-lap, live delta to provisional pole, lap
  verdict on the in-lap.
- **Post-race debrief.** Spoken and on screen after the flag: biggest losses, strategy places won
  or lost, best and worst corners vs the field; saved per race.
- **Rival tracking.** "Keep an eye on Norris": gaps, tyre age, stops, pace drop-offs.
- **Fuel and battery.** Lift-and-coast targets, deployment for attack/defence.
- **Damage decisions (partly built).** The cost per lap is said; still to do: stop for a wing or not.
- **Practice programme.** Laps on each compound, a long run, "I have what I need".
- **Voice controls.** "Stop" to cut the engineer off, "repeat slower", a beep when push-to-talk
  starts and stops listening.
- **Season and league view.** Results, points and trends across recordings.

## Built since they were suggested

Pit loss from the pit lane timer and real F1 results; answers first while you're on the mic;
volume slider; background uploads; coaching from the driver report.
