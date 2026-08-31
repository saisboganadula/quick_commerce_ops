"""Generate picker capacity, assignments, pick times, and fulfilment events.

Prerequisites: run scripts 01-05 first.

Outputs:
    data/raw/picker_interval_operations.csv
    data/raw/order_picker_assignments.csv
    data/raw/order_items_fulfilled.csv
    data/raw/fulfilment_events.csv
    data/validation/store_fulfilment_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/06_generate_store_fulfilment.py
"""

from heapq import heapify, heappop, heappush
from pathlib import Path

import numpy as np
import pandas as pd


SEED = 46
BASE_PICK_SECONDS_PER_UNIT = 15.0
PUTAWAY_RATE_PER_PICKER_HOUR = 150.0
DAILY_INBOUND_UNITS_PER_STORE = 10000
DAILY_AUDIT_HOURS_PER_STORE = 50.0
PUTAWAY_START_HOUR = 0
PUTAWAY_TARGET_END_HOUR = 6
PUTAWAY_FLEX_END_HOUR = 9

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

FILES = {
    "stores": RAW_DATA_DIR / "stores.csv",
    "intervals": RAW_DATA_DIR / "time_intervals.csv",
    "interval_demand": RAW_DATA_DIR / "interval_demand.csv",
    "workers": RAW_DATA_DIR / "workers.csv",
    "shifts": RAW_DATA_DIR / "worker_shifts.csv",
    "products": RAW_DATA_DIR / "products.csv",
    "orders": RAW_DATA_DIR / "orders.csv",
    "items": RAW_DATA_DIR / "order_items.csv",
}

EXPERIENCE_FACTOR = {"New": 1.15, "Experienced": 1.00, "Senior": 0.90}
COMPLEXITY_FACTOR = {"Low": 0.90, "Medium": 1.00, "High": 1.20}


def require_files() -> None:
    missing = [path for path in FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-05 first."
        )


def active_pickers_by_interval(
    orders: pd.DataFrame,
    intervals: pd.DataFrame,
    workers: pd.DataFrame,
    shifts: pd.DataFrame,
) -> dict[tuple[str, pd.Timestamp, int], list[str]]:
    """Map each detail store-date-interval to logged-in picker IDs."""

    detail_dates = sorted(pd.to_datetime(orders["Order_Date"].unique()))
    picker_workers = set(workers.loc[workers["Worker_Type"] == "Picker", "Worker_ID"])
    present = shifts.loc[
        shifts["Worker_ID"].isin(picker_workers)
        & shifts["Actual_Login"].notna()
        & shifts["Actual_Logout"].notna()
    ]

    mapping = {}
    stores = sorted(orders["Store_ID"].unique())
    for date in detail_dates:
        date = pd.Timestamp(date)
        for interval in intervals.itertuples(index=False):
            start = date.normalize() + pd.Timedelta(minutes=int(interval.Start_Minute))
            end = start + pd.Timedelta(minutes=30)
            for store_id in stores:
                eligible = present.loc[
                    (present["Store_ID"] == store_id)
                    & (present["Actual_Login"] < end)
                    & (present["Actual_Logout"] > start),
                    "Worker_ID",
                ].tolist()
                mapping[(store_id, date.normalize(), int(interval.Interval_ID))] = eligible
    return mapping


def build_picker_interval_operations(
    orders: pd.DataFrame,
    interval_demand: pd.DataFrame,
    intervals: pd.DataFrame,
    active_map: dict,
) -> pd.DataFrame:
    """Reserve picker labour for put-away/audit and expose picking capacity."""

    detail_dates = orders["Order_Date"].drop_duplicates()
    detail = interval_demand.loc[interval_demand["Date"].isin(detail_dates)].copy()
    interval_hour = intervals.set_index("Interval_ID")["Hour_Number"].to_dict()
    records = []

    for row in detail.itertuples(index=False):
        date = pd.Timestamp(row.Date).normalize()
        key = (row.Store_ID, date, int(row.Interval_ID))
        active_ids = active_map.get(key, [])
        active_count = len(active_ids)
        hour = int(interval_hour[row.Interval_ID])

        # Reserve one picker for orders whenever anybody is logged in.
        maximum_non_pick = max(0, active_count - 1)
        putaway_target = 12 if PUTAWAY_START_HOUR <= hour < PUTAWAY_TARGET_END_HOUR else 0
        if PUTAWAY_TARGET_END_HOUR <= hour < PUTAWAY_FLEX_END_HOUR:
            putaway_target = 3
        putaway_pickers = min(putaway_target, maximum_non_pick)

        remaining = active_count - putaway_pickers
        # Audit is favored outside lunch/evening peaks and may be deferred.
        audit_target = 2 if row.Daypart in {"Night", "Morning", "Afternoon", "Late Night"} else 0
        audit_pickers = min(audit_target, max(0, remaining - 1))
        picking_pickers = max(0, active_count - putaway_pickers - audit_pickers)

        putaway_units_capacity = int(round(putaway_pickers * 0.5 * PUTAWAY_RATE_PER_PICKER_HOUR))
        audit_hours_capacity = audit_pickers * 0.5

        # Selection is deterministic for reproducibility; workers rotate by interval.
        rotated = active_ids[int(row.Interval_ID) % len(active_ids):] + active_ids[:int(row.Interval_ID) % len(active_ids)] if active_ids else []
        putaway_ids = rotated[:putaway_pickers]
        audit_ids = rotated[putaway_pickers:putaway_pickers + audit_pickers]
        picking_ids = rotated[putaway_pickers + audit_pickers:]

        records.append(
            {
                "Store_Date_Interval_ID": row.Store_Date_Interval_ID,
                "Store_ID": row.Store_ID,
                "Date": date,
                "Interval_ID": int(row.Interval_ID),
                "Daypart": row.Daypart,
                "Actual_Orders": int(row.Actual_Orders),
                "Active_Pickers": active_count,
                "Putaway_Pickers": putaway_pickers,
                "Audit_Pickers": audit_pickers,
                "Picking_Pickers": picking_pickers,
                "Putaway_Units_Capacity": putaway_units_capacity,
                "Audit_Hours_Capacity": audit_hours_capacity,
                "Putaway_Worker_IDs": "|".join(putaway_ids),
                "Audit_Worker_IDs": "|".join(audit_ids),
                "Picking_Worker_IDs": "|".join(picking_ids),
            }
        )

    operations = pd.DataFrame(records)
    operations["Orders_Per_Picking_Picker"] = np.where(
        operations["Picking_Pickers"] > 0,
        operations["Actual_Orders"] / operations["Picking_Pickers"],
        np.nan,
    )
    return operations


def prepare_item_base_times(
    items: pd.DataFrame,
    products: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Calculate item-line pick seconds before picker experience is applied."""

    enriched = items.merge(
        products[["Product_ID", "Handling_Complexity"]],
        on="Product_ID",
        how="left",
        validate="many_to_one",
    )
    noise = np.clip(rng.lognormal(mean=0, sigma=0.18, size=len(enriched)), 0.65, 1.75)
    enriched["Base_Seconds_Per_Unit"] = (
        BASE_PICK_SECONDS_PER_UNIT
        * enriched["Handling_Complexity"].map(COMPLEXITY_FACTOR)
        * noise
    )
    enriched["Base_Line_Pick_Seconds"] = (
        enriched["Base_Seconds_Per_Unit"] * enriched["Ordered_Quantity"]
    )
    return enriched


def assign_orders_and_build_events(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    workers: pd.DataFrame,
    operations: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Assign orders to picker queues and create sequential timestamps."""

    experience = workers.set_index("Worker_ID")["Experience_Band"].to_dict()
    base_order_seconds = items.groupby("Order_ID")["Base_Line_Pick_Seconds"].sum().to_dict()
    item_groups = {order_id: group.index.to_numpy() for order_id, group in items.groupby("Order_ID")}
    picking_ids = operations.set_index("Store_Date_Interval_ID")["Picking_Worker_IDs"].to_dict()

    assignments = []
    events = []
    picker_for_order = {}

    for interval_key, group in orders.groupby("Store_Date_Interval_ID", sort=False):
        available = [worker_id for worker_id in picking_ids.get(interval_key, "").split("|") if worker_id]
        if not available:
            raise ValueError(f"No picking picker available for interval {interval_key}")

        ordered = group.sort_values("Order_Created_Time")
        heap = [(pd.Timestamp(ordered["Order_Created_Time"].min()), worker_id) for worker_id in available]
        heapify(heap)

        interval_load = len(ordered) / max(len(available), 1)
        congestion_factor = 1 + max(0, interval_load - 12) * 0.012
        congestion_factor = min(congestion_factor, 1.65)

        for order in ordered.itertuples(index=False):
            next_free, picker_id = heappop(heap)
            created = pd.Timestamp(order.Order_Created_Time)
            picking_start = max(created, next_free)
            experience_factor = EXPERIENCE_FACTOR[experience[picker_id]]
            total_pick_seconds = max(
                5.0,
                base_order_seconds[order.Order_ID] * experience_factor * congestion_factor,
            )
            picking_complete = picking_start + pd.Timedelta(seconds=total_pick_seconds)
            drop_zone_delay = int(np.clip(rng.lognormal(np.log(35), 0.45), 10, 180))
            drop_zone_ready = picking_complete + pd.Timedelta(seconds=drop_zone_delay)
            heappush(heap, (picking_complete, picker_id))

            picker_for_order[order.Order_ID] = picker_id
            assignments.append(
                {
                    "Assignment_ID": f"PA_{order.Order_ID}",
                    "Order_ID": order.Order_ID,
                    "Picker_ID": picker_id,
                    "Assigned_Time": picking_start,
                }
            )
            events.append(
                {
                    "Order_ID": order.Order_ID,
                    "Picking_Started_Time": picking_start,
                    "Picking_Completed_Time": picking_complete,
                    "Drop_Zone_Ready_Time": drop_zone_ready,
                    "Pick_Queue_Minutes": (picking_start - created).total_seconds() / 60,
                    "Picking_Minutes": total_pick_seconds / 60,
                    "Drop_Zone_Delay_Minutes": drop_zone_delay / 60,
                }
            )

    assignments_df = pd.DataFrame(assignments)
    events_df = pd.DataFrame(events)

    items = items.copy()
    items["Picking_Worker_ID"] = items["Order_ID"].map(picker_for_order)
    items["Experience_Factor"] = items["Picking_Worker_ID"].map(
        lambda worker_id: EXPERIENCE_FACTOR[experience[worker_id]]
    )
    order_congestion = orders[["Order_ID", "Store_Date_Interval_ID"]].merge(
        operations[["Store_Date_Interval_ID", "Orders_Per_Picking_Picker"]],
        on="Store_Date_Interval_ID",
        validate="many_to_one",
    )
    order_congestion["Congestion_Factor"] = np.clip(
        1 + np.maximum(0, order_congestion["Orders_Per_Picking_Picker"] - 12) * 0.012,
        1,
        1.65,
    )
    congestion_map = order_congestion.set_index("Order_ID")["Congestion_Factor"]
    items["Congestion_Factor"] = items["Order_ID"].map(congestion_map)
    items["Item_Line_Pick_Seconds"] = (
        items["Base_Line_Pick_Seconds"]
        * items["Experience_Factor"]
        * items["Congestion_Factor"]
    )
    items["Pick_Seconds_Per_Unit"] = (
        items["Item_Line_Pick_Seconds"] / items["Ordered_Quantity"]
    )

    return assignments_df, events_df, items


def add_daily_backlogs(operations: pd.DataFrame) -> pd.DataFrame:
    """Track daily put-away and audit completion against baseline requirements."""

    operations = operations.sort_values(["Store_ID", "Date", "Interval_ID"]).copy()
    operations["Putaway_Units_Completed"] = 0
    operations["Putaway_Backlog_Units"] = 0
    operations["Audit_Hours_Completed"] = operations["Audit_Hours_Capacity"]
    operations["Audit_Backlog_Hours"] = 0.0

    for (_, _), index in operations.groupby(["Store_ID", "Date"]).groups.items():
        remaining_putaway = DAILY_INBOUND_UNITS_PER_STORE
        remaining_audit = DAILY_AUDIT_HOURS_PER_STORE
        for row_index in sorted(index):
            putaway_done = min(
                remaining_putaway,
                int(operations.at[row_index, "Putaway_Units_Capacity"]),
            )
            remaining_putaway -= putaway_done
            audit_done = min(
                remaining_audit,
                float(operations.at[row_index, "Audit_Hours_Capacity"]),
            )
            remaining_audit -= audit_done
            operations.at[row_index, "Putaway_Units_Completed"] = putaway_done
            operations.at[row_index, "Putaway_Backlog_Units"] = remaining_putaway
            operations.at[row_index, "Audit_Hours_Completed"] = audit_done
            operations.at[row_index, "Audit_Backlog_Hours"] = remaining_audit

    return operations


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {"Table": table, "Validation_Test": test, "Passed": bool(passed), "Failure_Count": int(failures)}


def validate(
    orders: pd.DataFrame,
    original_items: pd.DataFrame,
    fulfilled_items: pd.DataFrame,
    assignments: pd.DataFrame,
    events: pd.DataFrame,
    workers: pd.DataFrame,
    operations: pd.DataFrame,
) -> pd.DataFrame:
    invalid_picker = (~assignments["Picker_ID"].isin(workers.loc[workers["Worker_Type"] == "Picker", "Worker_ID"])).sum()
    event_order_gaps = (~orders["Order_ID"].isin(events["Order_ID"])).sum()
    assignment_order_gaps = (~orders["Order_ID"].isin(assignments["Order_ID"])).sum()
    item_count_difference = abs(len(original_items) - len(fulfilled_items))
    invalid_sequence = (
        (events["Picking_Completed_Time"] < events["Picking_Started_Time"])
        | (events["Drop_Zone_Ready_Time"] < events["Picking_Completed_Time"])
    ).sum()
    negative_queue = (events["Pick_Queue_Minutes"] < 0).sum()
    duplicate_event_orders = events["Order_ID"].duplicated().sum()
    duplicate_assignments = assignments["Order_ID"].duplicated().sum()
    invalid_activity_split = (
        operations["Putaway_Pickers"] + operations["Audit_Pickers"] + operations["Picking_Pickers"]
        != operations["Active_Pickers"]
    ).sum()

    tests = [
        check("Picker Assignments", "Every order has one picker assignment", assignment_order_gaps == 0 and len(assignments) == len(orders), assignment_order_gaps),
        check("Picker Assignments", "One assignment exists per order", duplicate_assignments == 0, duplicate_assignments),
        check("Picker Assignments", "Every assigned worker is a picker", invalid_picker == 0, invalid_picker),
        check("Fulfilment Events", "Every order has one fulfilment event", event_order_gaps == 0 and len(events) == len(orders), event_order_gaps),
        check("Fulfilment Events", "One event row exists per order", duplicate_event_orders == 0, duplicate_event_orders),
        check("Fulfilment Events", "Timestamps follow the correct sequence", invalid_sequence == 0, invalid_sequence),
        check("Fulfilment Events", "Queue minutes are non-negative", negative_queue == 0, negative_queue),
        check("Fulfilled Items", "No order-item rows were lost or added", item_count_difference == 0, item_count_difference),
        check("Fulfilled Items", "Every item has a picker", fulfilled_items["Picking_Worker_ID"].notna().all(), fulfilled_items["Picking_Worker_ID"].isna().sum()),
        check("Picker Operations", "Activity allocations equal active pickers", invalid_activity_split == 0, invalid_activity_split),
    ]
    return pd.DataFrame(tests)


def main() -> None:
    require_files()
    rng = np.random.default_rng(SEED)

    stores = pd.read_csv(FILES["stores"])
    intervals = pd.read_csv(FILES["intervals"])
    interval_demand = pd.read_csv(FILES["interval_demand"], parse_dates=["Date"])
    workers = pd.read_csv(FILES["workers"], parse_dates=["Join_Date"])
    shifts = pd.read_csv(
        FILES["shifts"],
        parse_dates=["Shift_Date", "Scheduled_Start", "Scheduled_End", "Actual_Login", "Actual_Logout"],
    )
    products = pd.read_csv(FILES["products"])
    orders = pd.read_csv(
        FILES["orders"],
        parse_dates=["Order_Date", "Order_Created_Time"],
        dtype={"Promotion_ID": "string"},
        low_memory=False,
    )
    original_items = pd.read_csv(FILES["items"])

    active_map = active_pickers_by_interval(orders, intervals, workers, shifts)
    operations = build_picker_interval_operations(orders, interval_demand, intervals, active_map)
    operations = add_daily_backlogs(operations)
    prepared_items = prepare_item_base_times(original_items, products, rng)
    assignments, events, fulfilled_items = assign_orders_and_build_events(
        orders, prepared_items, workers, operations, rng
    )
    validation = validate(
        orders, original_items, fulfilled_items, assignments, events, workers, operations
    )

    print("\nSTORE FULFILMENT SUMMARY")
    print(f"Orders assigned: {len(assignments):,}")
    print(f"Item rows timed: {len(fulfilled_items):,}")
    print(f"Average PTPI: {fulfilled_items['Pick_Seconds_Per_Unit'].mean():.2f} seconds")
    print(f"Average pick queue: {events['Pick_Queue_Minutes'].mean():.2f} minutes")
    print(f"Average picking time: {events['Picking_Minutes'].mean():.2f} minutes")
    print(f"95th percentile pick queue: {events['Pick_Queue_Minutes'].quantile(0.95):.2f} minutes")

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(f"{status} | {row.Table} | {row.Validation_Test} | Failures: {row.Failure_Count}")

    if not validation["Passed"].all():
        raise ValueError("Store-fulfilment validation failed. Review the report above.")

    operations.to_csv(RAW_DATA_DIR / "picker_interval_operations.csv", index=False)
    assignments.to_csv(RAW_DATA_DIR / "order_picker_assignments.csv", index=False)
    fulfilled_items.to_csv(RAW_DATA_DIR / "order_items_fulfilled.csv", index=False)
    events.to_csv(RAW_DATA_DIR / "fulfilment_events.csv", index=False)
    validation.to_csv(VALIDATION_DIR / "store_fulfilment_validation.csv", index=False)

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "picker_interval_operations.csv")
    print(RAW_DATA_DIR / "order_picker_assignments.csv")
    print(RAW_DATA_DIR / "order_items_fulfilled.csv")
    print(RAW_DATA_DIR / "fulfilment_events.csv")
    print(VALIDATION_DIR / "store_fulfilment_validation.csv")


if __name__ == "__main__":
    main()
