#!/usr/bin/env python3
"""netmon: logs reachability of your router and the internet, and records outages.

Every INTERVAL seconds it pings the default gateway (your router) and two public
DNS servers. Every SLOW_EVERY seconds it also tests DNS lookups and records Wi-Fi
signal info (if the Pi is on Wi-Fi). Standard library only.

Files written to NETMON_DIR (default ~/netmon-logs):
  samples.csv  one row per cycle (latency in ms, blank = no reply)
  events.csv   monitor_start, outage_start, outage_end rows

Outage kinds:
  lan  the router (default gateway) did not answer
  wan  the router answered but neither internet target did
"""
import csv
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

LOG_DIR = os.environ.get("NETMON_DIR", os.path.expanduser("~/netmon-logs"))
WIFI_IFACE = os.environ.get("NETMON_WIFI_IFACE", "wlan0")
INTERVAL = float(os.environ.get("NETMON_INTERVAL", "2"))
SLOW_EVERY = 10          # seconds between DNS + Wi-Fi checks
OUTAGE_MIN_CYCLES = 2    # consecutive bad cycles before it counts as an outage
INTERNET_TARGETS = ["1.1.1.1", "8.8.8.8"]
DNS_TEST_NAME = "example.com"

SAMPLE_HEADER = [
    "time", "gateway", "gw_ms", "cf_ms", "google_ms",
    "dns_ok", "dns_ms", "wifi_connected", "wifi_signal_dbm",
    "wifi_bitrate_mbps", "wifi_freq_mhz", "wifi_bssid",
]
EVENT_HEADER = ["time", "event", "kind", "detail"]


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_gateway():
    try:
        out = subprocess.run(["ip", "route", "show", "default"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"default via (\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def ping(host):
    """Return round-trip ms, or None if no reply."""
    if not host:
        return None
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "1", host],
                             capture_output=True, text=True, timeout=4).stdout
        m = re.search(r"time[=<]([\d.]+) ?ms", out)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def dns_check():
    """Return (ok, ms) for a lookup using the system resolver."""
    start = time.monotonic()
    try:
        r = subprocess.run(["getent", "hosts", DNS_TEST_NAME],
                           capture_output=True, text=True, timeout=5)
        ok = r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        ok = False
    return ok, round((time.monotonic() - start) * 1000, 1)


def wifi_info():
    """Return dict of Wi-Fi link info; empty values if unavailable."""
    info = {"connected": "", "signal": "", "bitrate": "", "freq": "", "bssid": ""}
    try:
        out = subprocess.run(["iw", "dev", WIFI_IFACE, "link"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return info
    if not out.strip():
        return info
    if "Not connected" in out:
        info["connected"] = 0
        return info
    info["connected"] = 1
    for pattern, key in [
        (r"Connected to ([0-9a-f:]{17})", "bssid"),
        (r"freq: (\d+)", "freq"),
        (r"signal: (-?\d+)", "signal"),
        (r"tx bitrate: ([\d.]+)", "bitrate"),
    ]:
        m = re.search(pattern, out)
        if m:
            info[key] = m.group(1)
    return info


def fmt(v):
    return "" if v is None else v


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    samples_path = os.path.join(LOG_DIR, "samples.csv")
    events_path = os.path.join(LOG_DIR, "events.csv")
    new_samples = not os.path.exists(samples_path) or os.path.getsize(samples_path) == 0
    new_events = not os.path.exists(events_path) or os.path.getsize(events_path) == 0
    samples_f = open(samples_path, "a", newline="")
    events_f = open(events_path, "a", newline="")
    samples = csv.writer(samples_f)
    events = csv.writer(events_f)
    if new_samples:
        samples.writerow(SAMPLE_HEADER)
    if new_events:
        events.writerow(EVENT_HEADER)

    def log_event(event, kind="", detail=""):
        events.writerow([now_iso(), event, kind, detail])
        events_f.flush()

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    log_event("monitor_start", detail=f"interval={INTERVAL}s")

    gateway = get_gateway()
    last_slow = 0.0
    dns_ok, dns_ms = "", ""
    wifi = {"connected": "", "signal": "", "bitrate": "", "freq": "", "bssid": ""}

    streak_kind, streak, streak_start = None, 0, None
    in_outage, outage_start, outage_kinds = False, None, []
    cycles = 0
    next_tick = time.monotonic()

    pool = ThreadPoolExecutor(max_workers=3)
    try:
        while True:
            cycle_time = datetime.now().astimezone()
            slow = (time.monotonic() - last_slow) >= SLOW_EVERY
            if slow:
                gateway = get_gateway()
                dns_ok_b, dns_ms = dns_check()
                dns_ok = 1 if dns_ok_b else 0
                wifi = wifi_info()
                last_slow = time.monotonic()

            f_gw = pool.submit(ping, gateway)
            f_cf = pool.submit(ping, INTERNET_TARGETS[0])
            f_g = pool.submit(ping, INTERNET_TARGETS[1])
            gw_ms, cf_ms, g_ms = f_gw.result(), f_cf.result(), f_g.result()

            row = [cycle_time.isoformat(timespec="seconds"), gateway or "",
                   fmt(gw_ms), fmt(cf_ms), fmt(g_ms)]
            if slow:
                row += [dns_ok, dns_ms, wifi["connected"], wifi["signal"],
                        wifi["bitrate"], wifi["freq"], wifi["bssid"]]
            else:
                row += [""] * 7
            samples.writerow(row)
            cycles += 1
            if cycles % 10 == 0:
                samples_f.flush()

            # Classify this cycle
            if gw_ms is None:
                kind = "lan"
            elif cf_ms is None and g_ms is None:
                kind = "wan"
            else:
                kind = "ok"

            if kind == "ok":
                if in_outage:
                    secs = (cycle_time - outage_start).total_seconds()
                    log_event("outage_end", "+".join(outage_kinds),
                              f"duration_s={secs:.0f}")
                    in_outage = False
                streak_kind, streak = None, 0
            else:
                if kind == streak_kind:
                    streak += 1
                else:
                    streak_kind, streak, streak_start = kind, 1, cycle_time
                if in_outage:
                    if kind not in outage_kinds:
                        outage_kinds.append(kind)
                elif streak >= OUTAGE_MIN_CYCLES:
                    in_outage, outage_start, outage_kinds = True, streak_start, [kind]
                    events.writerow([outage_start.isoformat(timespec="seconds"),
                                     "outage_start", kind, ""])
                    events_f.flush()

            next_tick += INTERVAL
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        pool.shutdown(wait=False)
        samples_f.flush()
        events_f.flush()
        samples_f.close()
        events_f.close()


if __name__ == "__main__":
    sys.exit(main())
