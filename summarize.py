#!/usr/bin/env python3
"""Summarize netmon logs. Usage: python3 summarize.py [log_dir]"""
import csv
import os
import statistics
import sys
from collections import Counter
from datetime import datetime

log_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "NETMON_DIR", os.path.expanduser("~/netmon-logs"))


def parse_ts(s):
    return datetime.fromisoformat(s)


def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p / 100))]


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def human(secs):
    secs = int(secs)
    if secs < 120:
        return f"{secs}s"
    if secs < 7200:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


with open(os.path.join(log_dir, "samples.csv"), newline="") as f:
    rows = list(csv.DictReader(f))
if not rows:
    sys.exit("samples.csv is empty")

times = [parse_ts(r["time"]) for r in rows]
span = (times[-1] - times[0]).total_seconds()
print("=== COVERAGE ===")
print(f"From {times[0]}  to  {times[-1]}  ({human(span)}, {len(rows)} samples)")

gaps = [(times[i - 1], times[i]) for i in range(1, len(times))
        if (times[i] - times[i - 1]).total_seconds() > 15]
if gaps:
    print(f"Monitor gaps >15s (Pi off, rebooted, or script stopped): {len(gaps)}")
    for a, b in gaps[:10]:
        print(f"  {a} -> {b}  ({human((b - a).total_seconds())})")

print("\n=== PACKET LOSS & LATENCY ===")
for col, label in [("gw_ms", "Router"), ("cf_ms", "1.1.1.1"), ("google_ms", "8.8.8.8")]:
    vals = [fnum(r[col]) for r in rows]
    ok = [v for v in vals if v is not None]
    loss = 100 * (1 - len(ok) / len(vals))
    if ok:
        print(f"{label:8} loss {loss:5.2f}%  avg {statistics.mean(ok):6.1f}ms  "
              f"p95 {pct(ok, 95):6.1f}ms  p99 {pct(ok, 99):6.1f}ms  max {max(ok):7.1f}ms")
    else:
        print(f"{label:8} loss 100%")

dns = [r["dns_ok"] for r in rows if r["dns_ok"] != ""]
if dns:
    bad = dns.count("0")
    print(f"DNS lookups: {bad} failed of {len(dns)} ({100 * bad / len(dns):.2f}%)")

sig = [fnum(r["wifi_signal_dbm"]) for r in rows]
sig = [s for s in sig if s is not None]
if sig:
    print(f"\nPi Wi-Fi signal: avg {statistics.mean(sig):.0f} dBm, "
          f"worst {min(sig):.0f} dBm  (stronger than -67 is generally fine)")
disc = sum(1 for r in rows if r["wifi_connected"] == "0")
if disc:
    print(f"Samples where the Pi's Wi-Fi was disconnected: {disc}")

print("\n=== OUTAGES ===")
ev_path = os.path.join(log_dir, "events.csv")
starts, outages = [], []
if os.path.exists(ev_path):
    with open(ev_path, newline="") as f:
        for r in csv.DictReader(f):
            if r["event"] == "outage_start":
                starts.append((parse_ts(r["time"]), r["kind"]))
            elif r["event"] == "outage_end" and starts:
                st, k = starts.pop()
                dur = float(r["detail"].split("=")[1])
                outages.append((st, r["kind"], dur))
if starts:
    for st, k in starts:
        print(f"Unfinished outage at end of log: started {st:%Y-%m-%d %H:%M:%S} ({k})")

if not outages:
    print("No outages (2+ consecutive failed cycles) recorded.")
else:
    total = sum(o[2] for o in outages)
    print(f"{len(outages)} outages, total downtime {human(total)} "
          f"({100 * total / span:.3f}% of period), longest {human(max(o[2] for o in outages))}")
    kinds = Counter(o[1] for o in outages)
    print("By kind: " + ", ".join(f"{k}={n}" for k, n in kinds.items()))
    print("  lan = router unreachable | wan = router OK, internet unreachable")
    hours = Counter(o[0].hour for o in outages)
    print("By hour of day: " + ", ".join(f"{h:02d}h:{n}" for h, n in sorted(hours.items())))
    if len(outages) > 1:
        gaps_between = [(outages[i][0] - outages[i - 1][0]).total_seconds()
                        for i in range(1, len(outages))]
        print(f"Typical time between outages: median {human(statistics.median(gaps_between))}")
    print("\nTime                       Kind     Duration")
    for st, k, d in outages[:60]:
        print(f"{st.strftime('%Y-%m-%d %H:%M:%S')}   {k:8} {human(d)}")
    if len(outages) > 60:
        print(f"... and {len(outages) - 60} more (see events.csv)")
