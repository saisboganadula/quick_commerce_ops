"""Generate rider assignments, dynamic SLA, delivery events, cost, and utilization.

Prerequisites: run scripts 01-06 first.

Outputs:
    data/raw/order_rider_assignments.csv
    data/raw/delivery_events.csv
    data/raw/rider_shift_performance.csv
    data/validation/last_mile_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/07_generate_last_mile_delivery.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


SEED = 47
BASE_RIDER_COST_PER_ORDER = 30.0
PEAK_INCENTIVE_PER_ORDER = 5.0
LIGHT_RAIN_INCENTIVE = 5.0
HEAVY_RAIN_INCENTIVE = 10.0

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

FILES = {
    "intervals": RAW_DATA_DIR / "time_intervals.csv",
    "conditions": RAW_DATA_DIR / "daily_store_conditions.csv",
    "workers": RAW_DATA_DIR / "workers.csv",
    "shifts": RAW_DATA_DIR / "worker_shifts.csv",
    "orders": RAW_DATA_DIR / "orders.csv",
    "fulfilment": RAW_DATA_DIR / "fulfilment_events.csv",
}

WEATHER_SPEED_KMH = {
    "Clear": 22.0,
    "Cloudy": 21.0,
    "Light Rain": 18.0,
    "Heavy Rain": 14.0,
}

WEATHER_SLA_MINUTES = {
    "Clear": 0.0,
    "Cloudy": 0.5,
    "Light Rain": 2.0,
    "Heavy Rain": 4.0,
}


def require_files() -> None:
    missing = [path for path in FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-06 first."
        )


def build_interval_rider_supply(
    orders: pd.DataFrame,
    intervals: pd.DataFrame,
    shifts: pd.DataFrame,
    workers: pd.DataFrame,
) -> pd.DataFrame:
    """Count logged-in riders for each detail store-date interval."""

    rider_ids = set(workers.loc[workers["Worker_Type"] == "Rider", "Worker_ID"])
    rider_shifts = shifts.loc[
        shifts["Worker_ID"].isin(rider_ids)
        & shifts["Actual_Login"].notna()
        & shifts["Actual_Logout"].notna()
    ].copy()

    detail_dates = sorted(pd.to_datetime(orders["Order_Date"].unique()))
    store_ids = sorted(orders["Store_ID"].unique())
    records = []

    for date in detail_dates:
        date = pd.Timestamp(date).normalize()
        for interval in intervals.itertuples(index=False):
            start = date + pd.Timedelta(minutes=int(interval.Start_Minute))
            end = start + pd.Timedelta(minutes=30)
            for store_id in store_ids:
                active_count = (
                    (rider_shifts["Store_ID"] == store_id)
                    & (rider_shifts["Actual_Login"] < end)
                    & (rider_shifts["Actual_Logout"] > start)
                ).sum()
                records.append(
                    {
                        "Store_Date_Interval_ID": (
                            f"{store_id}_{date:%Y%m%d}_I{int(interval.Interval_ID):02d}"
                        ),
                        "Active_Riders": int(active_count),
                    }
                )

    return pd.DataFrame(records)


def add_dynamic_sla(
    orders: pd.DataFrame,
    conditions: pd.DataFrame,
    intervals: pd.DataFrame,
    rider_supply: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate the customer-facing promise available at order creation."""

    condition_columns = ["Store_ID", "Date", "Weather_Type"]
    orders = orders.merge(
        conditions[condition_columns],
        left_on=["Store_ID", "Order_Date"],
        right_on=["Store_ID", "Date"],
        how="left",
        validate="many_to_one",
    ).drop(columns=["Date"])

    orders = orders.merge(
        intervals[["Interval_ID", "Default_Peak_Flag"]],
        on="Interval_ID",
        how="left",
        validate="many_to_one",
    ).merge(
        rider_supply,
        on="Store_Date_Interval_ID",
        how="left",
        validate="many_to_one",
    )

    interval_order_count = orders.groupby("Store_Date_Interval_ID")["Order_ID"].transform("count")
    orders["Orders_Per_Active_Rider_Interval"] = np.where(
        orders["Active_Riders"] > 0,
        interval_order_count / orders["Active_Riders"],
        interval_order_count,
    )

    load_delay = np.maximum(
        0,
        orders["Orders_Per_Active_Rider_Interval"] - 1.0,
    ) * 2.0
    peak_delay = np.where(orders["Default_Peak_Flag"], 1.5, 0.0)
    weather_delay = orders["Weather_Type"].map(WEATHER_SLA_MINUTES)

    raw_sla = (
        6.0
        + orders["Delivery_Distance_KM"] * 2.3
        + load_delay
        + peak_delay
        + weather_delay
    )
    orders["Displayed_SLA_Minutes"] = np.ceil(np.clip(raw_sla, 7, 25)).astype(int)
    return orders


def build_rider_shift_candidates(
    shifts: pd.DataFrame,
    workers: pd.DataFrame,
    detail_start: pd.Timestamp,
    detail_end: pd.Timestamp,
) -> dict[str, list[dict]]:
    """Prepare actual attended rider shifts for allocation."""

    rider_ids = set(workers.loc[workers["Worker_Type"] == "Rider", "Worker_ID"])
    attended = shifts.loc[
        shifts["Worker_ID"].isin(rider_ids)
        & shifts["Actual_Login"].notna()
        & shifts["Actual_Logout"].notna()
        & (shifts["Actual_Logout"] >= detail_start)
        & (shifts["Actual_Login"] <= detail_end + pd.Timedelta(days=1))
    ].copy()

    by_store = {}
    for store_id, group in attended.groupby("Store_ID"):
        candidates = []
        for row in group.itertuples(index=False):
            candidates.append(
                {
                    "Shift_ID": row.Shift_ID,
                    "Rider_ID": row.Worker_ID,
                    "Login": pd.Timestamp(row.Actual_Login),
                    "Logout": pd.Timestamp(row.Actual_Logout),
                    "Next_Available": pd.Timestamp(row.Actual_Login),
                    "Logged_Hours": float(row.Logged_Hours),
                }
            )
        by_store[store_id] = candidates
    return by_store


def weather_incentive(weather_type: str) -> float:
    if weather_type == "Heavy Rain":
        return HEAVY_RAIN_INCENTIVE
    if weather_type == "Light Rain":
        return LIGHT_RAIN_INCENTIVE
    return 0.0


def assign_and_deliver(
    orders: pd.DataFrame,
    fulfilment: pd.DataFrame,
    shifts: pd.DataFrame,
    workers: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Assign each order to the earliest feasible rider shift."""

    order_journey = orders.merge(
        fulfilment,
        on="Order_ID",
        how="left",
        validate="one_to_one",
    ).sort_values(["Store_ID", "Order_Created_Time"])

    candidates_by_store = build_rider_shift_candidates(
        shifts,
        workers,
        order_journey["Order_Created_Time"].min(),
        order_journey["Order_Created_Time"].max(),
    )

    assignments = []
    deliveries = []
    performance_records = {}

    for store_id, store_orders in order_journey.groupby("Store_ID", sort=False):
        candidates = candidates_by_store[store_id]

        for order in store_orders.itertuples(index=False):
            created = pd.Timestamp(order.Order_Created_Time)
            ready = pd.Timestamp(order.Drop_Zone_Ready_Time)

            best_candidate = None
            best_arrival = None
            for candidate in candidates:
                if candidate["Logout"] <= created:
                    continue
                arrival = max(created, candidate["Login"], candidate["Next_Available"])
                if arrival >= candidate["Logout"]:
                    continue
                if best_arrival is None or arrival < best_arrival:
                    best_arrival = arrival
                    best_candidate = candidate

            if best_candidate is None:
                raise ValueError(f"No rider shift available for order {order.Order_ID}")

            verification_seconds = int(rng.integers(20, 91))
            pickup = max(best_arrival, ready) + pd.Timedelta(seconds=verification_seconds)

            speed = WEATHER_SPEED_KMH[order.Weather_Type]
            road_noise = float(np.clip(rng.lognormal(0, 0.16), 0.75, 1.60))
            building_minutes = float(rng.uniform(0.7, 3.0))
            travel_minutes = (
                order.Delivery_Distance_KM / speed * 60 * road_noise
                + building_minutes
            )
            delivered = pickup + pd.Timedelta(minutes=travel_minutes)

            # The rider becomes available after returning toward the store/zone.
            return_minutes = order.Delivery_Distance_KM / speed * 60 * 0.75
            next_available = delivered + pd.Timedelta(minutes=return_minutes)
            best_candidate["Next_Available"] = next_available

            active_delivery_minutes = (delivered - pickup).total_seconds() / 60
            round_trip_minutes = (next_available - pickup).total_seconds() / 60
            total_delivery_minutes = (delivered - created).total_seconds() / 60
            sla_breach_minutes = max(0.0, total_delivery_minutes - order.Displayed_SLA_Minutes)

            peak_incentive = PEAK_INCENTIVE_PER_ORDER if order.Default_Peak_Flag else 0.0
            rain_incentive = weather_incentive(order.Weather_Type)
            order_cost = BASE_RIDER_COST_PER_ORDER + peak_incentive + rain_incentive

            assignments.append(
                {
                    "Assignment_ID": f"RA_{order.Order_ID}",
                    "Order_ID": order.Order_ID,
                    "Rider_ID": best_candidate["Rider_ID"],
                    "Rider_Shift_ID": best_candidate["Shift_ID"],
                    "Assigned_Time": best_arrival,
                }
            )
            deliveries.append(
                {
                    "Order_ID": order.Order_ID,
                    "Rider_Arrival_Time": best_arrival,
                    "Rider_Pickup_Time": pickup,
                    "Delivered_Time": delivered,
                    "Rider_Wait_For_Order_Minutes": max(0.0, (ready - best_arrival).total_seconds() / 60),
                    "Ready_Order_Wait_For_Rider_Minutes": max(0.0, (best_arrival - ready).total_seconds() / 60),
                    "Pickup_Verification_Minutes": verification_seconds / 60,
                    "Last_Mile_Minutes": active_delivery_minutes,
                    "Round_Trip_Busy_Minutes": round_trip_minutes,
                    "Total_Delivery_Minutes": total_delivery_minutes,
                    "Displayed_SLA_Minutes": order.Displayed_SLA_Minutes,
                    "SLA_Breach_Flag": sla_breach_minutes > 0,
                    "SLA_Breach_Minutes": sla_breach_minutes,
                    "Weather_Type": order.Weather_Type,
                    "Peak_Flag": bool(order.Default_Peak_Flag),
                    "Base_Rider_Cost_INR": BASE_RIDER_COST_PER_ORDER,
                    "Peak_Incentive_INR": peak_incentive,
                    "Rain_Incentive_INR": rain_incentive,
                    "Total_Rider_Cost_INR": order_cost,
                }
            )

            perf = performance_records.setdefault(
                best_candidate["Shift_ID"],
                {
                    "Rider_Shift_ID": best_candidate["Shift_ID"],
                    "Rider_ID": best_candidate["Rider_ID"],
                    "Delivered_Orders": 0,
                    "Active_Delivery_Minutes": 0.0,
                    "Round_Trip_Busy_Minutes": 0.0,
                    "Rider_Cost_INR": 0.0,
                },
            )
            perf["Delivered_Orders"] += 1
            perf["Active_Delivery_Minutes"] += active_delivery_minutes
            perf["Round_Trip_Busy_Minutes"] += round_trip_minutes
            perf["Rider_Cost_INR"] += order_cost

    assignments_df = pd.DataFrame(assignments)
    deliveries_df = pd.DataFrame(deliveries)
    performance = pd.DataFrame(performance_records.values())

    rider_ids = set(workers.loc[workers["Worker_Type"] == "Rider", "Worker_ID"])
    detail_start_date = order_journey["Order_Date"].min()
    detail_end_date = order_journey["Order_Date"].max()
    rider_shift_details = shifts.loc[
        shifts["Worker_ID"].isin(rider_ids)
        & shifts["Shift_Date"].between(detail_start_date, detail_end_date),
        ["Shift_ID", "Store_ID", "Shift_Date", "Logged_Hours"],
    ].rename(columns={"Shift_ID": "Rider_Shift_ID"})
    performance = rider_shift_details.merge(
        performance,
        on="Rider_Shift_ID",
        how="left",
        validate="one_to_one",
    )
    for column in [
        "Delivered_Orders",
        "Active_Delivery_Minutes",
        "Round_Trip_Busy_Minutes",
        "Rider_Cost_INR",
    ]:
        performance[column] = performance[column].fillna(0)
    performance["Rider_Utilization"] = np.where(
        performance["Logged_Hours"] > 0,
        performance["Active_Delivery_Minutes"] / (performance["Logged_Hours"] * 60),
        0.0,
    )
    performance["Orders_Per_Logged_Hour"] = np.where(
        performance["Logged_Hours"] > 0,
        performance["Delivered_Orders"] / performance["Logged_Hours"],
        0.0,
    )
    return assignments_df, deliveries_df, performance


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {"Table": table, "Validation_Test": test, "Passed": bool(passed), "Failure_Count": int(failures)}


def validate(
    orders: pd.DataFrame,
    fulfilment: pd.DataFrame,
    assignments: pd.DataFrame,
    deliveries: pd.DataFrame,
    workers: pd.DataFrame,
    performance: pd.DataFrame,
) -> pd.DataFrame:
    rider_ids = set(workers.loc[workers["Worker_Type"] == "Rider", "Worker_ID"])
    missing_assignments = (~orders["Order_ID"].isin(assignments["Order_ID"])).sum()
    missing_deliveries = (~orders["Order_ID"].isin(deliveries["Order_ID"])).sum()
    invalid_riders = (~assignments["Rider_ID"].isin(rider_ids)).sum()
    duplicate_assignments = assignments["Order_ID"].duplicated().sum()
    duplicate_deliveries = deliveries["Order_ID"].duplicated().sum()
    journey = fulfilment.merge(deliveries, on="Order_ID", validate="one_to_one")
    invalid_sequence = (
        (journey["Rider_Pickup_Time"] < journey["Rider_Arrival_Time"])
        | (journey["Rider_Pickup_Time"] < journey["Drop_Zone_Ready_Time"])
        | (journey["Delivered_Time"] < journey["Rider_Pickup_Time"])
    ).sum()
    negative_times = (
        deliveries[
            [
                "Rider_Wait_For_Order_Minutes",
                "Ready_Order_Wait_For_Rider_Minutes",
                "Last_Mile_Minutes",
                "Total_Delivery_Minutes",
            ]
        ] < 0
    ).any(axis=1).sum()
    invalid_sla = (~deliveries["Displayed_SLA_Minutes"].between(7, 25)).sum()
    cost_reconciliation = (
        deliveries["Base_Rider_Cost_INR"]
        + deliveries["Peak_Incentive_INR"]
        + deliveries["Rain_Incentive_INR"]
        - deliveries["Total_Rider_Cost_INR"]
    ).abs().gt(0.001).sum()
    impossible_utilization = (performance["Rider_Utilization"] < 0).sum()

    tests = [
        check("Rider Assignments", "Every order has a rider assignment", missing_assignments == 0 and len(assignments) == len(orders), missing_assignments),
        check("Rider Assignments", "One rider assignment exists per order", duplicate_assignments == 0, duplicate_assignments),
        check("Rider Assignments", "Every assigned worker is a rider", invalid_riders == 0, invalid_riders),
        check("Delivery Events", "Every order has a delivery event", missing_deliveries == 0 and len(deliveries) == len(orders), missing_deliveries),
        check("Delivery Events", "One delivery event exists per order", duplicate_deliveries == 0, duplicate_deliveries),
        check("Delivery Events", "Timestamps follow the correct sequence", invalid_sequence == 0, invalid_sequence),
        check("Delivery Events", "Duration metrics are non-negative", negative_times == 0, negative_times),
        check("Delivery Events", "Displayed SLA remains between 7 and 25 minutes", invalid_sla == 0, invalid_sla),
        check("Delivery Events", "Rider cost components reconcile", cost_reconciliation == 0, cost_reconciliation),
        check("Rider Performance", "Utilization is non-negative", impossible_utilization == 0, impossible_utilization),
    ]
    return pd.DataFrame(tests)


def main() -> None:
    require_files()
    rng = np.random.default_rng(SEED)

    intervals = pd.read_csv(FILES["intervals"])
    conditions = pd.read_csv(FILES["conditions"], parse_dates=["Date"])
    workers = pd.read_csv(FILES["workers"], parse_dates=["Join_Date"])
    shifts = pd.read_csv(
        FILES["shifts"],
        parse_dates=["Shift_Date", "Scheduled_Start", "Scheduled_End", "Actual_Login", "Actual_Logout"],
    )
    orders = pd.read_csv(
        FILES["orders"],
        parse_dates=["Order_Date", "Order_Created_Time"],
        dtype={"Promotion_ID": "string"},
        low_memory=False,
    )
    fulfilment = pd.read_csv(
        FILES["fulfilment"],
        parse_dates=["Picking_Started_Time", "Picking_Completed_Time", "Drop_Zone_Ready_Time"],
    )

    rider_supply = build_interval_rider_supply(orders, intervals, shifts, workers)
    orders_with_sla = add_dynamic_sla(orders, conditions, intervals, rider_supply)
    assignments, deliveries, performance = assign_and_deliver(
        orders_with_sla, fulfilment, shifts, workers, rng
    )
    validation = validate(
        orders_with_sla, fulfilment, assignments, deliveries, workers, performance
    )

    print("\nLAST-MILE SUMMARY")
    print(f"Orders delivered: {len(deliveries):,}")
    print(f"Average displayed SLA: {deliveries['Displayed_SLA_Minutes'].mean():.2f} minutes")
    print(f"Average actual delivery: {deliveries['Total_Delivery_Minutes'].mean():.2f} minutes")
    print(f"SLA breach rate: {deliveries['SLA_Breach_Flag'].mean():.2%}")
    print(f"Average rider utilization: {performance.loc[performance['Logged_Hours'] > 0, 'Rider_Utilization'].mean():.2%}")
    print(f"Average rider cost/order: INR {deliveries['Total_Rider_Cost_INR'].mean():.2f}")

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(f"{status} | {row.Table} | {row.Validation_Test} | Failures: {row.Failure_Count}")

    if not validation["Passed"].all():
        raise ValueError("Last-mile validation failed. Review the report above.")

    assignments.to_csv(RAW_DATA_DIR / "order_rider_assignments.csv", index=False)
    deliveries.to_csv(RAW_DATA_DIR / "delivery_events.csv", index=False)
    performance.to_csv(RAW_DATA_DIR / "rider_shift_performance.csv", index=False)
    validation.to_csv(VALIDATION_DIR / "last_mile_validation.csv", index=False)

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "order_rider_assignments.csv")
    print(RAW_DATA_DIR / "delivery_events.csv")
    print(RAW_DATA_DIR / "rider_shift_performance.csv")
    print(VALIDATION_DIR / "last_mile_validation.csv")


if __name__ == "__main__":
    main()
