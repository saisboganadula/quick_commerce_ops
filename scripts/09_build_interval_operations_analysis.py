"""Build the primary store-date-30-minute analytical fact table.

Prerequisites: run scripts 01-08 first.

Outputs:
    data/processed/interval_operations_analysis.csv
    data/validation/interval_operations_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/09_build_interval_operations_analysis.py
"""

from pathlib import Path

import numpy as np
import pandas as pd  # pyright: ignore[reportMissingModuleSource]


TARGET_RIDER_UTILIZATION = 0.70
TARGET_PICKER_UTILIZATION = 0.70

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

FILES = {
    "intervals": RAW_DATA_DIR / "time_intervals.csv",
    "orders": RAW_DATA_DIR / "orders.csv",
    "shifts": RAW_DATA_DIR / "worker_shifts.csv",
    "workers": RAW_DATA_DIR / "workers.csv",
    "picker_ops": RAW_DATA_DIR / "picker_interval_operations.csv",
    "fulfilment": RAW_DATA_DIR / "fulfilment_events.csv",
    "rider_assignments": RAW_DATA_DIR / "order_rider_assignments.csv",
    "delivery": RAW_DATA_DIR / "delivery_events.csv",
    "quality": RAW_DATA_DIR / "quality_issues.csv",
    "root_causes": RAW_DATA_DIR / "sla_root_cause_analysis.csv",
}


def require_files() -> None:
    missing = [path for path in FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-08 first."
        )


def timestamp_to_interval_id(timestamp: pd.Timestamp) -> int:
    """Map a timestamp to an interval from 1 through 48."""

    return timestamp.hour * 2 + timestamp.minute // 30 + 1


def allocate_duration_to_intervals(
    records: pd.DataFrame,
    start_column: str,
    end_column: str,
    value_name: str,
    id_columns: list[str],
) -> pd.DataFrame:
    """Split event duration across every 30-minute interval it overlaps."""

    allocations = []
    for row in records.itertuples(index=False):
        row_dict = row._asdict()
        start = pd.Timestamp(row_dict[start_column])
        end = pd.Timestamp(row_dict[end_column])
        cursor = start

        while cursor < end:
            interval_start = cursor.floor("30min")
            interval_end = interval_start + pd.Timedelta(minutes=30)
            overlap_end = min(end, interval_end)
            minutes = (overlap_end - cursor).total_seconds() / 60

            allocation = {column: row_dict[column] for column in id_columns}
            allocation.update(
                {
                    "Activity_Date": interval_start.normalize(),
                    "Interval_ID": timestamp_to_interval_id(interval_start),
                    value_name: minutes,
                }
            )
            allocations.append(allocation)
            cursor = overlap_end

    return pd.DataFrame(allocations)


def build_active_rider_supply(
    base: pd.DataFrame,
    shifts: pd.DataFrame,
    workers: pd.DataFrame,
    intervals: pd.DataFrame,
) -> pd.DataFrame:
    """Count riders logged in during each interval."""

    rider_ids = set(workers.loc[workers["Worker_Type"] == "Rider", "Worker_ID"])
    rider_shifts = shifts.loc[
        shifts["Worker_ID"].isin(rider_ids)
        & shifts["Actual_Login"].notna()
        & shifts["Actual_Logout"].notna()
    ]
    interval_minutes = intervals.set_index("Interval_ID")["Start_Minute"].to_dict()

    counts = []
    for row in base[["Store_ID", "Date", "Interval_ID"]].itertuples(index=False):
        start = pd.Timestamp(row.Date) + pd.Timedelta(
            minutes=int(interval_minutes[row.Interval_ID])
        )
        end = start + pd.Timedelta(minutes=30)
        active = (
            (rider_shifts["Store_ID"] == row.Store_ID)
            & (rider_shifts["Actual_Login"] < end)
            & (rider_shifts["Actual_Logout"] > start)
        ).sum()
        counts.append(int(active))

    supply = base[["Store_Date_Interval_ID"]].copy()
    supply["Active_Riders"] = counts
    return supply


def aggregate_order_metrics(
    orders: pd.DataFrame,
    fulfilment: pd.DataFrame,
    delivery: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate service, cost, and order metrics to interval grain."""

    journey = (
        orders.merge(fulfilment, on="Order_ID", validate="one_to_one")
        .merge(delivery, on="Order_ID", validate="one_to_one")
    )
    return journey.groupby("Store_Date_Interval_ID", as_index=False).agg(
        Orders=("Order_ID", "count"),
        Units=("Total_Units", "sum"),
        Revenue_INR=("Order_Value_INR", "sum"),
        Average_Units_Per_Order=("Total_Units", "mean"),
        Average_Order_Value_INR=("Order_Value_INR", "mean"),
        Average_Displayed_SLA_Min=("Displayed_SLA_Minutes", "mean"),
        Average_Total_Delivery_Min=("Total_Delivery_Minutes", "mean"),
        SLA_Breaches=("SLA_Breach_Flag", "sum"),
        Average_Breach_Min=("SLA_Breach_Minutes", "mean"),
        Average_Pick_Queue_Min=("Pick_Queue_Minutes", "mean"),
        Average_Picking_Min=("Picking_Minutes", "mean"),
        Average_Drop_Zone_Delay_Min=("Drop_Zone_Delay_Minutes", "mean"),
        Average_Rider_Wait_For_Order_Min=("Rider_Wait_For_Order_Minutes", "mean"),
        Average_Ready_Order_Wait_Min=("Ready_Order_Wait_For_Rider_Minutes", "mean"),
        Average_Last_Mile_Min=("Last_Mile_Minutes", "mean"),
        Rider_Cost_INR=("Total_Rider_Cost_INR", "sum"),
    )


def aggregate_quality(
    orders: pd.DataFrame,
    quality: pd.DataFrame,
) -> pd.DataFrame:
    joined = quality.merge(
        orders[["Order_ID", "Store_Date_Interval_ID"]],
        on="Order_ID",
        how="left",
        validate="many_to_one",
    )
    joined["Missing_Item"] = joined["Issue_Type"].eq("Missing Item").astype(int)
    joined["Wrong_Item"] = joined["Issue_Type"].eq("Wrong Item").astype(int)
    joined["Damaged_Item"] = joined["Issue_Type"].eq("Damaged Item").astype(int)
    joined["Wrong_Handoff"] = joined["Issue_Type"].eq("Wrong Order Handoff").astype(int)

    return joined.groupby("Store_Date_Interval_ID", as_index=False).agg(
        Quality_Issue_Count=("Issue_ID", "count"),
        Orders_With_Quality_Issue=("Order_ID", "nunique"),
        Missing_Item_Count=("Missing_Item", "sum"),
        Wrong_Item_Count=("Wrong_Item", "sum"),
        Damaged_Item_Count=("Damaged_Item", "sum"),
        Wrong_Handoff_Count=("Wrong_Handoff", "sum"),
        Quality_Financial_Impact_INR=("Financial_Impact_INR", "sum"),
    )


def aggregate_root_causes(root_causes: pd.DataFrame) -> pd.DataFrame:
    breaches = root_causes.loc[root_causes["SLA_Breach_Flag"]].copy()
    counts = (
        breaches.groupby(
            ["Store_Date_Interval_ID", "Probable_Primary_Root_Cause"],
            as_index=False,
        )
        .size()
        .rename(columns={"size": "Root_Cause_Count"})
    )
    if counts.empty:
        return pd.DataFrame(
            columns=["Store_Date_Interval_ID", "Dominant_Root_Cause", "Dominant_Root_Cause_Count"]
        )

    dominant = counts.loc[
        counts.groupby("Store_Date_Interval_ID")["Root_Cause_Count"].idxmax()
    ].rename(
        columns={
            "Probable_Primary_Root_Cause": "Dominant_Root_Cause",
            "Root_Cause_Count": "Dominant_Root_Cause_Count",
        }
    )
    return dominant[
        ["Store_Date_Interval_ID", "Dominant_Root_Cause", "Dominant_Root_Cause_Count"]
    ]


def add_recommendations(analysis: pd.DataFrame) -> pd.DataFrame:
    """Create a transparent first-pass operational action rule."""

    def recommend(row) -> str:
        if row.SLA_Breach_Rate >= 0.15 and row.Dominant_Root_Cause == "Rider Supply / Availability":
            return "Add or rebalance riders in this interval"
        if row.SLA_Breach_Rate >= 0.15 and row.Dominant_Root_Cause in {
            "Picker Capacity / Queue", "Picking Productivity / Basket", "Store Not Ready for Rider"
        }:
            return "Protect or increase picker capacity"
        if row.Putaway_Backlog_Units > 0 and row.Interval_ID >= 13:
            return "Recover overdue put-away workload"
        if row.Audit_Backlog_Hours > 0 and row.Interval_ID >= 41:
            return "Recover audit backlog before day close"
        if row.Quality_Issue_Rate >= 0.05:
            return "Investigate inventory and order-quality process"
        if row.Rider_Supply_Gap < 0:
            return "Review rider roster against workload"
        return "Monitor - no immediate intervention"

    analysis["Recommended_Action"] = analysis.apply(recommend, axis=1)
    return analysis


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {"Table": table, "Validation_Test": test, "Passed": bool(passed), "Failure_Count": int(failures)}


def main() -> None:
    require_files()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    intervals = pd.read_csv(FILES["intervals"])
    orders = pd.read_csv(
        FILES["orders"], parse_dates=["Order_Date", "Order_Created_Time"],
        dtype={"Promotion_ID": "string"}, low_memory=False,
    )
    shifts = pd.read_csv(
        FILES["shifts"],
        parse_dates=["Shift_Date", "Scheduled_Start", "Scheduled_End", "Actual_Login", "Actual_Logout"],
    )
    workers = pd.read_csv(FILES["workers"])
    picker_ops = pd.read_csv(FILES["picker_ops"], parse_dates=["Date"])
    fulfilment = pd.read_csv(
        FILES["fulfilment"],
        parse_dates=["Picking_Started_Time", "Picking_Completed_Time", "Drop_Zone_Ready_Time"],
    )
    rider_assignments = pd.read_csv(FILES["rider_assignments"])
    delivery = pd.read_csv(
        FILES["delivery"],
        parse_dates=["Rider_Arrival_Time", "Rider_Pickup_Time", "Delivered_Time"],
    )
    quality = pd.read_csv(FILES["quality"], low_memory=False)
    root_causes = pd.read_csv(FILES["root_causes"], parse_dates=["Order_Date"])

    base = picker_ops.copy()
    order_metrics = aggregate_order_metrics(orders, fulfilment, delivery)
    quality_metrics = aggregate_quality(orders, quality)
    dominant_causes = aggregate_root_causes(root_causes)
    rider_supply = build_active_rider_supply(base, shifts, workers, intervals)

    picker_activity_source = fulfilment.merge(
        orders[["Order_ID", "Store_ID"]], on="Order_ID", validate="one_to_one"
    )
    picker_minutes = allocate_duration_to_intervals(
        picker_activity_source,
        "Picking_Started_Time",
        "Picking_Completed_Time",
        "Picker_Active_Minutes",
        ["Store_ID"],
    ).groupby(["Store_ID", "Activity_Date", "Interval_ID"], as_index=False)["Picker_Active_Minutes"].sum()

    rider_activity_source = (
        delivery.merge(rider_assignments[["Order_ID", "Rider_ID"]], on="Order_ID", validate="one_to_one")
        .merge(orders[["Order_ID", "Store_ID"]], on="Order_ID", validate="one_to_one")
    )
    rider_activity_source["Round_Trip_End"] = (
        rider_activity_source["Rider_Pickup_Time"]
        + pd.to_timedelta(rider_activity_source["Round_Trip_Busy_Minutes"], unit="m")
    )
    rider_minutes = allocate_duration_to_intervals(
        rider_activity_source,
        "Rider_Pickup_Time",
        "Round_Trip_End",
        "Rider_Busy_Minutes",
        ["Store_ID", "Rider_ID"],
    ).groupby(["Store_ID", "Activity_Date", "Interval_ID"], as_index=False).agg(
        Rider_Busy_Minutes=("Rider_Busy_Minutes", "sum"),
        Busy_Riders=("Rider_ID", "nunique"),
    )

    analysis = (
        base.merge(order_metrics, on="Store_Date_Interval_ID", how="left", validate="one_to_one")
        .merge(rider_supply, on="Store_Date_Interval_ID", how="left", validate="one_to_one")
        .merge(quality_metrics, on="Store_Date_Interval_ID", how="left", validate="one_to_one")
        .merge(dominant_causes, on="Store_Date_Interval_ID", how="left", validate="one_to_one")
        .merge(
            picker_minutes,
            left_on=["Store_ID", "Date", "Interval_ID"],
            right_on=["Store_ID", "Activity_Date", "Interval_ID"],
            how="left",
            validate="one_to_one",
        )
        .drop(columns=["Activity_Date"])
        .merge(
            rider_minutes,
            left_on=["Store_ID", "Date", "Interval_ID"],
            right_on=["Store_ID", "Activity_Date", "Interval_ID"],
            how="left",
            validate="one_to_one",
        )
        .drop(columns=["Activity_Date"])
    )

    numeric_fill_columns = [
        "Quality_Issue_Count", "Orders_With_Quality_Issue", "Missing_Item_Count",
        "Wrong_Item_Count", "Damaged_Item_Count", "Wrong_Handoff_Count",
        "Quality_Financial_Impact_INR", "Picker_Active_Minutes", "Rider_Busy_Minutes",
        "Busy_Riders",
    ]
    analysis[numeric_fill_columns] = analysis[numeric_fill_columns].fillna(0)
    analysis["Dominant_Root_Cause"] = analysis["Dominant_Root_Cause"].fillna("No SLA Breach")
    analysis["Dominant_Root_Cause_Count"] = analysis["Dominant_Root_Cause_Count"].fillna(0)

    analysis["SLA_Breach_Rate"] = np.where(
        analysis["Orders"] > 0, analysis["SLA_Breaches"] / analysis["Orders"], 0
    )
    analysis["Quality_Issue_Rate"] = np.where(
        analysis["Orders"] > 0,
        analysis["Orders_With_Quality_Issue"] / analysis["Orders"],
        0,
    )
    analysis["Rider_Cost_Per_Order_INR"] = np.where(
        analysis["Orders"] > 0, analysis["Rider_Cost_INR"] / analysis["Orders"], 0
    )
    analysis["Picker_Utilization"] = np.where(
        analysis["Picking_Pickers"] > 0,
        analysis["Picker_Active_Minutes"] / (analysis["Picking_Pickers"] * 30),
        0,
    )
    analysis["Rider_Utilization"] = np.where(
        analysis["Active_Riders"] > 0,
        analysis["Rider_Busy_Minutes"] / (analysis["Active_Riders"] * 30),
        0,
    )
    analysis["Required_Riders_At_Target"] = np.ceil(
        analysis["Rider_Busy_Minutes"] / (30 * TARGET_RIDER_UTILIZATION)
    ).astype(int)
    analysis["Rider_Supply_Gap"] = (
        analysis["Active_Riders"] - analysis["Required_Riders_At_Target"]
    )
    analysis["Required_Pickers_At_Target"] = np.ceil(
        analysis["Picker_Active_Minutes"] / (30 * TARGET_PICKER_UTILIZATION)
    ).astype(int)
    analysis["Picker_Supply_Gap"] = (
        analysis["Picking_Pickers"] - analysis["Required_Pickers_At_Target"]
    )
    analysis = add_recommendations(analysis)

    # The analytical grid must retain zero-order intervals, so the expected row
    # count comes from the interval base rather than from Orders.
    expected_rows = len(base)
    order_reconciliation = int(analysis["Orders"].sum()) == len(orders)
    cost_reconciliation = np.isclose(
        analysis["Rider_Cost_INR"].sum(), delivery["Total_Rider_Cost_INR"].sum()
    )
    validation = pd.DataFrame(
        [
            check("Interval Operations", "Store-date-interval key is unique", analysis["Store_Date_Interval_ID"].is_unique, analysis["Store_Date_Interval_ID"].duplicated().sum()),
            check("Interval Operations", "Expected interval rows are present", len(analysis) == expected_rows, abs(expected_rows - len(analysis))),
            check("Interval Operations", "Orders reconcile to raw Orders", order_reconciliation, int(not order_reconciliation)),
            check("Interval Operations", "Rider costs reconcile to Delivery Events", cost_reconciliation, int(not cost_reconciliation)),
            check("Interval Operations", "SLA breach rate is between 0 and 1", analysis["SLA_Breach_Rate"].between(0, 1).all(), (~analysis["SLA_Breach_Rate"].between(0, 1)).sum()),
            check("Interval Operations", "Quality rate is between 0 and 1", analysis["Quality_Issue_Rate"].between(0, 1).all(), (~analysis["Quality_Issue_Rate"].between(0, 1)).sum()),
            check("Interval Operations", "Utilization values are non-negative", (analysis[["Picker_Utilization", "Rider_Utilization"]] >= 0).all().all(), (analysis[["Picker_Utilization", "Rider_Utilization"]] < 0).sum().sum()),
            check("Interval Operations", "Recommended action is populated", analysis["Recommended_Action"].notna().all(), analysis["Recommended_Action"].isna().sum()),
        ]
    )

    print("\nINTERVAL OPERATIONS SUMMARY")
    print(f"Rows: {len(analysis):,}")
    print(f"Orders reconciled: {analysis['Orders'].sum():,}")
    print(f"Network SLA breach rate: {analysis['SLA_Breaches'].sum() / analysis['Orders'].sum():.2%}")
    print(f"Network quality issue rate: {analysis['Orders_With_Quality_Issue'].sum() / analysis['Orders'].sum():.2%}")
    print(f"Average rider utilization: {analysis['Rider_Utilization'].mean():.2%}")
    print(f"Average picker utilization: {analysis['Picker_Utilization'].mean():.2%}")

    print("\nTOP RECOMMENDED ACTIONS")
    print(analysis["Recommended_Action"].value_counts().to_string())

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(f"{status} | {row.Table} | {row.Validation_Test} | Failures: {row.Failure_Count}")

    if not validation["Passed"].all():
        raise ValueError("Interval-operations validation failed. Review the report above.")

    analysis.to_csv(PROCESSED_DIR / "interval_operations_analysis.csv", index=False)
    validation.to_csv(VALIDATION_DIR / "interval_operations_validation.csv", index=False)

    print("\nFILES GENERATED")
    print(PROCESSED_DIR / "interval_operations_analysis.csv")
    print(VALIDATION_DIR / "interval_operations_validation.csv")


if __name__ == "__main__":
    main()
