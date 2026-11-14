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

- **Every pass is announced**: passes, fights and lost places no longer get dropped when
  there's no straight coming (at Spa, taking the lead in the middle sector was never said).
- **No false undercuts**: when the car you were undercutting pits at the same time as you, the
  engineer no longer calls it an undercut or tells you they "stayed in front on fresher tyres".
- **Shorter track position answer**: "Where am I in track position?" names two cars still to
  stop, then how many more, instead of reading out the whole list.
- **Updates install straight away**: "Install and restart" now goes straight to the installer.
  Before, the app first added the race to your history and uploaded recordings, which could
  take minutes with nothing on screen, so it looked like the update had failed. (This helps
  from the next update on; to get this version from 0.1.8, wait for the black console window to
  say "Installing Race Engineer", or install it by hand.)
- **Pick your microphone and speakers**: Settings > Audio devices lists this PC's microphones
  and outputs. Choose which one you talk into and where the engineer's voice plays, then press
  **Test microphone**: say something and the engineer tells you what it heard. If it hears the
  game instead of you, pick another microphone (a headset plugged into a PlayStation controller
  shows up as its own microphone).
- **More answers on the radio**: say "Confirm" after a box call ("Copy, box this lap, hards");
  "At what?" or "Confirm percentages" repeats the last call; "Shorten that, log it" logs the
  last answer with your note; a question the engineer doesn't catch but that names a driver
  gets that driver's gap, tyres and pace.

On 0.1.7 or 0.1.8? The app shows "Download and install" by itself; settings and recordings are
kept. On 0.1.6 or older, install this one by hand once (download below, run it over the old one).

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
