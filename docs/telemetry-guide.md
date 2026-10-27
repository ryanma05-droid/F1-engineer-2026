# Turning on F1 25 telemetry for the Race Engineer

Do this once. The engineer listens for the game's telemetry on your PC.

## In F1 25

Main menu: **Game Options > Settings > Telemetry Settings**

| Setting | Playing on this PC | Playing on Xbox / PlayStation |
| --- | --- | --- |
| UDP Telemetry | On | On |
| UDP Broadcast Mode | Off | Off |
| UDP IP Address | 127.0.0.1 | your PC's IP address (below) |
| UDP Port | 20777 | 20777 |
| UDP Send Rate | 60Hz | 60Hz |
| UDP Format | 2025 | 2025 |
| Your Telemetry | Public | Public |

## Xbox / PlayStation: your PC's IP address

The Race Engineer window shows it when it starts. To look it up yourself: press Start, type
`cmd`, press Enter, type `ipconfig` and press Enter. Use the **IPv4 Address** under your Wi-Fi or
Ethernet adapter (for example `192.168.1.23`).

- The PC must be switched on and on the same Wi-Fi / router as the console.
- The engineer's voice plays on the PC: use its speakers, or earbuds/headphones on the PC.
- Asking questions (push-to-talk) is only for players on the PC.

## Windows Firewall

The first time the Race Engineer runs, Windows asks whether to allow it on the network. Tick
**Private networks** and click **Allow**. If you clicked Cancel by mistake: Start > "Allow an app
through Windows Firewall" > Change settings > tick RaceEngineer for Private.

## Is it working?

Start the Race Engineer, then drive any session. Within a few seconds of crossing the line you
should see lap lines and hear "Radio check" at start-up. If nothing shows up, check in this order:
the IP address, the port (20777), the firewall.

## If something goes wrong

Everything the Race Engineer window shows is also saved to
`%LOCALAPPDATA%\RaceEngineer\logs\race-engineer.log` (the run before is kept as
`race-engineer.previous.log`). Paste `%LOCALAPPDATA%\RaceEngineer\logs` into the File Explorer
address bar to find them, and send the file to whoever shared the app with you.
