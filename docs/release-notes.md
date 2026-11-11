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

- **Talk strategy with the engineer**: say "finish on softs", "max fifty percent", "one stop
  only", "no hards", "I want to go long" or "let's box on lap twelve" and the plan follows,
  read back with what it costs against the engineer's own plan. Risky requests get pushback
  ("that's past our limit, puncture risk, but it's your call"). "Back to your plan" clears them.
  When a riskier plan is worth it, the engineer asks "Want it?": say yes or no. Ask "why?",
  "are you sure?", "how much do I need to save?", "can I push?", or say "I'm managing" and the
  engineer tells you two laps later what it changed. When the tyres won't make the plan you'll
  hear "manage the tyres, save about one percent a lap".
- **Console only** (no push-to-talk): pick "Console only" as the controller and the engineer
  volunteers what you can't ask: other strategy options with their pros and cons, push and
  manage calls, and the gaps ahead and behind every few laps.
- **GP**, a new engineer in Settings: Gary's voice with the manner of a real race engineer,
  learned from four seasons of broadcast team radio. Calm, short, the gap said twice, "box and
  confirm". Put your name in Settings ("Your name") and GP uses it.
- **Weather**: rain coming from the game's forecast ("rain expected in about ten minutes,
  around lap twelve, sixty percent chance"), rain starting and easing. "Will it rain?" answers
  with the forecast.
- **Penalties**: your track-limit warnings counted ("that's two"); a time penalty on a car near
  you and what it means ("you're inside it, stay there and you're ahead"); "Any penalties?".
- **The cars around you**: "Norris ahead is struggling, losing four tenths a lap"; "expect
  Bortoleto to pit soon"; ask "How's Russell looking?" for his tyres, stops and pace against yours.
- **Push calls**: "push in, it's the undercut on Norris", "big out lap", then whether the undercut
  worked; "push now" when the car ahead or behind is closing in; encouragement in a battle that
  lasts a few laps.
- **More answers**: "Where's my teammate?", "How long did the pit stop take?", "his last lap
  relative to mine", a bit of hype on request, "pit strat", wing damage from lap one.
- **Fixes from your races**: lap times are said at tracks where the line is close to turn 1
  (Baku); box calls and "last lap" are never dropped; a lap re-driven after a flashback isn't
  announced twice; a car going into the pits isn't called a pass; the most urgent calls go before
  corner tips; Kevin and the others repeat themselves less.
- **Updates install more reliably**: the app no longer starts the driver report while an update
  installs, waits for itself to close, and tells you if an install didn't finish.

Already on 0.1.5 or 0.1.6? The app shows "Download and install" by itself; settings and
recordings are kept.

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
