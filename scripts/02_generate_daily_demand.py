"""Generate 60 days of explainable store-level demand.

Prerequisite
------------
Run scripts/01_generate_master_data.py first so data/raw/stores.csv exists.

Outputs
-------
data/raw/calendar.csv
data/raw/daily_store_conditions.csv
data/raw/daily_store_demand.csv
data/validation/daily_demand_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/02_generate_daily_demand.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


SEED = 42
SIMULATION_START_DATE = "2026-07-01"
SIMULATION_DAYS = 60

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

STORES_FILE = RAW_DATA_DIR / "stores.csv"

RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)


# Business assumptions. These are editable simulation inputs, not confirmed facts.
DAY_OF_WEEK_FACTORS = {
    "Monday": 0.92,
    "Tuesday": 0.94,
    "Wednesday": 0.96,
    "Thursday": 1.00,
    "Friday": 1.08,
    "Saturday": 1.15,
    "Sunday": 1.12,
}

WEATHER_FACTORS = {
    "Clear": 1.00,
    "Cloudy": 1.02,
    "Light Rain": 1.08,
    "Heavy Rain": 1.15,
}

# Hyderabad monsoon-period simulation weights; they sum to 1.00.
WEATHER_PROBABILITIES = [0.35, 0.25, 0.25, 0.15]

SALARY_WEEK_FACTOR = 1.08
PROMOTION_FACTOR = 1.18
PROMOTION_PROBABILITY = 0.12
NOISE_STANDARD_DEVIATION = 0.04
NOISE_MINIMUM = 0.88
NOISE_MAXIMUM = 1.12


def require_file(path: Path) -> None:
    """Stop with a clear message when a prerequisite file is missing."""

    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}\n"
            "Run 01_generate_master_data.py first."
        )


def build_calendar() -> pd.DataFrame:
    """Return one row per simulation date."""

    dates = pd.date_range(
        start=SIMULATION_START_DATE,
        periods=SIMULATION_DAYS,
        freq="D",
    )

    calendar = pd.DataFrame({"Date": dates})
    calendar["Day_Name"] = calendar["Date"].dt.day_name()
    calendar["Day_Of_Week_Number"] = calendar["Date"].dt.dayofweek + 1
    calendar["Weekend_Flag"] = calendar["Day_Name"].isin(["Saturday", "Sunday"])
    calendar["Salary_Week_Flag"] = calendar["Date"].dt.day.between(1, 7)
    calendar["Day_Of_Week_Factor"] = calendar["Day_Name"].map(DAY_OF_WEEK_FACTORS)

    return calendar


def build_daily_store_conditions(
    stores: pd.DataFrame,
    calendar: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate one condition row per store-date.

    Weather is sampled once per date because all stores are in Hyderabad.
    Promotions are sampled independently by store and date.
    """

    weather_by_date = pd.DataFrame(
        {
            "Date": calendar["Date"],
            "Weather_Type": rng.choice(
                list(WEATHER_FACTORS.keys()),
                size=len(calendar),
                p=WEATHER_PROBABILITIES,
            ),
        }
    )
    weather_by_date["Weather_Factor"] = weather_by_date["Weather_Type"].map(
        WEATHER_FACTORS
    )

    conditions = (
        stores[["Store_ID"]]
        .merge(calendar[["Date"]], how="cross")
        .merge(weather_by_date, on="Date", how="left", validate="many_to_one")
    )

    conditions["Promotion_Flag"] = (
        rng.random(len(conditions)) < PROMOTION_PROBABILITY
    )
    conditions["Promotion_Factor"] = np.where(
        conditions["Promotion_Flag"],
        PROMOTION_FACTOR,
        1.00,
    )

    # Noise represents small unobserved effects. Clipping prevents extreme values.
    conditions["Random_Noise_Factor"] = np.clip(
        rng.normal(
            loc=1.00,
            scale=NOISE_STANDARD_DEVIATION,
            size=len(conditions),
        ),
        NOISE_MINIMUM,
        NOISE_MAXIMUM,
    )

    conditions.insert(
        0,
        "Store_Date_ID",
        conditions["Store_ID"]
        + "_"
        + conditions["Date"].dt.strftime("%Y%m%d"),
    )

    return conditions


def build_daily_store_demand(
    stores: pd.DataFrame,
    calendar: pd.DataFrame,
    conditions: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Calculate expected demand and sample actual order counts."""

    demand = (
        conditions.merge(
            stores[["Store_ID", "Base_Daily_Orders"]],
            on="Store_ID",
            how="left",
            validate="many_to_one",
        )
        .merge(
            calendar[
                [
                    "Date",
                    "Day_Name",
                    "Weekend_Flag",
                    "Salary_Week_Flag",
                    "Day_Of_Week_Factor",
                ]
            ],
            on="Date",
            how="left",
            validate="many_to_one",
        )
    )

    demand["Salary_Week_Factor"] = np.where(
        demand["Salary_Week_Flag"],
        SALARY_WEEK_FACTOR,
        1.00,
    )

    demand["Expected_Orders"] = (
        demand["Base_Daily_Orders"]
        * demand["Day_Of_Week_Factor"]
        * demand["Salary_Week_Factor"]
        * demand["Weather_Factor"]
        * demand["Promotion_Factor"]
        * demand["Random_Noise_Factor"]
    )

    # A Poisson draw converts expected demand into a realistic non-negative count.
    demand["Actual_Orders"] = rng.poisson(demand["Expected_Orders"]).astype(int)
    demand["Demand_Variance"] = demand["Actual_Orders"] - demand["Expected_Orders"]
    demand["Demand_Variance_Pct"] = (
        demand["Demand_Variance"] / demand["Expected_Orders"]
    )

    return demand[
        [
            "Store_Date_ID",
            "Store_ID",
            "Date",
            "Day_Name",
            "Weekend_Flag",
            "Salary_Week_Flag",
            "Weather_Type",
            "Promotion_Flag",
            "Base_Daily_Orders",
            "Day_Of_Week_Factor",
            "Salary_Week_Factor",
            "Weather_Factor",
            "Promotion_Factor",
            "Random_Noise_Factor",
            "Expected_Orders",
            "Actual_Orders",
            "Demand_Variance",
            "Demand_Variance_Pct",
        ]
    ]


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


def validate_outputs(
    stores: pd.DataFrame,
    calendar: pd.DataFrame,
    conditions: pd.DataFrame,
    demand: pd.DataFrame,
) -> pd.DataFrame:
    """Run structural, relational, and basic behavioral checks."""

    expected_store_dates = len(stores) * len(calendar)
    invalid_store_fk = (~demand["Store_ID"].isin(stores["Store_ID"])).sum()
    invalid_date_fk = (~demand["Date"].isin(calendar["Date"])).sum()
    invalid_noise = (~conditions["Random_Noise_Factor"].between(
        NOISE_MINIMUM, NOISE_MAXIMUM
    )).sum()
    invalid_actual_orders = (demand["Actual_Orders"] < 0).sum()

    weekend_mean = demand.loc[demand["Weekend_Flag"], "Actual_Orders"].mean()
    weekday_mean = demand.loc[~demand["Weekend_Flag"], "Actual_Orders"].mean()
    promotion_mean = demand.loc[demand["Promotion_Flag"], "Actual_Orders"].mean()
    no_promotion_mean = demand.loc[~demand["Promotion_Flag"], "Actual_Orders"].mean()

    tests = [
        check(
            "Calendar",
            "Calendar has the requested number of dates",
            len(calendar) == SIMULATION_DAYS,
            abs(SIMULATION_DAYS - len(calendar)),
        ),
        check(
            "Calendar",
            "Date is unique",
            calendar["Date"].is_unique,
            calendar["Date"].duplicated().sum(),
        ),
        check(
            "Daily Store Conditions",
            "One condition row exists per store-date",
            len(conditions) == expected_store_dates,
            abs(expected_store_dates - len(conditions)),
        ),
        check(
            "Daily Store Conditions",
            "Store_Date_ID is unique",
            conditions["Store_Date_ID"].is_unique,
            conditions["Store_Date_ID"].duplicated().sum(),
        ),
        check(
            "Daily Store Conditions",
            "Random noise stays within its allowed range",
            invalid_noise == 0,
            invalid_noise,
        ),
        check(
            "Daily Store Demand",
            "One demand row exists per store-date",
            len(demand) == expected_store_dates,
            abs(expected_store_dates - len(demand)),
        ),
        check(
            "Daily Store Demand",
            "Store_Date_ID is unique",
            demand["Store_Date_ID"].is_unique,
            demand["Store_Date_ID"].duplicated().sum(),
        ),
        check(
            "Daily Store Demand",
            "Every Store_ID exists in Stores",
            invalid_store_fk == 0,
            invalid_store_fk,
        ),
        check(
            "Daily Store Demand",
            "Every Date exists in Calendar",
            invalid_date_fk == 0,
            invalid_date_fk,
        ),
        check(
            "Daily Store Demand",
            "Actual orders are non-negative integers",
            invalid_actual_orders == 0,
            invalid_actual_orders,
        ),
        check(
            "Daily Store Demand",
            "Weekend average demand exceeds weekday average demand",
            weekend_mean > weekday_mean,
            int(weekend_mean <= weekday_mean),
        ),
        check(
            "Daily Store Demand",
            "Promotion-day average demand exceeds non-promotion demand",
            promotion_mean > no_promotion_mean,
            int(promotion_mean <= no_promotion_mean),
        ),
    ]

    return pd.DataFrame(tests)


def main() -> None:
    require_file(STORES_FILE)

    stores = pd.read_csv(STORES_FILE)
    calendar = build_calendar()

    # One generator object controls every random draw in this script.
    rng = np.random.default_rng(SEED)

    conditions = build_daily_store_conditions(stores, calendar, rng)
    demand = build_daily_store_demand(stores, calendar, conditions, rng)
    validation = validate_outputs(stores, calendar, conditions, demand)

    print("\nDAILY DEMAND SAMPLE")
    print(demand.head(10).to_string(index=False))

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(
            f"{status} | {row.Table} | {row.Validation_Test} "
            f"| Failures: {row.Failure_Count}"
        )

    print("\nBUSINESS CHECKS")
    print(
        "Average daily network orders:",
        f"{demand.groupby('Date')['Actual_Orders'].sum().mean():,.0f}",
    )
    print(
        "Weekday average orders per store-day:",
        f"{demand.loc[~demand['Weekend_Flag'], 'Actual_Orders'].mean():,.0f}",
    )
    print(
        "Weekend average orders per store-day:",
        f"{demand.loc[demand['Weekend_Flag'], 'Actual_Orders'].mean():,.0f}",
    )
    print(
        "Non-promotion average orders per store-day:",
        f"{demand.loc[~demand['Promotion_Flag'], 'Actual_Orders'].mean():,.0f}",
    )
    print(
        "Promotion average orders per store-day:",
        f"{demand.loc[demand['Promotion_Flag'], 'Actual_Orders'].mean():,.0f}",
    )

    if not validation["Passed"].all():
        raise ValueError("Daily-demand validation failed. Review the report above.")

    calendar.to_csv(RAW_DATA_DIR / "calendar.csv", index=False)
    conditions.to_csv(RAW_DATA_DIR / "daily_store_conditions.csv", index=False)
    demand.to_csv(RAW_DATA_DIR / "daily_store_demand.csv", index=False)
    validation.to_csv(
        VALIDATION_DIR / "daily_demand_validation.csv",
        index=False,
    )

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "calendar.csv")
    print(RAW_DATA_DIR / "daily_store_conditions.csv")
    print(RAW_DATA_DIR / "daily_store_demand.csv")
    print(VALIDATION_DIR / "daily_demand_validation.csv")


if __name__ == "__main__":
    main()
