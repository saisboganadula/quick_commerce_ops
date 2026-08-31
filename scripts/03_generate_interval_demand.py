"""Split every store-day order total across 48 half-hour intervals.

Prerequisites
-------------
Run these first:
    01_generate_master_data.py
    02_generate_daily_demand.py

Outputs
-------
data/raw/interval_demand.csv
data/validation/interval_demand_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/03_generate_interval_demand.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


SEED = 43

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

STORES_FILE = RAW_DATA_DIR / "stores.csv"
INTERVALS_FILE = RAW_DATA_DIR / "time_intervals.csv"
DAILY_DEMAND_FILE = RAW_DATA_DIR / "daily_store_demand.csv"

OUTPUT_FILE = RAW_DATA_DIR / "interval_demand.csv"
VALIDATION_FILE = VALIDATION_DIR / "interval_demand_validation.csv"


def require_files(paths: list[Path]) -> None:
    missing = [path for path in paths if not path.exists()]
    if missing:
        missing_text = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            f"Required input files are missing:\n{missing_text}\n"
            "Run scripts 01 and 02 first."
        )


def gaussian_peak(
    time_hours: np.ndarray,
    center: float,
    width: float,
    strength: float,
) -> np.ndarray:
    """Return a smooth peak around a clock-hour center."""

    return strength * np.exp(-0.5 * ((time_hours - center) / width) ** 2)


def build_base_weights(time_hours: np.ndarray, weekend: bool) -> np.ndarray:
    """Create a general quick-commerce demand curve."""

    # A small overnight baseline keeps a 24-hour store active.
    weights = np.full(len(time_hours), 0.10)

    if weekend:
        weights += gaussian_peak(time_hours, center=10.5, width=1.6, strength=0.75)
        weights += gaussian_peak(time_hours, center=14.0, width=1.8, strength=0.90)
        weights += gaussian_peak(time_hours, center=20.0, width=2.0, strength=1.35)
    else:
        weights += gaussian_peak(time_hours, center=8.5, width=1.3, strength=0.55)
        weights += gaussian_peak(time_hours, center=13.0, width=1.5, strength=0.90)
        weights += gaussian_peak(time_hours, center=20.0, width=1.8, strength=1.45)

    return weights


def apply_store_profile(
    weights: np.ndarray,
    time_hours: np.ndarray,
    store_id: str,
    weekend: bool,
) -> np.ndarray:
    """Adjust the common curve for each store's simulated catchment."""

    adjusted = weights.copy()

    if store_id == "DS001":  # Madhapur: office lunch and evening demand
        adjusted += gaussian_peak(time_hours, 13.0, 1.2, 0.30 if not weekend else 0.10)
        adjusted += gaussian_peak(time_hours, 20.0, 1.4, 0.15)
    elif store_id == "DS002":  # Gachibowli: office and residential mix
        adjusted += gaussian_peak(time_hours, 13.0, 1.4, 0.20 if not weekend else 0.10)
        adjusted += gaussian_peak(time_hours, 20.5, 1.7, 0.20)
    elif store_id == "DS003":  # Kondapur: residential and weekend heavy
        adjusted += gaussian_peak(time_hours, 11.0, 1.5, 0.25 if weekend else 0.05)
        adjusted += gaussian_peak(time_hours, 20.0, 1.6, 0.25)
    elif store_id == "DS004":  # Kukatpally: consistently high-volume curve
        adjusted += gaussian_peak(time_hours, 13.0, 2.2, 0.18)
        adjusted += gaussian_peak(time_hours, 19.5, 2.4, 0.18)
    elif store_id == "DS005":  # Banjara Hills: stronger evening demand
        adjusted += gaussian_peak(time_hours, 20.5, 1.5, 0.30)

    return adjusted


def build_interval_probabilities(
    intervals: pd.DataFrame,
    store_id: str,
    weekend: bool,
) -> np.ndarray:
    """Return 48 probabilities that sum to exactly 1."""

    time_hours = intervals["Start_Minute"].to_numpy(dtype=float) / 60.0
    weights = build_base_weights(time_hours, weekend)
    weights = apply_store_profile(weights, time_hours, store_id, weekend)

    return weights / weights.sum()


def generate_interval_demand(
    daily_demand: pd.DataFrame,
    intervals: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Use a multinomial draw so intervals reconcile to each daily total."""

    records = []

    for day in daily_demand.itertuples(index=False):
        probabilities = build_interval_probabilities(
            intervals=intervals,
            store_id=day.Store_ID,
            weekend=bool(day.Weekend_Flag),
        )

        # Multinomial assigns every daily order to exactly one interval.
        interval_orders = rng.multinomial(
            n=int(day.Actual_Orders),
            pvals=probabilities,
        )

        for interval, orders, probability in zip(
            intervals.itertuples(index=False),
            interval_orders,
            probabilities,
        ):
            records.append(
                {
                    "Store_Date_Interval_ID": (
                        f"{day.Store_ID}_{pd.Timestamp(day.Date):%Y%m%d}_"
                        f"I{interval.Interval_ID:02d}"
                    ),
                    "Store_Date_ID": day.Store_Date_ID,
                    "Store_ID": day.Store_ID,
                    "Date": pd.Timestamp(day.Date),
                    "Interval_ID": interval.Interval_ID,
                    "Start_Time": interval.Start_Time,
                    "End_Time": interval.End_Time,
                    "Daypart": interval.Daypart,
                    "Weekend_Flag": bool(day.Weekend_Flag),
                    "Default_Peak_Flag": bool(interval.Default_Peak_Flag),
                    "Demand_Probability": probability,
                    "Actual_Orders": int(orders),
                    "Demand_Share": orders / day.Actual_Orders,
                }
            )

    return pd.DataFrame(records)


def check(
    table: str,
    test: str,
    passed: bool,
    failure_count: int,
) -> dict:
    return {
        "Table": table,
        "Validation_Test": test,
        "Passed": bool(passed),
        "Failure_Count": int(failure_count),
    }


def validate_interval_demand(
    interval_demand: pd.DataFrame,
    stores: pd.DataFrame,
    intervals: pd.DataFrame,
    daily_demand: pd.DataFrame,
) -> pd.DataFrame:
    """Validate keys, relationships, row counts, and exact reconciliation."""

    expected_rows = len(daily_demand) * len(intervals)
    duplicate_keys = interval_demand["Store_Date_Interval_ID"].duplicated().sum()
    invalid_store_fk = (~interval_demand["Store_ID"].isin(stores["Store_ID"])).sum()
    invalid_interval_fk = (
        ~interval_demand["Interval_ID"].isin(intervals["Interval_ID"])
    ).sum()
    negative_orders = (interval_demand["Actual_Orders"] < 0).sum()

    rows_per_store_day = interval_demand.groupby("Store_Date_ID").size()
    bad_interval_counts = (rows_per_store_day != 48).sum()

    interval_totals = (
        interval_demand.groupby("Store_Date_ID", as_index=False)["Actual_Orders"]
        .sum()
        .rename(columns={"Actual_Orders": "Interval_Order_Total"})
    )
    reconciliation = daily_demand[["Store_Date_ID", "Actual_Orders"]].merge(
        interval_totals,
        on="Store_Date_ID",
        how="left",
        validate="one_to_one",
    )
    reconciliation["Difference"] = (
        reconciliation["Actual_Orders"] - reconciliation["Interval_Order_Total"]
    )
    unreconciled_days = reconciliation["Difference"].ne(0).sum()

    probability_totals = interval_demand.groupby("Store_Date_ID")[
        "Demand_Probability"
    ].sum()
    invalid_probability_totals = (~np.isclose(probability_totals, 1.0)).sum()

    night_average = interval_demand.loc[
        interval_demand["Daypart"] == "Night", "Actual_Orders"
    ].mean()
    evening_average = interval_demand.loc[
        interval_demand["Daypart"] == "Evening", "Actual_Orders"
    ].mean()

    tests = [
        check(
            "Interval Demand",
            "Expected number of rows generated",
            len(interval_demand) == expected_rows,
            abs(expected_rows - len(interval_demand)),
        ),
        check(
            "Interval Demand",
            "Store-date-interval key is unique",
            duplicate_keys == 0,
            duplicate_keys,
        ),
        check(
            "Interval Demand",
            "Every Store_ID exists in Stores",
            invalid_store_fk == 0,
            invalid_store_fk,
        ),
        check(
            "Interval Demand",
            "Every Interval_ID exists in Time Intervals",
            invalid_interval_fk == 0,
            invalid_interval_fk,
        ),
        check(
            "Interval Demand",
            "Every store-date has 48 intervals",
            bad_interval_counts == 0,
            bad_interval_counts,
        ),
        check(
            "Interval Demand",
            "Interval orders reconcile exactly to daily orders",
            unreconciled_days == 0,
            unreconciled_days,
        ),
        check(
            "Interval Demand",
            "Demand probabilities sum to 1 per store-date",
            invalid_probability_totals == 0,
            invalid_probability_totals,
        ),
        check(
            "Interval Demand",
            "Order counts are non-negative",
            negative_orders == 0,
            negative_orders,
        ),
        check(
            "Interval Demand",
            "Evening demand exceeds overnight demand",
            evening_average > night_average,
            int(evening_average <= night_average),
        ),
    ]

    return pd.DataFrame(tests)


def main() -> None:
    require_files([STORES_FILE, INTERVALS_FILE, DAILY_DEMAND_FILE])

    stores = pd.read_csv(STORES_FILE)
    intervals = pd.read_csv(INTERVALS_FILE)
    daily_demand = pd.read_csv(DAILY_DEMAND_FILE, parse_dates=["Date"])

    rng = np.random.default_rng(SEED)
    interval_demand = generate_interval_demand(
        daily_demand=daily_demand,
        intervals=intervals,
        rng=rng,
    )
    validation = validate_interval_demand(
        interval_demand=interval_demand,
        stores=stores,
        intervals=intervals,
        daily_demand=daily_demand,
    )

    sample = interval_demand.loc[
        interval_demand["Store_Date_ID"] == interval_demand["Store_Date_ID"].iloc[0]
    ]

    print("\nFIRST STORE-DAY: ALL 48 INTERVALS")
    print(
        sample[
            ["Interval_ID", "Start_Time", "Daypart", "Actual_Orders", "Demand_Share"]
        ].to_string(index=False)
    )

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(
            f"{status} | {row.Table} | {row.Validation_Test} "
            f"| Failures: {row.Failure_Count}"
        )

    print("\nBUSINESS CHECKS")
    daypart_summary = (
        interval_demand.groupby("Daypart", as_index=False)
        .agg(
            Average_Orders_Per_Interval=("Actual_Orders", "mean"),
            Total_Orders=("Actual_Orders", "sum"),
        )
        .sort_values("Average_Orders_Per_Interval", ascending=False)
    )
    print(daypart_summary.to_string(index=False))

    if not validation["Passed"].all():
        raise ValueError("Interval-demand validation failed. Review the report above.")

    interval_demand.to_csv(OUTPUT_FILE, index=False)
    validation.to_csv(VALIDATION_FILE, index=False)

    print("\nFILES GENERATED")
    print(OUTPUT_FILE)
    print(VALIDATION_FILE)


if __name__ == "__main__":
    main()
