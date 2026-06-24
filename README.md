# Litter Robot Dashboard

Monitoring, logging, and dashboards for two Litter-Robot 4 units, built on the
[`pylitterbot`](https://pypi.org/project/pylitterbot/) Whisker API.

## Introduction

This repository shares the Litter-Robot dashboard I built to track the litter and
health habits of my cats. A Litter-Robot is an automated, self-cleaning cat litter
box: after a cat uses it, it sifts the waste into a sealed waste drawer, keeping the
box clean and giving each cat a fresh bed of litter. Because every cleaning cycle and
every visit is recorded in the cloud, there's a surprising amount you can learn about
your cats from it — how often they go, how much they weigh, and whether anything looks
off.

The dashboard surfaces two things at a glance. First, the real-time **status** of
each robot — whether it's online, the current litter level, how full the waste
drawer is, cycle counts, and any faults. Second, the cats' **usage and health
trends** over time — visit frequency, cat detections, and recorded weights — drawn
from each robot's activity history. Watching weight and bathroom habits over time can
be an early hint that something's wrong, which is the real reason I started logging
all of this.

I run two Litter-Robot 4 units (**LR4-1** and **LR4-2**), and the data behind the
dashboard comes from the Whisker API via the included monitor script.

## How it works

The whole thing is a small pipeline, and you can follow it end to end:

1. **Collect** — a Python script (`code/litter_robot_v1.py`) logs into the Whisker
   cloud, reads each robot's current status and recent activity, and appends what it
   finds to CSV files.
2. **Schedule** — a Windows scheduled task runs that script automatically on a timer,
   so the history builds up on its own without anyone remembering to run it.
3. **Visualize** — a self-contained HTML dashboard reads that history and turns it
   into easy-to-read cards and charts you can open in any browser.

Nothing here needs a server or a database — it's just a script, some CSV files, and an
HTML page. That keeps it easy to run, back up, and share.

## The robots

The two robots have been renamed over time, so older export files use the previous
names. This table is the key for matching them up:

| Current name | Former name          |
|--------------|----------------------|
| **LR4-1**    | Vegas Robot 1        |
| **LR4-2**    | Vegas Litter Robot 2 |

## Dashboard

![Litter Robot dashboard](dashboards/dashboard_screenshot.png)

<!-- To add the screenshot: open dashboards/litter_robot_dashboard.html in a
     browser, capture it, save as dashboards/dashboard_screenshot.png, and it
     will render here. -->

To see the live dashboard, just open `dashboards/litter_robot_dashboard.html` in any
web browser — no install or internet connection required, since the data is baked
right into the file.

## Folder layout

Everything is sorted into four folders so it's easy to find what you need:

```
.
├── code/                scripts: monitor (litter_robot_v1.py) + Windows scheduling
├── dashboards/          generated HTML dashboards
├── live_logs/           current monthly logs the monitor appends to each run
└── historical_exports/  older raw activity CSVs exported from the Whisker app
```

The short version: **code** is what runs, **dashboards** is what you look at, and the
two log folders are the data. `live_logs/` is the data this project collects on its
own; `historical_exports/` is older data I downloaded by hand from the Whisker app
before the script existed. The scheduled task runs
`code/litter_robot_v1.py --log-dir <...>/live_logs`, so every new row lands in
`live_logs/`.

## What's here

### Code (`code/`)

- **`litter_robot_v1.py`** — the main monitor, and the heart of the project. It
  connects to the Whisker account and pulls down just about everything the robots
  know: full device status and diagnostics, the complete activity archive, usage
  insights (including cat detections), the sleep schedule, firmware status, and each
  pet's profile and weight history. It's built to run over and over (say, every few
  hours): each run appends to the monthly CSV logs and quietly skips anything it has
  already recorded, so you never get duplicate rows no matter how often it runs.

### Scheduling, Windows (`code/`)

These small helper scripts set up and manage the automatic timer. You'll mostly just
double-click them.

- **`schedule_litter_robot.ps1`** — registers the recurring Windows scheduled task.
- **`schedule_tonight.bat`** — double-click to (re-)register the task.
- **`check_schedule.bat`** — dumps the task's status and diagnostics to a text file
  so you can confirm it's actually running.
- **`uninstall_schedule.bat`** — removes the scheduled task.
- **`setup_github.ps1`** — one-time helper that sets up git and pushes to GitHub.

### Dashboards (`dashboards/`)

- **`litter_robot_dashboard.html`** — the main dashboard you'll open day to day.
- **`cat_health_dashboard.html`** — a per-cat view focused on weight and health.
- **`litter_robot_dashboard_template.html`** — the empty template the dashboard is
  built from; handy if you want to restyle or rebuild it.

## CSV reference

If you ever want to open the raw data in Excel or build your own charts, here's what
each file and column means. Don't worry about memorizing this — it's just a lookup.

### `live_logs/` — generated by the monitor

**`litter_robot_logs_MM-YYYY.csv`** — robot status snapshots. The script writes one
row per robot per run, but only when something actually changed since the last
snapshot, so the file stays compact.

| Column | Meaning |
|--------|---------|
| `snapshot_time` | When the snapshot was taken |
| `robot` | LR4-1 or LR4-2 |
| `serial`, `model` | Device serial and model |
| `online`, `status` | Connectivity and current state (e.g. Ready) |
| `litter_pct`, `litter_state` | Litter level % and state (e.g. OPTIMAL) |
| `waste_drawer_pct`, `drawer_full` | Waste drawer fill % and full flag |
| `cycle_count`, `cycle_capacity`, `cycles_after_full` | Clean-cycle counters |
| `scoops_saved` | Lifetime scoops saved |
| `last_pet_weight_lbs` | Most recent measured pet weight (lb) |
| `clean_wait_min` | Clean-cycle wait time (minutes) |
| `sleep_active` | Whether sleep mode is active |
| `hopper_status` | Litter hopper status |
| `globe_motor_fault`, `usb_fault` | Fault indicators |
| `wifi_mode` | Wi-Fi connection mode |
| `firmware` | ESP / PIC / TOF firmware versions |

**`litter_robot_usage_logs_MM_YYYY.csv`** — the activity feed: every cat detection,
weigh-in, and clean cycle, one row per event.

| Column | Meaning |
|--------|---------|
| `Robot` | LR4-1 or LR4-2 |
| `Activity` | Event type (e.g. Cat Detected, Clean Cycle Complete) |
| `Timestamp` | When it occurred |
| `Value` | Associated value (e.g. weight), or `-` if none |

**`litter_robot_applog.csv`** — a simple run journal: one row each time the script
runs, so you can confirm it's working and see how long it took.

| Column | Meaning |
|--------|---------|
| `run_time` | When the run started |
| `duration_sec` | Run duration in seconds |
| `status` | `ok` or `error` |
| `error` | Error detail if the run failed |

### `historical_exports/` — manual exports from the Whisker app

These are older activity exports I downloaded by hand before the script existed. The
robot is identified by the **filename** (`lr4-1_*` / `lr4-2_*`, or the older
`vegas_robot_1_*` / `vegas_litter_robot_2_*`), so the rows themselves don't repeat the
robot name.

| Column | Meaning |
|--------|---------|
| `Activity` | Event type (e.g. Clean Cycle Complete) |
| `Timestamp` | When it occurred |
| `Value` | Associated value, or `-` if none |

## Setup

You only need to do this once. First, install the one library the script depends on:

```bash
pip install pylitterbot
```

Then give the script your Whisker login. It reads these from environment variables so
your password never has to live inside the code (and never ends up in this repo):

```powershell
# Windows PowerShell (current session)
$env:WHISKER_USERNAME="you@example.com"
$env:WHISKER_PASSWORD="your-password"
```

```bash
# macOS / Linux
export WHISKER_USERNAME="you@example.com"
export WHISKER_PASSWORD="your-password"
```

The lines above set the variables for your current terminal session only. To make
them stick for the automatic scheduled task, see the **Scheduled task** section below.

## Run

Once setup is done, run the monitor from the repo root. Each run prints a readable
report and appends any new data to the logs:

```bash
python code/litter_robot_v1.py --log-dir live_logs     # run + append to live_logs/
python code/litter_robot_v1.py --log-dir "C:\path"     # write logs elsewhere
python code/litter_robot_v1.py --log-dir live_logs > report.txt   # also save the console report
```

If you skip `--log-dir`, the script writes the logs next to itself (inside `code/`),
so it's worth pointing it at `live_logs/` as shown.

## Scheduled task (Windows)

Running the script by hand works, but the real value comes from letting it run on its
own so the history fills in around the clock. On Windows that's a scheduled task:

- **Register / re-register:** double-click `code\schedule_tonight.bat` (it runs
  `schedule_litter_robot.ps1` for you). It will ask for administrator rights and your
  Windows password — the password lets the task run *whether or not you're logged on*.
  Re-run this any time you move files, so the task picks up the current
  `code\litter_robot_v1.py` path.
- **Schedule:** the task `LitterRobotHourly` runs once an hour, indefinitely, around the clock.
- **Output:** the task passes `--log-dir ...\live_logs`, so logs land in `live_logs/`.
- **Credentials:** because the task runs on its own (and may run under a separate
  local account), the per-session variables from Setup aren't enough — set them as
  **machine-level** variables so the task can always read them. In an elevated
  PowerShell (Run as administrator):

  ```powershell
  [Environment]::SetEnvironmentVariable("WHISKER_USERNAME","you@example.com","Machine")
  [Environment]::SetEnvironmentVariable("WHISKER_PASSWORD","your-password","Machine")
  ```

  Then reboot (or restart) so Task Scheduler picks up the new machine environment.
- **Check status:** double-click `code\check_schedule.bat` to confirm it's running.
- **Remove:** double-click `code\uninstall_schedule.bat`.

## Security

Credentials are read from environment variables only — there are no secrets in this
repository. Please don't commit passwords, tokens, or API keys: keep them in the
environment variables described above. The `.gitignore` already excludes common
secret and `.env` files as a safety net.
