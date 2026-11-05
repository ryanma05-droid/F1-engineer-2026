# Race Engineer for F1 25

A race engineer in your ear for EA SPORTS F1 25. It reads the game's telemetry and talks to you
over the radio like a real engineer: gaps, tyres, pit strategy, damage, and corner-by-corner
coaching. Hold a controller button and ask it questions out loud.

Free and open source (MIT). A hobby project, not affiliated with EA, Codemasters or Formula 1.

## Download

**[Latest release](https://github.com/ryanma05-droid/F1-engineer-2026/releases/latest)**

- **Windows (PC players):** `RaceEngineer-Setup-<version>.exe`. Installs for your user only (no
  admin rights). The first run walks you through setup.
- **Mac (Xbox/PlayStation players):** `RaceEngineer-<version>-mac.dmg`, Apple Silicon (M1 or
  newer). The console sends telemetry over your Wi-Fi and the Mac does the talking. See
  [Mac first open](docs/mac-first-open.md).
- **Windows PC + console:** the Windows installer also works as the engineer for an Xbox or
  PlayStation on the same network.

The app checks for new versions and offers to install them (installing always needs a click).

### "Windows protected your PC"

The installer isn't code-signed (a signing certificate costs a few hundred dollars a year), so
the first time you run it Windows SmartScreen shows a blue warning. Click **More info**, then
**Run anyway**. On a Mac it's the same story: see [Mac first open](docs/mac-first-open.md).

If you'd rather not trust a download, everything here is the source the installer is built
from: GitHub Actions builds it from this repo on every release (`.github/workflows/release.yml`),
and the Releases page lists each file's SHA-256.

## Game settings

Settings > Telemetry Settings: UDP Telemetry **On**, Broadcast Mode **Off**, IP Address
`127.0.0.1` (PC) or the IP the setup shows (console), UDP Port `20777`, UDP Send Rate **60Hz**,
UDP Format **2025**. Full steps: [Telemetry guide](docs/telemetry-guide.md).

## What it does

- **Race calls:** lap times and gaps, the car ahead and behind, safety car, penalties, passes and
  lost places, the last lap and the finish.
- **Tyres and strategy:** wear warnings, a pit plan that learns from your own races (one lap,
  one tyre: "box end of lap twelve for softs"), undercut warnings, where you'll come out after a
  stop, a pre-race plan.
- **Corner coaching:** where you lose time against the quickest car on track and why (braking,
  apex speed, throttle, gears on a manual gearbox), your ideal lap from your recent laps, and a
  "coaching mode" that talks you through each lap.
- **Push-to-talk (PC):** hold LB (Xbox controller) or L1 (PlayStation) and ask "what's the
  plan?", "how are the tyres?", "where am I losing time?", "has Albon pitted?" and lots more.
  Speech recognition runs on your PC; nothing is sent anywhere.
- **Driver report:** a PDF of your strengths and weaknesses across every session you've
  recorded, which also steers the live coaching.
- **App window:** live race view, radio log, settings (six engineer voices, volume, how chatty
  it is).

The strategy and tyre models start from the game's own figures and get better with every race
you drive, so expect your first few races at a track to be rougher.

## Privacy

Everything runs on your machine. The app only goes online to check for updates (GitHub
Releases), to download the voices and speech model once (Hugging Face), and to fetch real F1
pit-stop times once (OpenF1). None of these send anything about you or your races. Your races
are recorded to your own data folder (`%LOCALAPPDATA%\RaceEngineer` on Windows,
`~/Library/Application Support/RaceEngineer` on a Mac) and never uploaded. The app window is
only reachable from your own computer.

## Running from source (developers)

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run python -m engineer.radio.setup_voice --test             # download the voice, radio check
uv run python -m engineer run                                  # live
uv run python -m engineer.ingest.replay <file.zst> --speed max # replay a recording
uv run python -m engineer.report                               # driver report PDF
uv run pytest                                                  # tests
```

`docs/architecture.md` explains the design, `CLAUDE.md` the rules the code follows, and
`PROGRESS.md` what's done and what's open. Packet layouts come from EA's published F1 25 UDP
specification (`docs/spec/`).

## Bugs and ideas

Open an issue. If something breaks, attach the log: `%LOCALAPPDATA%\RaceEngineer\logs\race-engineer.log`
(Windows) or `~/Library/Application Support/RaceEngineer/logs/race-engineer.log` (Mac).

## Licence

MIT: see [LICENSE](LICENSE). F1 25 and EA SPORTS are trademarks of their owners.
