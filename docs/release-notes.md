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

- **Updates in the app**: from this version on, the app tells you when a new one is out. On
  Windows, "Download and install" then "Install and restart" updates it for you (settings and
  recordings are kept). Settings > Updates can download new versions automatically (never during
  a session; installing is always your click). This version itself has to be installed by hand.
- **Your questions come first**: anything you ask on the radio is answered before any other
  call, and the engineer doesn't talk over you while you hold the button.
- **Volume slider** (Settings > Engineer voice): 70 % is the old loudness, up to about three times
  louder at 100 %.
- **Coaching from your driver report**: the corners where you usually lose time at a track are
  coached first from lap one, the engineer says what to work on after your first lap, and tells
  you when a report corner is fixed. Ask "what should I work on?" any time. Build a report once
  (Driver report tab) to switch it on.
- **Driver report fix**: a recording with several sessions (an online lobby, a race weekend) is
  now measured session by session. Before, every lap was measured on the first track's corners.
- **Background uploads**: recordings go up to GitHub while you're not racing, and closing the
  app no longer cuts an upload off.
- **Smarter strategy**: tyre wear from 5-lap races isn't mixed with proper-length races, a tight
  plan gives way to the safe one when its gain changes no position, and the pre-race plan is said
  after every qualifying in an online lobby.
- **More answers on the radio**: "has Albon pitted?", "can I catch him?", "why not hards?",
  "box at lap ten for hards?", sector comparisons, and "what's the fastest lap?".
- Hidden online players are named by team ("Ferrari"), and lap times like 1:40.4 are said "one
  forty point four".

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
