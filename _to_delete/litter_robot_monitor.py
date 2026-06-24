import sys
# In Colab/Jupyter, uncomment the next line to install the dependency:
# !{sys.executable} -m pip install pylitterbot

"""
Litter-Robot 4 Monitor  (full-extraction version)
=================================================
Connects to your Whisker account and dumps *everything* the pylitterbot
API exposes: full device status + diagnostics, the complete activity
archive, usage insights (incl. cat detections), sleep schedule, firmware
update availability, and full pet profiles + weight history.

Credentials are read from environment variables so they are NOT stored in
this file:

    export WHISKER_USERNAME="you@example.com"
    export WHISKER_PASSWORD="your-password"

(On Windows PowerShell:  $env:WHISKER_USERNAME="you@example.com" )

If the variables aren't set, you'll be prompted at runtime.
"""

import asyncio
import os
from datetime import datetime, timezone, timedelta

from pylitterbot import Account
from pylitterbot.robot.litterrobot4 import LitterRobot4

# ── Settings ─────────────────────────────────────────────────────────────────
# How many activity events to pull per robot. The old script used 30; the API
# accepts any limit, so this effectively pulls your whole history. Lower it if
# you only want recent events.
ACTIVITY_LIMIT = 2000
INSIGHT_DAYS = 30
WEIGHT_HISTORY_LIMIT = 100

# ── Credentials (from environment, never hard-coded) ─────────────────────────
USERNAME = os.environ.get("WHISKER_USERNAME")
PASSWORD = os.environ.get("WHISKER_PASSWORD")
if not USERNAME or not PASSWORD:
    try:
        import getpass
        USERNAME = USERNAME or input("Whisker username (email): ").strip()
        PASSWORD = PASSWORD or getpass.getpass("Whisker password: ")
    except Exception:
        raise SystemExit(
            "Set WHISKER_USERNAME and WHISKER_PASSWORD environment variables."
        )


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


async def fetch_cat_detections(robot: LitterRobot4, days: int) -> int | None:
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
    account = Account()
    try:
        await account.connect(
            username=USERNAME,
            password=PASSWORD,
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

        print(f"\n{'─' * 60}")
        print(f"  Report generated: {fmt_dt(datetime.now())}")
        print(f"{'─' * 60}\n")

    finally:
        await account.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

# In Colab/Jupyter, comment out the block above and use:
# await main()
