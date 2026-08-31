"""Generate workers and 60 days of scheduled/actual shifts.

Prerequisites: run scripts 01, 02, and 03 first.

Outputs:
    data/raw/workers.csv
    data/raw/worker_shifts.csv
    data/validation/workforce_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/04_generate_workers_and_shifts.py
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd


SEED = 44

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

STORES_FILE = RAW_DATA_DIR / "stores.csv"
CALENDAR_FILE = RAW_DATA_DIR / "calendar.csv"
DAILY_DEMAND_FILE = RAW_DATA_DIR / "daily_store_demand.csv"


# Editable simulation assumptions - not confirmed operating facts.
PICKER_HEADCOUNT = {
    "DS001": 36,
    "DS002": 34,
    "DS003": 32,
    "DS004": 35,
    "DS005": 30,
}

RIDER_HEADCOUNT = {
    "DS001": 165,
    "DS002": 155,
    "DS003": 145,
    "DS004": 160,
    "DS005": 135,
}

SHIFT_DEFINITIONS = {
    "Night": {"start_hour": 0, "duration_hours": 9},
    "Morning": {"start_hour": 8, "duration_hours": 9},
    "Evening": {"start_hour": 16, "duration_hours": 9},
}

PICKER_ATTENDANCE_RATE = 0.94
REGULAR_RIDER_ATTENDANCE_RATE = 0.92
GIG_RIDER_ATTENDANCE_RATE = 0.86
GIG_RIDER_DAILY_SCHEDULE_RATE = 0.88
LATE_ARRIVAL_RATE = 0.08
EARLY_LOGOUT_RATE = 0.04

# High-demand store-days receive short picker OD coverage during the evening peak.
SURGE_THRESHOLD_VS_BASE = 1.12
OD_PICKERS_PER_SURGE_STORE = 3
OD_START_HOUR = 18
OD_DURATION_HOURS = 4


def require_files(paths: list[Path]) -> None:
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-03 first."
        )


def allocate_home_shift(worker_number: int) -> str:
    """Distribute workers approximately evenly across three home shifts."""

    shift_names = list(SHIFT_DEFINITIONS)
    return shift_names[(worker_number - 1) % len(shift_names)]


def allocate_experience(rng: np.random.Generator) -> str:
    return rng.choice(
        ["New", "Experienced", "Senior"],
        p=[0.20, 0.60, 0.20],
    )


def build_workers(
    stores: pd.DataFrame,
    simulation_start: pd.Timestamp,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Return one row per simulated picker or rider."""

    records = []

    for store_id in stores["Store_ID"]:
        for number in range(1, PICKER_HEADCOUNT[store_id] + 1):
            records.append(
                {
                    "Worker_ID": f"P_{store_id}_{number:03d}",
                    "Worker_Type": "Picker",
                    "Home_Store_ID": store_id,
                    "Employment_Type": "Regular",
                    "Home_Shift": allocate_home_shift(number),
                    "Experience_Band": allocate_experience(rng),
                    "Join_Date": simulation_start
                    - pd.Timedelta(days=int(rng.integers(30, 900))),
                    "Weekly_Off_Day": int((number - 1) % 7 + 1),
                    "Active_Flag": True,
                }
            )

        for number in range(1, RIDER_HEADCOUNT[store_id] + 1):
            employment_type = rng.choice(["Regular", "Gig"], p=[0.15, 0.85])
            records.append(
                {
                    "Worker_ID": f"R_{store_id}_{number:03d}",
                    "Worker_Type": "Rider",
                    "Home_Store_ID": store_id,
                    "Employment_Type": employment_type,
                    "Home_Shift": allocate_home_shift(number),
                    "Experience_Band": allocate_experience(rng),
                    "Join_Date": simulation_start
                    - pd.Timedelta(days=int(rng.integers(15, 700))),
                    "Weekly_Off_Day": int((number - 1) % 7 + 1),
                    "Active_Flag": True,
                }
            )

    return pd.DataFrame(records)


def shift_datetimes(
    shift_date: pd.Timestamp,
    shift_name: str,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    definition = SHIFT_DEFINITIONS[shift_name]
    start = shift_date.normalize() + pd.Timedelta(hours=definition["start_hour"])
    end = start + pd.Timedelta(hours=definition["duration_hours"])
    return start, end


def should_schedule_worker(
    worker,
    date: pd.Timestamp,
    rng: np.random.Generator,
) -> bool:
    """Apply weekly-off and gig-availability rules."""

    if date.dayofweek + 1 == worker.Weekly_Off_Day:
        return False

    if worker.Worker_Type == "Rider" and worker.Employment_Type == "Gig":
        return bool(rng.random() < GIG_RIDER_DAILY_SCHEDULE_RATE)

    return True


def attendance_rate(worker) -> float:
    if worker.Worker_Type == "Picker":
        return PICKER_ATTENDANCE_RATE
    if worker.Employment_Type == "Regular":
        return REGULAR_RIDER_ATTENDANCE_RATE
    return GIG_RIDER_ATTENDANCE_RATE


def create_attendance_outcome(
    worker,
    scheduled_start: pd.Timestamp,
    scheduled_end: pd.Timestamp,
    rng: np.random.Generator,
) -> dict:
    """Generate actual attendance without contradicting the schedule."""

    if rng.random() >= attendance_rate(worker):
        return {
            "Attendance_Status": "Absent",
            "Actual_Login": pd.NaT,
            "Actual_Logout": pd.NaT,
        }

    late = rng.random() < LATE_ARRIVAL_RATE
    early = rng.random() < EARLY_LOGOUT_RATE

    login_delay = int(rng.integers(10, 46)) if late else 0
    early_minutes = int(rng.integers(15, 61)) if early else 0

    actual_login = scheduled_start + pd.Timedelta(minutes=login_delay)
    actual_logout = scheduled_end - pd.Timedelta(minutes=early_minutes)

    if late and early:
        status = "Late and Early Logout"
    elif late:
        status = "Late"
    elif early:
        status = "Early Logout"
    else:
        status = "Present"

    return {
        "Attendance_Status": status,
        "Actual_Login": actual_login,
        "Actual_Logout": actual_logout,
    }


def build_base_shifts(
    workers: pd.DataFrame,
    calendar: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Create scheduled shifts and observed attendance."""

    records = []
    shift_sequence = 1

    for date in calendar["Date"]:
        for worker in workers.itertuples(index=False):
            if not should_schedule_worker(worker, date, rng):
                continue

            scheduled_start, scheduled_end = shift_datetimes(date, worker.Home_Shift)
            outcome = create_attendance_outcome(
                worker,
                scheduled_start,
                scheduled_end,
                rng,
            )

            records.append(
                {
                    "Shift_ID": f"SH{shift_sequence:08d}",
                    "Worker_ID": worker.Worker_ID,
                    "Worker_Type": worker.Worker_Type,
                    "Store_ID": worker.Home_Store_ID,
                    "Shift_Date": date,
                    "Shift_Name": worker.Home_Shift,
                    "OD_Flag": False,
                    "Scheduled_Start": scheduled_start,
                    "Scheduled_End": scheduled_end,
                    "Actual_Login": outcome["Actual_Login"],
                    "Actual_Logout": outcome["Actual_Logout"],
                    "Attendance_Status": outcome["Attendance_Status"],
                }
            )
            shift_sequence += 1

    return pd.DataFrame(records)


def add_picker_od_shifts(
    base_shifts: pd.DataFrame,
    workers: pd.DataFrame,
    daily_demand: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Add four-hour evening OD shifts on high-demand store-days."""

    surge_days = daily_demand.loc[
        daily_demand["Actual_Orders"]
        > daily_demand["Base_Daily_Orders"] * SURGE_THRESHOLD_VS_BASE
    ]

    od_records = []
    next_number = len(base_shifts) + 1

    pickers = workers.loc[workers["Worker_Type"] == "Picker"]

    for surge in surge_days.itertuples(index=False):
        date = pd.Timestamp(surge.Date)
        store_pickers = pickers.loc[pickers["Home_Store_ID"] == surge.Store_ID]

        already_scheduled = set(
            base_shifts.loc[
                (base_shifts["Store_ID"] == surge.Store_ID)
                & (base_shifts["Shift_Date"] == date),
                "Worker_ID",
            ]
        )
        available = store_pickers.loc[
            ~store_pickers["Worker_ID"].isin(already_scheduled)
        ]

        if available.empty:
            continue

        selected_count = min(OD_PICKERS_PER_SURGE_STORE, len(available))
        selected_indices = rng.choice(
            available.index.to_numpy(),
            size=selected_count,
            replace=False,
        )

        for worker in available.loc[selected_indices].itertuples(index=False):
            start = date.normalize() + pd.Timedelta(hours=OD_START_HOUR)
            end = start + pd.Timedelta(hours=OD_DURATION_HOURS)
            outcome = create_attendance_outcome(worker, start, end, rng)

            od_records.append(
                {
                    "Shift_ID": f"SH{next_number:08d}",
                    "Worker_ID": worker.Worker_ID,
                    "Worker_Type": worker.Worker_Type,
                    "Store_ID": worker.Home_Store_ID,
                    "Shift_Date": date,
                    "Shift_Name": "OD Evening",
                    "OD_Flag": True,
                    "Scheduled_Start": start,
                    "Scheduled_End": end,
                    "Actual_Login": outcome["Actual_Login"],
                    "Actual_Logout": outcome["Actual_Logout"],
                    "Attendance_Status": outcome["Attendance_Status"],
                }
            )
            next_number += 1

    if not od_records:
        return base_shifts

    return pd.concat([base_shifts, pd.DataFrame(od_records)], ignore_index=True)


def calculate_shift_metrics(shifts: pd.DataFrame) -> pd.DataFrame:
    """Derive hours from timestamps rather than generating them independently."""

    shifts = shifts.copy()
    shifts["Scheduled_Hours"] = (
        shifts["Scheduled_End"] - shifts["Scheduled_Start"]
    ).dt.total_seconds() / 3600
    shifts["Logged_Hours"] = (
        shifts["Actual_Logout"] - shifts["Actual_Login"]
    ).dt.total_seconds() / 3600
    shifts["Logged_Hours"] = shifts["Logged_Hours"].fillna(0)
    return shifts


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {
        "Table": table,
        "Validation_Test": test,
        "Passed": bool(passed),
        "Failure_Count": int(failures),
    }


def validate(
    workers: pd.DataFrame,
    shifts: pd.DataFrame,
    stores: pd.DataFrame,
) -> pd.DataFrame:
    invalid_worker_store = (~workers["Home_Store_ID"].isin(stores["Store_ID"])).sum()
    invalid_shift_worker = (~shifts["Worker_ID"].isin(workers["Worker_ID"])).sum()
    invalid_shift_store = (~shifts["Store_ID"].isin(stores["Store_ID"])).sum()
    negative_logged_hours = (shifts["Logged_Hours"] < 0).sum()
    invalid_base_duration = (
        (~shifts["OD_Flag"]) & (shifts["Scheduled_Hours"] != 9)
    ).sum()
    invalid_od_duration = (
        shifts["OD_Flag"] & (shifts["Scheduled_Hours"] != OD_DURATION_HOURS)
    ).sum()
    absent_with_hours = (
        (shifts["Attendance_Status"] == "Absent") & (shifts["Logged_Hours"] != 0)
    ).sum()
    login_before_start = (
        shifts["Actual_Login"].notna()
        & (shifts["Actual_Login"] < shifts["Scheduled_Start"])
    ).sum()
    logout_after_end = (
        shifts["Actual_Logout"].notna()
        & (shifts["Actual_Logout"] > shifts["Scheduled_End"])
    ).sum()

    tests = [
        check("Workers", "Worker_ID is unique", workers["Worker_ID"].is_unique, workers["Worker_ID"].duplicated().sum()),
        check("Workers", "Every home store exists", invalid_worker_store == 0, invalid_worker_store),
        check("Shifts", "Shift_ID is unique", shifts["Shift_ID"].is_unique, shifts["Shift_ID"].duplicated().sum()),
        check("Shifts", "Every Worker_ID exists", invalid_shift_worker == 0, invalid_shift_worker),
        check("Shifts", "Every Store_ID exists", invalid_shift_store == 0, invalid_shift_store),
        check("Shifts", "Base shifts last 9 hours", invalid_base_duration == 0, invalid_base_duration),
        check("Shifts", "OD shifts last 4 hours", invalid_od_duration == 0, invalid_od_duration),
        check("Shifts", "Logged hours are non-negative", negative_logged_hours == 0, negative_logged_hours),
        check("Shifts", "Absent workers have zero logged hours", absent_with_hours == 0, absent_with_hours),
        check("Shifts", "No login occurs before scheduled start", login_before_start == 0, login_before_start),
        check("Shifts", "No logout occurs after scheduled end", logout_after_end == 0, logout_after_end),
    ]
    return pd.DataFrame(tests)


def main() -> None:
    require_files([STORES_FILE, CALENDAR_FILE, DAILY_DEMAND_FILE])

    stores = pd.read_csv(STORES_FILE)
    calendar = pd.read_csv(CALENDAR_FILE, parse_dates=["Date"])
    daily_demand = pd.read_csv(DAILY_DEMAND_FILE, parse_dates=["Date"])
    rng = np.random.default_rng(SEED)

    workers = build_workers(stores, calendar["Date"].min(), rng)
    shifts = build_base_shifts(workers, calendar, rng)
    shifts = add_picker_od_shifts(shifts, workers, daily_demand, rng)
    shifts = calculate_shift_metrics(shifts)
    validation = validate(workers, shifts, stores)

    print("\nWORKER COUNTS")
    print(
        workers.groupby(["Home_Store_ID", "Worker_Type"])
        .size()
        .unstack(fill_value=0)
        .to_string()
    )

    print("\nSHIFT SAMPLE")
    print(shifts.head(10).to_string(index=False))

    print("\nATTENDANCE SUMMARY")
    print(
        shifts.groupby(["Worker_Type", "Attendance_Status"])
        .size()
        .unstack(fill_value=0)
        .to_string()
    )

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(
            f"{status} | {row.Table} | {row.Validation_Test} "
            f"| Failures: {row.Failure_Count}"
        )

    if not validation["Passed"].all():
        raise ValueError("Workforce validation failed. Review the report above.")

    workers.to_csv(RAW_DATA_DIR / "workers.csv", index=False)
    shifts.to_csv(RAW_DATA_DIR / "worker_shifts.csv", index=False)
    validation.to_csv(
        VALIDATION_DIR / "workforce_validation.csv",
        index=False,
    )

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "workers.csv")
    print(RAW_DATA_DIR / "worker_shifts.csv")
    print(VALIDATION_DIR / "workforce_validation.csv")


if __name__ == "__main__":
    main()
