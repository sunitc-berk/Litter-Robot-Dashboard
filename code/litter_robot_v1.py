#!/usr/bin/env python3
"""
Litter-Robot 4 Monitor  (full-extraction version + CSV logging)
===============================================================
Connects to your Whisker account and dumps *everything* the pylitterbot
API exposes: full device status + diagnostics, the complete activity
archive, usage insights (incl. cat detections), sleep schedule, firmware
update availability, and full pet profiles + weight history.

It is designed to be run repeatedly (e.g. every couple of hours). On each
run it appends to two monthly CSV logs, de-duplicating so re-runs never
create duplicate rows:

  * litter_robot_logs_MM-YYYY.csv         — robot STATUS snapshots
      (litter %, waste drawer %, cycle count, faults, ... one row per
       robot per run, but only when something changed since the last row)

  * litter_robot_usage_logs_MM_YYYY.csv   — ACTIVITY events
      (cat detected / weight recorded / clean cycles ... like the
       lr4-*_activity_*.csv exports). Columns: Robot, Activity, Timestamp, Value

A new monthly file is created automatically when one does not exist; rows are
routed to the file for the month of the row's timestamp.

Usage:
    python litter_robot_v1.py                       # run + append to logs
    python litter_robot_v1.py --log-dir "C:\\path"   # write logs elsewhere
    python litter_robot_v1.py --no-install           # don't auto-install deps
    python litter_robot_v1.py > report.txt           # also save console report

Credentials are taken from environment variables if set, otherwise you'll be
prompted interactively:

    Windows PowerShell:  $env:WHISKER_USERNAME="you@example.com"
                         $env:WHISKER_PASSWORD="your-password"
    Windows CMD:         set WHISKER_USERNAME=you@example.com
    macOS/Linux:         export WHISKER_USERNAME="you@example.com"

SECURITY NOTE: never hard-code credentials in this file. Use the environment
variables above so secrets are never committed to version control.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import importlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Settings ─────────────────────────────────────────────────────────────────
# How many activity events to pull per robot. The old script used 30; the API
# accepts any limit, so this effectively pulls your whole history. Lower it if
# you only want recent events.
ACTIVITY_LIMIT = 2000
INSIGHT_DAYS = 30
WEIGHT_HISTORY_LIMIT = 100

# Third-party packages this script needs: (import name, pip install spec)
REQUIRED_PACKAGES = [("pylitterbot", "pylitterbot")]

# Directory where the CSV logs are written. Set in run() from --log-dir, and
# defaults to the folder that contains this script.
LOG_DIR = "."

# CSV schemas
STATUS_FIELDS = [
    "snapshot_time", "robot", "serial", "model", "online", "status",
    "litter_pct", "litter_state", "waste_drawer_pct", "drawer_full",
    "cycle_count", "cycle_capacity", "cycles_after_full", "scoops_saved",
    "last_pet_weight_lbs", "clean_wait_min", "sleep_active", "hopper_status",
    "globe_motor_fault", "usb_fault", "wifi_mode", "firmware",
]
USAGE_FIELDS = ["Robot", "Activity", "Timestamp", "Value"]


# ── Dependency bootstrap ─────────────────────────────────────────────────────
def _pip_install(pip_spec: str) -> None:
    """Install a package, falling back through strategies that handle
    PEP 668 "externally-managed-environment" interpreters (e.g. uv- or
    distro-managed Pythons that block a plain `pip install`)."""
    base = [sys.executable, "-m", "pip", "install"]
    attempts = [
        base + [pip_spec],                                       # normal install
        base + ["--break-system-packages", pip_spec],            # override PEP 668 guard
        base + ["--user", "--break-system-packages", pip_spec],  # per-user fallback
    ]
    last_exc = None
    for cmd in attempts:
        try:
            subprocess.check_call(cmd)
            return
        except subprocess.CalledProcessError as exc:
            last_exc = exc
            extra = " ".join(cmd[4:-1]) or "(default)"
            print(f"[setup] install attempt failed [{extra}] — trying another method ...")
    raise SystemExit(
        f"[setup] Could not install '{pip_spec}' automatically: {last_exc}\n"
        f"Your Python looks like an 'externally-managed' environment. "
        f"Fix it one of these ways:\n"
        f"  1) Create & use a virtual environment:\n"
        f"       python -m venv .venv\n"
        f"       .venv\\Scripts\\activate          (Windows)\n"
        f"       pip install {pip_spec}\n"
        f"  2) If you use uv:   uv pip install {pip_spec}\n"
        f"  3) Force it:        {sys.executable} -m pip install --break-system-packages {pip_spec}"
    )


def ensure_dependencies(auto_install: bool = True) -> None:
    """Ensure required third-party packages are importable.

    If a package is missing and auto_install is True, install it with pip using
    the current interpreter. If auto_install is False, exit with instructions.
    """
    for import_name, pip_spec in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            if not auto_install:
                raise SystemExit(
                    f"Missing dependency '{import_name}'.\n"
                    f"Install it with:\n    {sys.executable} -m pip install {pip_spec}\n"
                    f"(or re-run without --no-install to install automatically)."
                )
            print(f"[setup] '{import_name}' not found — installing '{pip_spec}' ...")
            _pip_install(pip_spec)
            importlib.invalidate_caches()
            importlib.import_module(import_name)


# ── Credentials ──────────────────────────────────────────────────────────────
def resolve_credentials() -> "tuple[str, str]":
    """Resolve Whisker credentials.

    Order of precedence:
      1. WHISKER_USERNAME / WHISKER_PASSWORD environment variables (preferred;
         this is how unattended / scheduled-task runs supply credentials).
      2. Interactive prompt — only when running with a console attached.

    Running unattended (e.g. Task Scheduler) with the env vars unset exits
    immediately with a clear message instead of blocking on a prompt.
    """
    username = os.environ.get("WHISKER_USERNAME")
    password = os.environ.get("WHISKER_PASSWORD")
    if username and password:
        return username, password

    # Env vars incomplete — only prompt if we actually have an interactive console.
    if not sys.stdin or not sys.stdin.isatty():
        raise SystemExit(
            "Missing credentials: set the WHISKER_USERNAME and WHISKER_PASSWORD "
            "environment variables (no interactive console available to prompt)."
        )

    try:
        import getpass

        username = username or input("Whisker username (email): ").strip()
        password = password or getpass.getpass("Whisker password: ")
    except Exception:
        raise SystemExit(
            "Set WHISKER_USERNAME and WHISKER_PASSWORD environment variables."
        )
    return username, password


# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_dt(dt) -> str:
    """Format a datetime or date into a friendly, readable string."""
    if dt is None:
        return "N/A"
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%A, %B %d %Y  %I:%M %p")
    return dt.strftime("%A, %B %d %Y")


def fmt_pct(value) -> str:
    """Format a percentage value."""
    if value is None:
        return "N/A"
    return f"{round(value)}%"


def fmt(value) -> str:
    """Safely stringify any value (enums, None, etc.)."""
    if value is None:
        return "N/A"
    return str(value)


def yn(value) -> str:
    """Yes/No for booleans (None -> N/A)."""
    if value is None:
        return "N/A"
    return "Yes" if value else "No"


def section(title: str) -> None:
    width = 60
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def sub(title: str) -> None:
    print(f"\n  ── {title} " + "─" * max(0, 44 - len(title)))


# ── CSV logging helpers ──────────────────────────────────────────────────────
def _num(value):
    """Numeric value for CSV (rounded to int for percentages); '' if None."""
    return "" if value is None else value


def _to_local(dt):
    """Return a timezone-aware/naive datetime expressed in local time."""
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone()
    return dt


def status_log_path(dt: datetime) -> Path:
    return Path(LOG_DIR) / f"litter_robot_logs_{dt.month:02d}-{dt.year}.csv"


def usage_log_path(dt: datetime) -> Path:
    return Path(LOG_DIR) / f"litter_robot_usage_logs_{dt.month:02d}_{dt.year}.csv"


def split_action(text: str) -> "tuple[str, str]":
    """Split an activity string into (Activity, Value).

    'Pet Weight Recorded: 16.2 lbs' -> ('Pet Weight Recorded', '16.2 lbs')
    'Clean Cycles: 2328'            -> ('Clean Cycles', '2328')
    'Cat Detected'                  -> ('Cat Detected', '-')
    """
    text = (text or "").strip()
    if ":" in text:
        label, _, val = text.partition(":")
        val = val.strip()
        return label.strip(), (val if val else "-")
    return text, "-"


def _read_existing(path: Path) -> "list[dict]":
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append_rows(path: Path, fields: "list[str]", rows: "list[dict]") -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _same_status(a: dict, b: dict) -> bool:
    """True if two status rows are identical ignoring the snapshot timestamp."""
    return all(
        str(a.get(k, "")) == str(b.get(k, ""))
        for k in STATUS_FIELDS
        if k != "snapshot_time"
    )


def append_status_rows(rows: "list[dict]") -> int:
    """Append status snapshots, skipping a row when it matches the last logged
    row for that robot (so identical back-to-back snapshots aren't duplicated)."""
    if not rows:
        return 0
    by_file: "dict[Path, list[dict]]" = {}
    for row in rows:
        dt = datetime.strptime(row["snapshot_time"], "%Y-%m-%d %H:%M:%S")
        by_file.setdefault(status_log_path(dt), []).append(row)

    added = 0
    for path, group in by_file.items():
        last_by_robot: "dict[str, dict]" = {}
        for er in _read_existing(path):
            last_by_robot[er.get("robot", "")] = er
        to_write = []
        for row in group:
            prev = last_by_robot.get(row["robot"])
            if prev is not None and _same_status(prev, row):
                continue
            to_write.append(row)
            last_by_robot[row["robot"]] = row
        if to_write:
            _append_rows(path, STATUS_FIELDS, to_write)
            added += len(to_write)
    return added


def append_usage_events(events: "list[dict]") -> int:
    """Append activity events keyed by (Robot, Timestamp, Activity, Value),
    routing each event to the monthly file for its own timestamp and skipping
    any event already present in that file."""
    if not events:
        return 0
    by_file: "dict[Path, list[dict]]" = {}
    for ev in events:
        dt = datetime.strptime(ev["Timestamp"], "%Y-%m-%d %H:%M")
        by_file.setdefault(usage_log_path(dt), []).append(ev)

    added = 0
    for path, group in by_file.items():
        seen = {
            (r.get("Robot", ""), r.get("Timestamp", ""), r.get("Activity", ""), r.get("Value", ""))
            for r in _read_existing(path)
        }
        to_write = []
        for ev in group:
            key = (ev["Robot"], ev["Timestamp"], ev["Activity"], ev["Value"])
            if key in seen:
                continue
            seen.add(key)
            to_write.append(ev)
        if to_write:
            to_write.sort(key=lambda e: e["Timestamp"])
            _append_rows(path, USAGE_FIELDS, to_write)
            added += len(to_write)
    return added


# ── App-run log ──────────────────────────────────────────────────────────────
APPLOG_FILE = "litter_robot_applog.csv"
APPLOG_FIELDS = ["run_time", "duration_sec", "status", "error"]


def _oneline(text: str, limit: int = 500) -> str:
    """Collapse whitespace/newlines and cap length for clean CSV storage."""
    return " ".join(str(text).split())[:limit]


def _append_applog(log_dir: str, start: datetime, status: str, error: str = "") -> None:
    """Append one row recording this execution: when it ran, how long it took,
    whether it succeeded, and the error message if it failed. Never raises."""
    try:
        path = Path(log_dir) / APPLOG_FILE
        new_file = not path.exists()
        duration = (datetime.now() - start).total_seconds()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(APPLOG_FIELDS)
            writer.writerow([
                start.strftime("%Y-%m-%d %H:%M:%S"),
                f"{duration:.1f}",
                status,
                error,
            ])
    except Exception:
        pass  # app-run logging must never break the run


# ── Dashboard generation (from CSV logs only) ────────────────────────────────
# The HTML dashboard is rebuilt from the CSV logs on every run. The old
# litter_robot_code_output_*.txt report is NO LONGER used as a data source.
DASHBOARD_TEMPLATE = "litter_robot_dashboard_template.html"
DASHBOARD_OUTPUT = "litter_robot_dashboard.html"

# Cat profiles (birthday/sex/note are stable and not reliably exposed by the
# API, so they live here). Order is preserved in the dashboard's cat cards.
CAT_PROFILES = [
    ("Mochi",   {"sex": "male",   "birth": "2024-05-27", "note": "Fixed · indoor · wet/dry"}),
    ("Socks",   {"sex": "male",   "birth": "2022-01-10", "note": "Fixed · indoor · the big guy"}),
    ("Mittens", {"sex": "female", "birth": "2024-05-27", "note": "Fixed · indoor · b. 2024"}),
]
ROBOT_ORDER = ["LR4-1", "LR4-2"]
ROBOT_ALIASES = {"LR4-1": 'aka "Vegas Robot 1"', "LR4-2": 'aka "Vegas Litter Robot 2"'}
ROBOT_NIGHTLIGHT = {"LR4-1": "Off", "LR4-2": "Auto"}
DAILY_KEY = {"LR4-1": "daily_lr1", "LR4-2": "daily_lr2"}


def _classify_cat(weight: float) -> str:
    """Identify the cat from a recorded weight (Mittens ≈11, Mochi ≈16, Socks ≈19)."""
    if weight < 13.5:
        return "Mittens"
    if weight < 17.5:
        return "Mochi"
    return "Socks"


def _is_cycle(activity: str) -> bool:
    return (activity or "").strip().lower() == "clean cycle complete"


def _is_weight(activity: str) -> bool:
    return (activity or "").strip().lower() in ("weight recorded", "pet weight recorded")


def _parse_weight(value: str):
    m = re.search(r"([\d.]+)", value or "")
    return float(m.group(1)) if m else None


def _robot_from_filename(fn: str):
    b = os.path.basename(fn).lower()
    if b.startswith("vegas_robot_1_") or b.startswith("lr4-1_activity"):
        return "LR4-1"
    if b.startswith("vegas_litter_robot_2_") or b.startswith("lr4-2_activity"):
        return "LR4-2"
    return None


def _export_date_from_filename(fn: str):
    """The date in a historical export filename is its export date."""
    b = os.path.basename(fn)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\.csv$", b)            # YYYY-MM-DD
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"_(\d{1,2})-(\d{1,2})-(\d{4})\.csv$", b)        # M-D-YYYY
    if m:
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def _parse_hist_ts(raw: str, export_date: datetime):
    """Parse a year-less historical timestamp ('6/17 at 8:44 AM', '7/4 6:22AM',
    '9/10 1:17 am'), inferring the year from the file's export date."""
    raw = (raw or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})\s*(?:at\s*)?(.+)$", raw, re.I)
    if not m:
        return None
    mon, day = int(m.group(1)), int(m.group(2))
    t = m.group(3).strip().upper().replace(" ", "")
    tm = re.match(r"(\d{1,2}):(\d{2})(AM|PM)", t)
    if not tm:
        return None
    hh, mm, ap = int(tm.group(1)), int(tm.group(2)), tm.group(3)
    if ap == "PM" and hh != 12:
        hh += 12
    if ap == "AM" and hh == 12:
        hh = 0
    year = export_date.year
    if (mon, day) > (export_date.month, export_date.day):
        year -= 1
    return datetime(year, mon, day, hh, mm)


def _gather_dashboard_events(log_dir: str):
    """Combine activity events from the new monthly usage logs and the older
    per-export CSVs. New logs win for any date they cover (per-robot cutover at
    the earliest new-log timestamp), so the two timestamp sources never overlap.
    Returns a de-duplicated list of (robot, datetime, activity, value)."""
    new_events, hist_events = [], []

    for fn in glob.glob(os.path.join(log_dir, "litter_robot_usage_logs_*.csv")):
        with open(fn, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    ts = datetime.strptime(r["Timestamp"], "%Y-%m-%d %H:%M")
                except Exception:
                    continue
                new_events.append((r["Robot"], ts, r["Activity"], r.get("Value", "")))

    cutover = {}
    for robot, ts, *_ in new_events:
        if robot not in cutover or ts < cutover[robot]:
            cutover[robot] = ts

    patterns = ["vegas_robot_1_*.csv", "vegas_litter_robot_2_*.csv",
                "lr4-1_activity*.csv", "lr4-2_activity*.csv"]
    files = set()
    for p in patterns:
        files.update(glob.glob(os.path.join(log_dir, p)))
    for fn in files:
        robot = _robot_from_filename(fn)
        exp = _export_date_from_filename(fn)
        if not robot or not exp:
            continue
        with open(fn, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ts = _parse_hist_ts(r.get("Timestamp", ""), exp)
                if ts is None:
                    continue
                if robot in cutover and ts >= cutover[robot]:
                    continue  # this date is covered by the (newer, consistent) logs
                hist_events.append((robot, ts, r.get("Activity", ""), r.get("Value", "")))

    seen, combined = set(), []
    for robot, ts, act, val in new_events + hist_events:
        key = (robot, ts.strftime("%Y-%m-%dT%H:%M"), (act or "").strip().lower(), (val or "").strip())
        if key in seen:
            continue
        seen.add(key)
        combined.append((robot, ts, act, val))
    return combined


def _latest_status(log_dir: str):
    """Most recent status row per robot, across all monthly status logs."""
    latest = {}
    for fn in glob.glob(os.path.join(log_dir, "litter_robot_logs_*.csv")):
        with open(fn, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rb = r.get("robot")
                if rb and (rb not in latest or r["snapshot_time"] > latest[rb]["snapshot_time"]):
                    latest[rb] = r
    return latest


def generate_dashboard(log_dir: str):
    """Rebuild litter_robot_dashboard.html from the CSV logs (deletes the old
    file first). Returns a small info dict, or None if the template is missing."""
    tpl_path = Path(log_dir) / DASHBOARD_TEMPLATE
    if not tpl_path.exists():
        print(f"  Template not found: {tpl_path} — skipping dashboard generation.")
        return None
    template = tpl_path.read_text(encoding="utf-8")

    events = _gather_dashboard_events(log_dir)
    status = _latest_status(log_dir)

    # Daily clean-cycle counts (continuous date axis, gaps filled with 0).
    cycle_dates = sorted({ts.date() for _, ts, a, _ in events if _is_cycle(a)})
    daily = {"LR4-1": {}, "LR4-2": {}}
    for rb, ts, a, _ in events:
        if _is_cycle(a) and rb in daily:
            daily[rb][ts.date()] = daily[rb].get(ts.date(), 0) + 1

    def series(rb):
        out = []
        if not cycle_dates:
            return out
        d = cycle_dates[0]
        while d <= cycle_dates[-1]:
            out.append({"date": d.strftime("%Y-%m-%d"), "cycles": daily[rb].get(d, 0)})
            d = d + timedelta(days=1)
        return out

    daily_lr1, daily_lr2 = series("LR4-1"), series("LR4-2")

    # Visits (weight recordings); cat identified by weight.
    visits, latest_w = [], {}
    for rb, ts, a, v in events:
        if not _is_weight(a):
            continue
        w = _parse_weight(v)
        if w is None:
            continue
        cat = _classify_cat(w)
        visits.append({"dt": ts.strftime("%Y-%m-%dT%H:%M"), "weight": round(w, 1),
                       "cat": cat, "robot": rb})
        if cat not in latest_w or ts > latest_w[cat][0]:
            latest_w[cat] = (ts, w)
    visits.sort(key=lambda e: e["dt"], reverse=True)

    # Robot status cards (from the latest status snapshot per robot).
    robots, n_online, scoops_total = [], 0, 0
    for rb in ROBOT_ORDER:
        s = status.get(rb)
        if not s:
            continue
        online = (s.get("online") == "Yes")
        n_online += 1 if online else 0
        try:
            scoops_total += int(s.get("scoops_saved") or 0)
        except ValueError:
            pass
        robots.append({
            "name": rb, "alias": ROBOT_ALIASES.get(rb, ""),
            "litter": int(float(s.get("litter_pct") or 0)),
            "drawer": int(float(s.get("waste_drawer_pct") or 0)),
            "online": online, "drawerFull": (s.get("drawer_full") == "Yes"),
            "cycles": f"{int(s.get('cycle_count') or 0):,}",
            "nightlight": ROBOT_NIGHTLIGHT.get(rb, "N/A"), "daily_key": DAILY_KEY[rb],
        })

    # Cat cards.
    def fmt_last(ts):
        h = ((ts.hour + 11) % 12) + 1
        return (f"{ts.strftime('%b')} {ts.day}, {h}:{ts.minute:02d} {ts.strftime('%p')}",
                ts.strftime("%Y-%m-%dT%H:%M"))

    cats = []
    for name, prof in CAT_PROFILES:
        cur, last, last_iso = "", "", ""
        if name in latest_w:
            ts, w = latest_w[name]
            cur = round(w, 2)
            last, last_iso = fmt_last(ts)
        cats.append({"name": name, "sex": prof["sex"], "cur": cur, "birth": prof["birth"],
                     "last": last, "lastISO": last_iso, "note": prof["note"]})

    now = datetime.now()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    gen_str = (f"{now.strftime('%a, %b')} {now.day} {now.year} "
               f"{((now.hour + 11) % 12) + 1}:{now.minute:02d} {now.strftime('%p')}")
    online_str = f"{n_online} / {len(robots)}"
    scoops_str = f"{scoops_total:,}"

    data_json = json.dumps({"daily_lr1": daily_lr1, "daily_lr2": daily_lr2, "visits": visits})

    def robot_js(r):
        return ("{name:%s,alias:%s,litter:%d,drawer:%d,online:%s,drawerFull:%s,"
                "cycles:%s,nightlight:%s,update:false,daily:DATA.%s}" % (
                    json.dumps(r["name"]), json.dumps(r["alias"]), r["litter"], r["drawer"],
                    "true" if r["online"] else "false",
                    "true" if r["drawerFull"] else "false",
                    json.dumps(r["cycles"]), json.dumps(r["nightlight"]), r["daily_key"]))

    robots_js = "[\n " + ",\n ".join(robot_js(r) for r in robots) + "\n]"
    cats_json = json.dumps(cats)

    html = (template
            .replace("__DATA_JSON__", data_json)
            .replace("__ROBOTS_JS__", robots_js)
            .replace("__CATS_JSON__", cats_json)
            .replace("__NOW_ISO__", now_iso)
            .replace("__GENERATED__", gen_str)
            .replace("__ONLINE__", online_str)
            .replace("__SCOOPS__", scoops_str))

    out_path = Path(log_dir) / DASHBOARD_OUTPUT
    if out_path.exists():
        out_path.unlink()  # delete the old html, then write a fresh one
    out_path.write_text(html, encoding="utf-8")

    return {
        "output": str(out_path),
        "visits": len(visits),
        "date_min": cycle_dates[0].strftime("%Y-%m-%d") if cycle_dates else "n/a",
        "date_max": cycle_dates[-1].strftime("%Y-%m-%d") if cycle_dates else "n/a",
    }


async def fetch_cat_detections(robot, days: int):
    """Pull totalCatDetections directly — pylitterbot fetches it but drops it
    from the Insight object, so we query the GraphQL endpoint ourselves."""
    try:
        from pylitterbot.utils import utcnow

        data = await robot._post(  # noqa: SLF001 (intentional internal use)
            json={
                "query": """
                    query GetLR4Insights($serial: String!, $startTimestamp: String) {
                        getLitterRobot4Insights(serial: $serial, startTimestamp: $startTimestamp) {
                            totalCatDetections
                        }
                    }
                """,
                "variables": {
                    "serial": robot.serial,
                    "startTimestamp": (utcnow() - timedelta(days=days)).strftime(
                        "%Y-%m-%dT%H:%M:%S.%fZ"
                    ),
                },
            }
        )
        return (
            data.get("data", {})
            .get("getLitterRobot4Insights", {})
            .get("totalCatDetections")
        )
    except Exception:
        return None


# ── Main ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    from pylitterbot import Account
    from pylitterbot.robot.litterrobot4 import LitterRobot4

    username, password = resolve_credentials()

    run_now = datetime.now()
    run_ts = run_now.strftime("%Y-%m-%d %H:%M:%S")
    status_rows: "list[dict]" = []
    usage_events: "list[dict]" = []

    account = Account()
    try:
        await account.connect(
            username=username,
            password=password,
            load_robots=True,
            load_pets=True,
        )

        # ── Robots ───────────────────────────────────────────────────────────
        section("🤖  LITTER-ROBOT STATUS")
        if not account.robots:
            print("  No robots found on this account.")

        for robot in account.robots:
            print(f"\n  Name        : {robot.name}")
            print(f"  Model       : {robot.model}")
            print(f"  Serial      : {robot.serial}")
            print(f"  Robot ID    : {robot.id}")
            print(f"  Online      : {yn(robot.is_online)}")
            print(f"  Status      : {robot.status_text}  ({fmt(robot.status_code)})")
            print(f"  Power       : {'On' if robot.is_on else 'Off'}")
            print(f"  Onboarded   : {yn(robot.is_onboarded)}")
            print(f"  Setup       : {fmt_dt(robot.setup_date)}")
            print(f"  Last Seen   : {fmt_dt(robot.last_seen)}")
            print(f"  Firmware    : {robot.firmware}")

            if not isinstance(robot, LitterRobot4):
                continue

            # ── Levels & Cycles ──────────────────────────────────────────────
            sub("Levels & Cycles")
            print(f"  Litter Level         : {fmt_pct(robot.litter_level)}  ({fmt(robot.litter_level_state)})")
            try:
                print(f"  Litter Level (calc)  : {fmt_pct(robot.litter_level_calculated)}")
            except Exception:
                pass
            print(f"  Waste Drawer         : {fmt_pct(robot.waste_drawer_level)}")
            print(f"  Drawer Full?         : {yn(robot.is_waste_drawer_full)}")
            print(f"  Drawer Full Indicator: {yn(robot.is_drawer_full_indicator_triggered)}")
            print(f"  Cycle Count          : {robot.cycle_count:,}")
            print(f"  Cycle Capacity       : {robot.cycle_capacity}")
            print(f"  Cycles After Full    : {robot.cycles_after_drawer_full}")
            print(f"  Scoops Saved         : {robot.scoops_saved_count:,}")
            print(f"  Last Pet Weight      : {robot.pet_weight} lbs")
            print(f"  Clean Wait Time      : {robot.clean_cycle_wait_time_minutes} min")

            # ── Configuration ────────────────────────────────────────────────
            sub("Configuration")
            print(f"  Timezone        : {fmt(robot.timezone)}")
            print(f"  Surface Type    : {fmt(robot.surface_type)}")
            print(f"  Power Type      : {fmt(robot.power_type)}  (AC=mains, DC=battery backup)")
            print(f"  Panel Locked    : {yn(robot.panel_lock_enabled)}")
            print(f"  Panel Brightness: {fmt(robot.panel_brightness)}")
            print(f"  Night Light Mode: {fmt(robot.night_light_mode)}  (enabled: {yn(robot.night_light_mode_enabled)})")
            print(f"  Night Light Lvl : {fmt(robot.night_light_level)}  ({robot.night_light_brightness})")

            # ── LitterHopper (auto-refill accessory) ─────────────────────────
            sub("LitterHopper")
            print(f"  Hopper Status   : {fmt(robot.hopper_status)}")
            print(f"  Hopper Removed  : {yn(robot.is_hopper_removed)}")

            # ── Sleep Mode ───────────────────────────────────────────────────
            sub("Sleep Mode")
            print(f"  Schedule Enabled: {yn(robot.sleep_mode_enabled)}")
            print(f"  Currently Asleep: {yn(robot.is_sleeping)}")
            if robot.sleep_mode_enabled:
                print(f"  Next Start      : {fmt_dt(robot.sleep_mode_start_time)}")
                print(f"  Next End        : {fmt_dt(robot.sleep_mode_end_time)}")
                if robot.sleep_schedule is not None:
                    print(f"  Weekly Schedule : {robot.sleep_schedule}")

            # ── Diagnostics / Faults ─────────────────────────────────────────
            sub("Diagnostics")
            print(f"  Globe Motor Fault   : {fmt(robot.globe_motor_fault_status)}")
            print(f"  Globe Retract Fault : {fmt(robot.globe_motor_retract_fault_status)}")
            print(f"  USB Fault           : {fmt(robot.usb_fault_status)}")
            print(f"  Wi-Fi Mode          : {fmt(robot.wifi_mode_status)}")

            # Capture a STATUS snapshot row for the status log.
            status_rows.append({
                "snapshot_time": run_ts,
                "robot": robot.name,
                "serial": robot.serial,
                "model": robot.model,
                "online": yn(robot.is_online),
                "status": robot.status_text,
                "litter_pct": "" if robot.litter_level is None else round(robot.litter_level),
                "litter_state": fmt(robot.litter_level_state),
                "waste_drawer_pct": "" if robot.waste_drawer_level is None else round(robot.waste_drawer_level),
                "drawer_full": yn(robot.is_waste_drawer_full),
                "cycle_count": _num(robot.cycle_count),
                "cycle_capacity": _num(robot.cycle_capacity),
                "cycles_after_full": _num(robot.cycles_after_drawer_full),
                "scoops_saved": _num(robot.scoops_saved_count),
                "last_pet_weight_lbs": _num(robot.pet_weight),
                "clean_wait_min": _num(robot.clean_cycle_wait_time_minutes),
                "sleep_active": yn(robot.sleep_mode_enabled),
                "hopper_status": fmt(robot.hopper_status),
                "globe_motor_fault": fmt(robot.globe_motor_fault_status),
                "usb_fault": fmt(robot.usb_fault_status),
                "wifi_mode": fmt(robot.wifi_mode_status),
                "firmware": fmt(robot.firmware),
            })

            # ── Firmware update availability ─────────────────────────────────
            sub("Firmware")
            print(f"  Update Status   : {fmt(robot.firmware_update_status)}")
            print(f"  Update Triggered: {yn(robot.firmware_update_triggered)}")
            try:
                has_update = await robot.has_firmware_update()
                print(f"  Update Available: {yn(has_update)}")
                if has_update:
                    print(f"  Latest Firmware : {await robot.get_latest_firmware()}")
            except Exception as exc:
                print(f"  Update check failed: {exc}")

            # ── Activity History (FULL archive) ──────────────────────────────
            section(f"📋  ACTIVITY HISTORY  —  {robot.name}  (up to {ACTIVITY_LIMIT:,} events)")
            try:
                activities = await robot.get_activity_history(limit=ACTIVITY_LIMIT)
                if not activities:
                    print("  No activity recorded yet.")
                else:
                    print(f"  Retrieved {len(activities):,} events.\n")
                    for i, act in enumerate(activities, 1):
                        action = (
                            act.action.text
                            if hasattr(act.action, "text")
                            else str(act.action)
                        )
                        print(f"  {i:>4}. {fmt_dt(act.timestamp):<38}  {action}")

                        # Capture the event for the usage log.
                        local_ts = _to_local(act.timestamp)
                        if local_ts is not None:
                            activity, value = split_action(action)
                            usage_events.append({
                                "Robot": robot.name,
                                "Activity": activity,
                                "Timestamp": local_ts.strftime("%Y-%m-%d %H:%M"),
                                "Value": value,
                            })
            except Exception as exc:
                print(f"  Could not fetch activity history: {exc}")

            # ── Usage Insight ────────────────────────────────────────────────
            section(f"📊  USAGE INSIGHT (last {INSIGHT_DAYS} days)  —  {robot.name}")
            try:
                insight = await robot.get_insight(days=INSIGHT_DAYS)
                cat_detections = await fetch_cat_detections(robot, INSIGHT_DAYS)
                print(f"  Total Cycles    : {insight.total_cycles:,}")
                print(f"  Cat Detections  : {fmt(cat_detections)}")
                print(f"  Days Tracked    : {insight.total_days}")
                print(f"  Avg / Day       : {insight.average_cycles:.1f} cycles")
                print()
                print(f"  {'Date':<22}  Cycles")
                print(f"  {'─' * 22}  ──────")
                for day_date, count in insight.cycle_history:
                    bar = "█" * count
                    print(f"  {fmt_dt(day_date):<22}  {count:>3}  {bar}")
            except Exception as exc:
                print(f"  Could not fetch usage insight: {exc}")

        # ── Pets ─────────────────────────────────────────────────────────────
        section("🐱  PETS")
        if not account.pets:
            print("  No pets found on this account.")

        for pet in account.pets:
            print(f"\n  Name            : {pet.name}")
            print(f"  Pet ID          : {pet.id}")
            print(f"  Type            : {fmt(pet.pet_type)}")
            print(f"  Gender          : {fmt(pet.gender)}")
            print(f"  Breeds          : {fmt(pet.breeds)}")
            print(f"  Birthday        : {fmt_dt(pet.birthday)}")
            print(f"  Adoption Date   : {fmt_dt(pet.adoption_date)}")
            print(f"  Age             : {fmt(pet.age)}")
            print(f"  Fixed           : {yn(pet.is_fixed)}")
            print(f"  Diet            : {fmt(pet.diet)}")
            print(f"  Environment     : {fmt(pet.environment_type)}")
            print(f"  Health Concerns : {fmt(pet.health_concerns)}")
            print(f"  Healthy         : {yn(pet.is_healthy)}")
            print(f"  Active Profile  : {yn(pet.is_active)}")
            print(f"  Weight ID Enabled: {yn(pet.weight_id_feature_enabled)}")
            print(f"  Pet Tag ID      : {fmt(pet.pet_tag_id)}")
            print(f"  Image URL       : {fmt(pet.image_url)}")
            print(f"  Estimated Weight: {fmt(pet.estimated_weight)} lbs")
            print(f"  Last Weight Read: {fmt(pet.last_weight_reading)} lbs")
            print(f"  Current Weight  : {fmt(pet.weight)} lbs")

            # Weight history (also powers last-weighed time + visit counts)
            try:
                weight_history = await pet.fetch_weight_history(limit=WEIGHT_HISTORY_LIMIT)
            except Exception as exc:
                weight_history = []
                print(f"  Could not fetch weight history: {exc}")

            # FIX: last_weight_reading is a float, not an object. Derive the
            # last-weighed timestamp from the weight history instead.
            if weight_history:
                latest = max(weight_history, key=lambda w: w.timestamp)
                print(f"  Last Weighed    : {fmt_dt(latest.timestamp)}  ({latest.weight} lbs)")

            # Visit counts derived from weight history
            try:
                now = datetime.now(tz=timezone.utc)
                print(f"  Visits (24h)    : {pet.get_visits_since(now - timedelta(days=1))}")
                print(f"  Visits (7d)     : {pet.get_visits_since(now - timedelta(days=7))}")
                print(f"  Visits (30d)    : {pet.get_visits_since(now - timedelta(days=30))}")
            except Exception:
                pass

            if weight_history:
                print(f"\n  ── Weight History ({len(weight_history)} readings) ────────")
                print(f"  {'Date & Time':<38}  Weight")
                print(f"  {'─' * 38}  ──────")
                for wt in sorted(weight_history, key=lambda w: w.timestamp):
                    print(f"  {fmt_dt(wt.timestamp):<38}  {wt.weight} lbs")

        # ── Append to CSV logs (de-duplicated) ────────────────────────────────
        section("🗒  CSV LOG UPDATE")
        try:
            n_status = append_status_rows(status_rows)
            n_usage = append_usage_events(usage_events)
            print(f"  Log directory          : {os.path.abspath(LOG_DIR)}")
            print(f"  Status snapshots pulled : {len(status_rows)}  → appended {n_status} new")
            print(f"  Activity events pulled  : {len(usage_events)}  → appended {n_usage} new")
        except Exception as exc:
            print(f"  Could not write CSV logs: {exc}")

        # ── Rebuild the HTML dashboard from the CSV logs ──────────────────────
        section("📈  DASHBOARD")
        try:
            info = generate_dashboard(LOG_DIR)
            if info:
                print(f"  Rebuilt: {info['output']}")
                print(f"  History : {info['date_min']} → {info['date_max']}  ·  {info['visits']:,} visits plotted")
                print(f"  Source  : CSV logs + historical exports (txt report no longer used)")
        except Exception as exc:
            print(f"  Could not generate dashboard: {exc}")

        print(f"\n{'─' * 60}")
        print(f"  Report generated: {fmt_dt(datetime.now())}")
        print(f"{'─' * 60}\n")

    finally:
        await account.disconnect()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Litter-Robot 4 monitor — full extraction + monthly CSV logging."
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not auto-install missing dependencies; exit with instructions instead.",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory for the CSV logs (default: the folder containing this script).",
    )
    return parser.parse_args(argv)


def run() -> None:
    global LOG_DIR
    args = parse_args()

    # Resolve where logs are written.
    if args.log_dir:
        LOG_DIR = args.log_dir
    else:
        try:
            LOG_DIR = str(Path(__file__).resolve().parent)
        except NameError:
            LOG_DIR = os.getcwd()
    os.makedirs(LOG_DIR, exist_ok=True)

    # Record every execution (time, duration, ok/error) to litter_robot_applog.csv.
    start = datetime.now()
    status, error = "ok", ""
    try:
        # Make sure third-party deps (pylitterbot) are available before we import them.
        ensure_dependencies(auto_install=not args.no_install)

        # On Windows, aiohttp's default Proactor loop can raise noisy "Event loop is
        # closed" errors on shutdown; the Selector loop avoids that.
        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except AttributeError:
                pass

        asyncio.run(main())
    except BaseException as exc:
        status = "error"
        error = _oneline(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _append_applog(LOG_DIR, start, status, error)


if __name__ == "__main__":
    run()
