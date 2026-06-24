# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-script data pipeline that monitors two Litter-Robot 4 units (**LR4-1**, **LR4-2**)
via the Whisker cloud API (`pylitterbot`), logs to CSV, and regenerates a self-contained
HTML dashboard. There is **no server, no database, no build step, and no test suite** —
everything runs from one Python script that appends to CSVs and rewrites an HTML file.

## Commands

```bash
# One-time setup: create the virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run the monitor (fetch → append CSVs → rebuild dashboard). Run from repo root
# with the virtual environment active.
python code/litter_robot_v1.py --log-dir live_logs

# Also capture the console report
python code/litter_robot_v1.py --log-dir live_logs > report.txt
```

- **Dependencies**: the only third-party package is `pylitterbot`, declared in
  `requirements.txt` and installed into a virtual environment (`.venv/`, gitignored).
  The script does **not** auto-install — `check_dependencies()` exits with venv setup
  instructions if `pylitterbot` is missing.
- **Credentials**: read from `WHISKER_USERNAME` / `WHISKER_PASSWORD` env vars (falls back to
  an interactive prompt only when a TTY is attached; unattended runs without the vars exit
  immediately). Never hard-code these.
- **No lint/test tooling is configured.** Verify changes by running the script and inspecting
  the regenerated `litter_robot_dashboard.html` and the CSV diffs.

## Architecture: the data pipeline

The flow inside `code/litter_robot_v1.py` is: **Whisker API → in-memory rows → de-duplicated
CSV append → dashboard rebuilt from CSVs.** Key stages:

1. **`main()`** connects via `pylitterbot.Account`, prints a human report, and accumulates
   two in-memory lists: `status_rows` (one snapshot per robot) and `usage_events` (one row
   per activity-feed event). `fetch_cat_detections()` bypasses pylitterbot to hit the GraphQL
   endpoint directly because the library drops `totalCatDetections` from its Insight object.
2. **`append_status_rows()` / `append_usage_events()`** write to monthly CSVs with
   de-duplication: status rows are skipped when identical to the robot's last logged row
   (ignoring timestamp); usage events are keyed by `(Robot, Timestamp, Activity, Value)`.
   This is what makes the script safe to run hourly without creating duplicate rows.
3. **`generate_dashboard()`** deletes and rewrites `litter_robot_dashboard.html` from the CSVs
   on every run (the old `.txt` report is no longer a data source).

### The single-directory assumption (most important gotcha)

`generate_dashboard()` reads the template, the status logs, the usage logs, **and** the
historical exports all from the same `--log-dir`. In this repo those are split across
`live_logs/`, `historical_exports/`, and `dashboards/`. So:

- The organized folder layout in the repo is for human/GitHub readability.
- The actual running deployment is **flatter** — template, logs, exports, and output all in
  directories the script can see together. `schedule_litter_robot.ps1` finds this project
  root automatically from its own location (override with `-WorkDir`).
- If you run `--log-dir live_logs` against this repo as-is, dashboard generation is **skipped**
  because the template lives in `dashboards/`, not `live_logs/`. Account for this before
  assuming the dashboard regenerates locally.

### Historical-data merge logic

`_gather_dashboard_events()` unifies two timestamp sources that must never overlap:

- **New monthly usage logs** (`litter_robot_usage_logs_MM_YYYY.csv`) — full, consistent timestamps.
- **Old manual exports** (`historical_exports/*.csv`) — year-less timestamps like `6/17 at 8:44 AM`.
  The robot identity comes from the **filename** (`lr4-1_*`/`vegas_robot_1_*` → LR4-1), and the
  year is inferred from the export date in the filename via `_parse_hist_ts`.

To avoid double-counting, a **per-robot cutover** is computed as that robot's earliest new-log
timestamp; any historical event at/after the cutover is dropped in favor of the newer logs.
Final results are de-duplicated by `(robot, minute-precision timestamp, activity, value)`.

### Cat identification by weight

There is no per-cat identifier in the activity feed, so `_classify_cat()` maps a recorded
weight to a cat by band: `<13.5 → Mittens`, `<17.5 → Mochi`, else `Socks`. Per-cat metadata
that the API does not expose (birthday, sex, notes) is hard-coded in `CAT_PROFILES`. If a
cat's weight changes enough to cross a band, this classification breaks — adjust the thresholds.

### Dashboard templating

`generate_dashboard()` does plain string replacement of `__PLACEHOLDER__` tokens in
`dashboards/litter_robot_dashboard_template.html`:
`__DATA_JSON__`, `__ROBOTS_JS__`, `__CATS_JSON__`, `__NOW_ISO__`, `__GENERATED__`,
`__ONLINE__`, `__SCOOPS__`. The template loads **Chart.js from a CDN**, so the generated
dashboard needs internet to render charts (the data itself is baked in). To restyle the
dashboard, edit the template — not the generated output, which is overwritten every run.

## Conventions and quirks

- **Monthly filename inconsistency**: status logs use a dash (`litter_robot_logs_MM-YYYY.csv`),
  usage logs use underscores (`litter_robot_usage_logs_MM_YYYY.csv`). Match the existing pattern
  when adding code that globs these.
- **Robot renames**: LR4-1 was "Vegas Robot 1"; LR4-2 was "Vegas Litter Robot 2". Older export
  filenames use the former names; `_robot_from_filename()` maps both.
- **`cat_health_dashboard.html` is NOT generated by the script** — it is a standalone, hand-built
  file with data baked in (no placeholders, not referenced in Python). Don't expect the monitor
  to update it.
- **`litter_robot_applog.csv`** records every execution (time, duration, ok/error) and is written
  in a `finally` block that never raises — a run log, separate from the data logs.
- **`_to_delete/`** holds the deprecated predecessors (`litter_robot_monitor.py`, the Jupyter
  notebook, a `broken_git/` directory). It is tracked in git but is legacy; do not build on it.
- **Scheduling is Windows-only** (`code/*.ps1` / `*.bat` register a `LitterRobotHourly` Task
  Scheduler job). The Python script itself is cross-platform; only the scheduling wrappers are
  Windows-specific. `schedule_litter_robot.ps1` auto-detects the project root from its own
  location (override with `-WorkDir`), so it no longer hard-codes a deployment path.
