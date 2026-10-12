#!/usr/bin/env python3
"""Build step for the Energy Demand Forecaster.

Bundles recent hourly electricity demand for a curated set of US grid regions
into a single static index.html. Data comes from EIA's keyless Grid Monitor
six-month CSV files (EIA-930 balancing-authority hourly demand). No API key.

The raw CSVs (~45 MB each) are cached under ./.cache so re-runs are fast.
Re-run to refresh / extend the window: `python3 build.py`.
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{}.csv"
# most recent complete + partial half-years (edit to extend history)
FILES = ["EIA930_BALANCE_2026_Jan_Jun", "EIA930_BALANCE_2026_Jul_Dec"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

# curated regions: BA code -> (display name, timezone label)
REGIONS = {
    "PJM":  ("PJM Interconnection (Mid-Atlantic)", "ET"),
    "MISO": ("Midcontinent ISO (Midwest)", "CT"),
    "ERCO": ("ERCOT (Texas)", "CT"),
    "CISO": ("California ISO", "PT"),
    "SOCO": ("Southern Company (Southeast)", "ET"),
    "NYIS": ("New York ISO", "ET"),
    "ISNE": ("ISO New England", "ET"),
    "FPL":  ("Florida Power & Light", "ET"),
}


def get_csv_text(stem):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, stem + ".csv")
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    url = BASE.format(stem)
    sys.stderr.write("downloading %s ...\n" % stem)
    req = urllib.request.Request(url, headers={"User-Agent": "energy-forecast-build/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)
    return data


def parse_local(ts):
    # e.g. "01/01/2026 1:00:00 AM"
    return datetime.strptime(ts.strip(), "%m/%d/%Y %I:%M:%S %p")


def main():
    # BA code -> {datetime: demand}
    series = {code: {} for code in REGIONS}
    for stem in FILES:
        text = get_csv_text(stem)
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}
        ci_ba = col["Balancing Authority"]
        ci_t = col["Local Time at End of Hour"]
        ci_d = col["Demand (MW)"]
        ci_adj = col.get("Demand (MW) (Adjusted)", ci_d)
        for row in reader:
            if len(row) <= ci_adj:
                continue
            ba = row[ci_ba]
            if ba not in series:
                continue
            raw = row[ci_adj] or row[ci_d]
            if not raw:
                continue
            try:
                val = float(raw)
                t = parse_local(row[ci_t])
            except (ValueError, IndexError):
                continue
            series[ba][t] = val  # later file/dup overwrites earlier harmlessly

    assets, data = [], {}
    for code, (name, tz) in REGIONS.items():
        pts = series[code]
        if len(pts) < 24 * 60:
            sys.stderr.write("  skip %s (only %d hours)\n" % (code, len(pts)))
            continue
        lo, hi = min(pts), max(pts)
        n = int((hi - lo).total_seconds() // 3600) + 1
        values = [None] * n
        for t, v in pts.items():
            idx = int((t - lo).total_seconds() // 3600)
            if 0 <= idx < n:
                values[idx] = round(v)
        start = lo.strftime("%Y-%m-%dT%H")
        data[code] = {"start": start, "values": values}
        assets.append({"id": code, "name": name, "tz": tz, "start": start, "n": n})
        sys.stderr.write("  %-5s %5d hours  %s .. %s\n"
                         % (code, n, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")))

    bundle = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
              "regions": assets, "series": data}
    payload = json.dumps(bundle, separators=(",", ":"))

    with open("index.template.html") as fh:
        template = fh.read()
    html = template.replace("/*__DATA__*/", payload)
    with open("index.html", "w") as fh:
        fh.write(html)
    sys.stderr.write("wrote index.html (%d KB) — %d regions\n"
                     % (len(html) // 1024, len(assets)))


if __name__ == "__main__":
    main()
