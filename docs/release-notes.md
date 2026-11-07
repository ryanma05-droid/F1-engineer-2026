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

- **Your ideal lap**: the best you've shown lately, corner by corner, on the tyres you have now.
  It's built from your last five clean laps this stint (not your fastest lap of the race, which
  was on fresh tyres), so it stays a fair target as the tyres wear. Ask "what's my ideal lap?",
  see it on the Live tab's Coaching card, and in coaching mode the engineer tells you how far off
  it you were and where. The driver report's corner table now compares with it too.
- **Gear coaching** (manual gears only): when you take a slow corner a gear lower than the
  quickest car at the same speed, the engineer says so ("take the apex a gear higher"), and the
  driver report spots it as a habit. On the automatic gearbox there's no gear advice.
- Open source under the MIT licence.

Already on 0.1.5? The app shows "Download and install" by itself; settings and recordings are
kept.

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
