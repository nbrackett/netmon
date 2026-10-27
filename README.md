# netmon: week-long connectivity monitor for a Raspberry Pi

Pings your router and the internet every 2 seconds, logs every outage with its
type and duration, and gives you a summary to bring to your ISP (or to me).

## Before you start: how the Pi connects matters

The Pi Zero 2 W has no Ethernet port and only a 2.4 GHz Wi-Fi radio.

- **Best:** a micro-USB OTG Ethernet adapter, plugged into the router. Then
  the monitor measures only router + upstream, with Wi-Fi out of the picture.
- **Fine if you only have Wi-Fi:** the logs still separate the cases.
  `lan` outages (router unreachable) could be Wi-Fi or the router;
  `wan` outages (router answers, internet doesn't) are upstream either way.
  Wi-Fi signal and disconnects are logged too.
- Don't use this Pi for speed tests: its Wi-Fi tops out far below 300 Mbps.

## Install

1. Copy this folder to the Pi as `~/netmon` (for example
   `scp -r netmon pi@raspberrypi.local:~/`).
2. On the Pi:

   ```bash
   cd ~/netmon
   sed -i "s/YOURUSER/$USER/g" netmon.service
   sudo cp netmon.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now netmon
   ```

3. Check it works: `systemctl status netmon` and `tail ~/netmon-logs/samples.csv`.

It restarts itself on crash and starts at boot, so a power blip won't end the test.
Use a decent power supply; a flaky one will show up as monitor gaps.

## After a week

```bash
python3 ~/netmon/summarize.py
```

Paste the output back to me. Raw data is in `~/netmon-logs/` (about 20 MB/week).

## Also do this

When an outage happens, glance at your router's (and modem's) status lights
and event log, and note the time. Match against `events.csv`.

## Reading the results

| Pattern | Likely cause |
|---|---|
| `wan` outages | Modem, cable/line, or ISP. Take the timestamps to your ISP. |
| `lan` outages with a wired Pi | Router crashing/rebooting or a bad port/cable. |
| `lan` outages with a Wi-Fi Pi, signal drops or disconnects logged | Wi-Fi or router radio problem. |
| Low loss but high p99 latency | Choppiness without full outages: congestion or interference. |
| Monitor gaps | The Pi lost power or rebooted. Not a network finding. |

Note: the Pi has no clock battery. After a reboot with no internet, its
timestamps can be off until it syncs.

## Settings

Environment variables: `NETMON_DIR`, `NETMON_INTERVAL` (seconds, default 2),
`NETMON_WIFI_IFACE` (default `wlan0`).
