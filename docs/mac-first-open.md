# Race Engineer on a Mac: first open

F1 25 isn't available on macOS, so the Mac is the race engineer for F1 25 on your Xbox or
PlayStation: the console sends its telemetry over your Wi-Fi to the Mac, and the engineer talks
through the Mac's speakers or your AirPods. Needs a Mac with Apple Silicon (M1 or newer).

1. Drag **Race Engineer** into **Applications**.
2. Open it. macOS will say it can't check the app for malicious software (it isn't signed with a
   paid Apple developer account). Click **Done**, then open **System Settings > Privacy &
   Security**, scroll down and click **Open Anyway** next to "Race Engineer", and confirm.
3. A Terminal window opens with the first-time setup. If macOS asks to allow incoming network
   connections or to find devices on your local network, click **Allow**.
4. Set up F1 25 on the console as the setup shows (Telemetry guide.txt has the same steps).

If it still won't open, open Terminal (Applications > Utilities) and paste this, then press
Enter and open the app again:

    xattr -dr com.apple.quarantine "/Applications/Race Engineer.app"

Your settings and recordings are kept in `~/Library/Application Support/RaceEngineer`. If
something goes wrong, send `~/Library/Application Support/RaceEngineer/logs/race-engineer.log`.
