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

- **Pre-race tyre plan**: before the race ask "what tyres should I start on?" (or "strategy for
  Suzuka, fifteen laps"), or use the Pre-race card on the Live tab. It's also said when
  qualifying ends.
- **Coaching mode**, a fourth tempo: each lap against the fastest car on track, corner by corner,
  with a summary when the session ends and a Coaching card on the Live tab.
- **Plain corner advice** by default ("brake slightly later, then carry a bit more speed through
  the corner"); say "give me the numbers" or pick Specific in Settings for metres and km/h.
- **Pick your engineer's voice** on the Settings tab: five real-sounding engineers, and Kevin,
  who is on work experience.
- **Calls worded a few different ways**, so one race doesn't sound like the last.
- **Better pit stop timing**: pit loss is measured with the game's own pit lane timer (real F1
  figures are used until a track has a timed stop), so box calls and "where do I come out?" are
  closer to reality.
- Losing a place is called the same way as a pass (after it sticks), a good defence is praised,
  and no place changes are called around your own stop.

### Mac (Apple Silicon, for F1 25 on Xbox / PlayStation)

Download **RaceEngineer-*-mac.dmg**, open it and drag Race Engineer into Applications. The first
open needs System Settings > Privacy & Security > **Open Anyway** (the app isn't signed with a
paid Apple account); "READ ME FIRST" in the download explains it step by step. Settings and
recordings are kept in `~/Library/Application Support/RaceEngineer`.

### If something goes wrong

The window stays open with the error, and everything it showed is saved
in `logs/race-engineer.log` inside the settings folder above: send that file.
