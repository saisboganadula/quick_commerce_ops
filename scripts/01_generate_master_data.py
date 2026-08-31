"""Generate and validate the foundation tables for the project.

Outputs
-------
data/raw/stores.csv
data/raw/time_intervals.csv
data/validation/master_data_validation.csv

Run this file from the quick_commerce_ops project directory:
    python3 scripts/01_generate_master_data.py
"""

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


# The script is expected to live in quick_commerce_ops/scripts/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)


def build_stores() -> pd.DataFrame:
    """Return one row per dark store.

    Base_Daily_Orders is a simulation baseline, not a fixed daily result.
    Actual demand will be generated in a later script.
    """

    return pd.DataFrame(
        {
            "Store_ID": ["DS001", "DS002", "DS003", "DS004", "DS005"],
            "Store_Name": [
                "Madhapur",
                "Gachibowli",
                "Kondapur",
                "Kukatpally",
                "Banjara Hills",
            ],
            "City": ["Hyderabad"] * 5,
            "Store_Size_Band": ["Large", "Large", "Medium", "Large", "Medium"],
            "Base_Daily_Orders": [2300, 2100, 1900, 2200, 1700],
            "Delivery_Radius_KM": [2.5, 3.0, 2.8, 3.2, 2.4],
            "Operating_Hours": [24] * 5,
            "Active_Flag": [True] * 5,
        }
    )


def classify_daypart(hour: int) -> str:
    """Map a clock hour to an operational daypart."""

    if 0 <= hour < 6:
        return "Night"
    if 6 <= hour < 11:
        return "Morning"
    if 11 <= hour < 15:
        return "Lunch"
    if 15 <= hour < 18:
        return "Afternoon"
    if 18 <= hour < 23:
        return "Evening"
    return "Late Night"


def is_default_peak(hour: int) -> bool:
    """Return the baseline peak flag.

    This is only a starting assumption. Actual peak demand will later depend on
    weekday, weather, promotions, store profile, and random variation.
    """

    return 12 <= hour < 14 or 18 <= hour < 22


def build_time_intervals() -> pd.DataFrame:
    """Return the 48 repeating 30-minute intervals in a 24-hour day."""

    records = []
    start_of_day = datetime(2000, 1, 1, 0, 0)

    for interval_number in range(48):
        start = start_of_day + timedelta(minutes=30 * interval_number)
        end = start + timedelta(minutes=30)

        records.append(
            {
                "Interval_ID": interval_number + 1,
                "Start_Time": start.strftime("%H:%M:%S"),
                "End_Time": end.strftime("%H:%M:%S"),
                "Start_Minute": interval_number * 30,
                "End_Minute": (interval_number + 1) * 30,
                "Hour_Number": start.hour,
                "Daypart": classify_daypart(start.hour),
                "Default_Peak_Flag": is_default_peak(start.hour),
            }
        )

    return pd.DataFrame(records)


def validation_row(
    table: str,
    test: str,
    passed: bool,
    failure_count: int,
) -> dict:
    """Create one standardized validation-result record."""

    return {
        "Table": table,
        "Validation_Test": test,
        "Passed": bool(passed),
        "Failure_Count": int(failure_count),
    }


def validate_stores(stores: pd.DataFrame) -> pd.DataFrame:
    """Run structural and business-rule checks on Stores."""

    required_columns = [
        "Store_ID",
        "Store_Name",
        "City",
        "Store_Size_Band",
        "Base_Daily_Orders",
        "Delivery_Radius_KM",
        "Operating_Hours",
        "Active_Flag",
    ]

    missing_columns = [column for column in required_columns if column not in stores]
    null_count = (
        stores[[column for column in required_columns if column in stores]]
        .isna()
        .sum()
        .sum()
    )
    invalid_order_count = (~stores["Base_Daily_Orders"].between(1700, 2300)).sum()
    invalid_radius_count = (~stores["Delivery_Radius_KM"].between(1, 5)).sum()

    tests = [
        validation_row("Stores", "Row count equals 5", len(stores) == 5, abs(5 - len(stores))),
        validation_row(
            "Stores",
            "Required columns exist",
            not missing_columns,
            len(missing_columns),
        ),
        validation_row(
            "Stores",
            "Store_ID is unique",
            stores["Store_ID"].is_unique,
            stores["Store_ID"].duplicated().sum(),
        ),
        validation_row(
            "Stores",
            "Store_Name is unique",
            stores["Store_Name"].is_unique,
            stores["Store_Name"].duplicated().sum(),
        ),
        validation_row("Stores", "Required values are populated", null_count == 0, null_count),
        validation_row(
            "Stores",
            "Daily-order baselines are between 1700 and 2300",
            invalid_order_count == 0,
            invalid_order_count,
        ),
        validation_row(
            "Stores",
            "Delivery radii are between 1 and 5 km",
            invalid_radius_count == 0,
            invalid_radius_count,
        ),
        validation_row(
            "Stores",
            "All stores are in Hyderabad",
            stores["City"].eq("Hyderabad").all(),
            (~stores["City"].eq("Hyderabad")).sum(),
        ),
        validation_row(
            "Stores",
            "All stores operate 24 hours",
            stores["Operating_Hours"].eq(24).all(),
            (~stores["Operating_Hours"].eq(24)).sum(),
        ),
    ]

    return pd.DataFrame(tests)


def validate_time_intervals(intervals: pd.DataFrame) -> pd.DataFrame:
    """Run structural and continuity checks on Time Intervals."""

    duplicate_id_count = intervals["Interval_ID"].duplicated().sum()
    invalid_duration_count = (
        (intervals["End_Minute"] - intervals["Start_Minute"]) != 30
    ).sum()
    continuity_breaks = (
        intervals["Start_Minute"].iloc[1:].reset_index(drop=True)
        != intervals["End_Minute"].iloc[:-1].reset_index(drop=True)
    ).sum()
    invalid_start_count = (~intervals["Start_Minute"].between(0, 1410)).sum()
    invalid_end_count = (~intervals["End_Minute"].between(30, 1440)).sum()

    tests = [
        validation_row(
            "Time Intervals",
            "Row count equals 48",
            len(intervals) == 48,
            abs(48 - len(intervals)),
        ),
        validation_row(
            "Time Intervals",
            "Interval_ID is unique",
            duplicate_id_count == 0,
            duplicate_id_count,
        ),
        validation_row(
            "Time Intervals",
            "Every interval is 30 minutes",
            invalid_duration_count == 0,
            invalid_duration_count,
        ),
        validation_row(
            "Time Intervals",
            "Intervals are continuous",
            continuity_breaks == 0,
            continuity_breaks,
        ),
        validation_row(
            "Time Intervals",
            "Start minutes are within the day",
            invalid_start_count == 0,
            invalid_start_count,
        ),
        validation_row(
            "Time Intervals",
            "End minutes cover no more than 24 hours",
            invalid_end_count == 0,
            invalid_end_count,
        ),
        validation_row(
            "Time Intervals",
            "First interval starts at minute 0",
            intervals["Start_Minute"].iloc[0] == 0,
            int(intervals["Start_Minute"].iloc[0] != 0),
        ),
        validation_row(
            "Time Intervals",
            "Final interval ends at minute 1440",
            intervals["End_Minute"].iloc[-1] == 1440,
            int(intervals["End_Minute"].iloc[-1] != 1440),
        ),
    ]

    return pd.DataFrame(tests)


def main() -> None:
    stores = build_stores()
    time_intervals = build_time_intervals()

    validation_report = pd.concat(
        [
            validate_stores(stores),
            validate_time_intervals(time_intervals),
        ],
        ignore_index=True,
    )

    print("\nSTORES")
    print(stores.to_string(index=False))

    print("\nTIME INTERVAL SAMPLE")
    print(time_intervals.head(6).to_string(index=False))
    print("...")
    print(time_intervals.tail(3).to_string(index=False))

    print("\nVALIDATION REPORT")
    for row in validation_report.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(
            f"{status} | {row.Table} | {row.Validation_Test} "
            f"| Failures: {row.Failure_Count}"
        )

    if not validation_report["Passed"].all():
        raise ValueError("Master-data validation failed. Review the report above.")

    stores.to_csv(RAW_DATA_DIR / "stores.csv", index=False)
    time_intervals.to_csv(RAW_DATA_DIR / "time_intervals.csv", index=False)
    validation_report.to_csv(
        VALIDATION_DIR / "master_data_validation.csv",
        index=False,
    )

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "stores.csv")
    print(RAW_DATA_DIR / "time_intervals.csv")
    print(VALIDATION_DIR / "master_data_validation.csv")
    print(f"\nNetwork baseline daily orders: {stores['Base_Daily_Orders'].sum():,}")


if __name__ == "__main__":
    main()
