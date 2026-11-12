## Race Engineer for EA SPORTS F1 25

### Windows

1. Download **RaceEngineer-Setup-*.exe** below and run it. If Windows says "Windows protected
   your PC", click **More info**, then **Run anyway** (the app isn't code-signed).
2. The first start asks where you play (PC, or Xbox/PlayStation) and downloads the engineer's
   voice. Then the app window opens and asks which controller you use (PlayStation: hold
   **L1** to ask the engineer something; Xbox: hold **LB**) and how much the engineer should
   talk (Quiet, Normal, Chatty or Coaching; change it mid-race by saying "talk more" or "keep it short").
   The window shows your race live, the team radio, and your driver report.
3. Turn on telemetry in F1 25 (Game Options > Settings > Telemetry Settings). The settings are
   shown at the end of setup, and in Start menu > Race Engineer > Telemetry guide.
4. When Windows asks about the firewall, allow it on **Private networks**.

Your settings and recordings are kept in `%LOCALAPPDATA%\RaceEngineer`, and updates keep them:
install a newer version over the old one and everything carries on.

Ran the engineer from a terminal before (a copy of the project folder)? The first start finds that
folder and offers to bring your recordings, history, reports and settings over (or run
`RaceEngineer.exe --import "<that folder>"`). Nothing in the old folder is changed.

### New in this version

- **More energy when it matters**: passes, fights, battles, push laps and the undercut are said
  faster, livelier and a touch louder by every engineer ("Great move on Norris! P three."). Lose
  a place and you get encouragement ("Stay close, the move back is on!"). GP lifts too ("Get in
  there!").
- **Battles**: any car within two seconds. Every lap of a battle you hear the gap and which way
  it's going ("Antonelli behind, one point four, you're holding them. Great job, keep it up!").
- **When the stops shake out**: "Stops all done for everyone. P six, up two places on the cycle."
- **Your time penalty**: the gap you need to the car behind, every lap until it's served ("you
  need three seconds on Antonelli. Push, push!"), and when you're clear of it.
- **Chatty mode says more**: cars around you pitting, and battle updates for cars a bit further
  away.
- **Undercuts**: your stop is now tracked as an undercut even when another car is coming out
  of the pits as you go in ("Push now, big out lap", then whether it worked).
- **New questions**: "Is this an undercut opportunity?", "Do you think Ocon will pit?", "How am
  I looking to Kimi?", "What about this battle with Norris?", "Go chatty mode", "Quiet mode",
  "Will I need to pit again?".
- **Lap delta overlay** (PC, borderless windowed): a small window over the game with your delta
  to your previous lap at every point of the lap, and what each corner cost. Turn it on in
  Settings > Lap delta overlay; "Move overlay" to drag and resize it, then "Lock here".
- **Tyre history fix**: several races in one online lobby are no longer mixed into one track's
  tyre wear history (fixed by itself on the first start).

On 0.1.7? The app shows "Download and install" by itself; settings and recordings are kept.
On 0.1.6 or older, install this one by hand once (download below, run it over the old one):
those versions' in-app installer was the broken one.

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
