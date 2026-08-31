"""Generate quality issues and classify SLA-breach root causes.

Prerequisites: run scripts 01-07 first.

Outputs:
    data/raw/quality_issues.csv
    data/raw/sla_root_cause_analysis.csv
    data/validation/quality_root_cause_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/08_generate_quality_and_root_causes.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


SEED = 48

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

FILES = {
    "orders": RAW_DATA_DIR / "orders.csv",
    "items": RAW_DATA_DIR / "order_items_fulfilled.csv",
    "products": RAW_DATA_DIR / "products.csv",
    "workers": RAW_DATA_DIR / "workers.csv",
    "picker_operations": RAW_DATA_DIR / "picker_interval_operations.csv",
    "fulfilment": RAW_DATA_DIR / "fulfilment_events.csv",
    "rider_assignments": RAW_DATA_DIR / "order_rider_assignments.csv",
    "delivery": RAW_DATA_DIR / "delivery_events.csv",
}


def require_files() -> None:
    missing = [path for path in FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-07 first."
        )


def build_sla_root_causes(
    orders: pd.DataFrame,
    fulfilment: pd.DataFrame,
    delivery: pd.DataFrame,
) -> pd.DataFrame:
    """Classify the most likely delay stage for every order."""

    analysis = (
        orders[
            [
                "Order_ID",
                "Store_ID",
                "Order_Date",
                "Interval_ID",
                "Store_Date_Interval_ID",
                "Total_Units",
                "Delivery_Distance_KM",
            ]
        ]
        .merge(fulfilment, on="Order_ID", how="left", validate="one_to_one")
        .merge(delivery, on="Order_ID", how="left", validate="one_to_one")
    )

    analysis["Expected_Picking_Minutes"] = (
        analysis["Total_Units"] * 15.0 / 60.0
    )
    speed = analysis["Weather_Type"].map(
        {"Clear": 22.0, "Cloudy": 21.0, "Light Rain": 18.0, "Heavy Rain": 14.0}
    )
    analysis["Expected_Last_Mile_Minutes"] = (
        analysis["Delivery_Distance_KM"] / speed * 60 + 1.5
    )

    def classify(row) -> tuple[str, str]:
        if not row.SLA_Breach_Flag:
            return "Within SLA", "No breach"

        candidates = {
            "Picker Capacity / Queue": max(0.0, row.Pick_Queue_Minutes - 1.0),
            "Picking Productivity / Basket": max(
                0.0, row.Picking_Minutes - row.Expected_Picking_Minutes * 1.30
            ),
            "Drop-Zone Handoff": max(0.0, row.Drop_Zone_Delay_Minutes - 1.0),
            "Rider Supply / Availability": max(
                0.0, row.Ready_Order_Wait_For_Rider_Minutes - 1.5
            ),
            "Store Not Ready for Rider": max(
                0.0, row.Rider_Wait_For_Order_Minutes - 1.5
            ),
            "Last-Mile Travel": max(
                0.0, row.Last_Mile_Minutes - row.Expected_Last_Mile_Minutes * 1.25
            ),
        }
        ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
        if ranked[0][1] <= 0:
            return "Multi-Stage / Further Investigation", "No single stage exceeded threshold"
        primary = ranked[0][0]
        secondary = ranked[1][0] if ranked[1][1] > 0 else "No material secondary cause"
        return primary, secondary

    classifications = analysis.apply(classify, axis=1, result_type="expand")
    analysis[["Probable_Primary_Root_Cause", "Probable_Secondary_Root_Cause"]] = classifications
    analysis["Root_Cause_Status"] = np.where(
        analysis["SLA_Breach_Flag"], "Probable - Review Required", "Not Applicable"
    )

    return analysis[
        [
            "Order_ID",
            "Store_ID",
            "Order_Date",
            "Interval_ID",
            "Store_Date_Interval_ID",
            "Total_Units",
            "Displayed_SLA_Minutes",
            "Total_Delivery_Minutes",
            "SLA_Breach_Flag",
            "SLA_Breach_Minutes",
            "Pick_Queue_Minutes",
            "Picking_Minutes",
            "Drop_Zone_Delay_Minutes",
            "Rider_Wait_For_Order_Minutes",
            "Ready_Order_Wait_For_Rider_Minutes",
            "Last_Mile_Minutes",
            "Probable_Primary_Root_Cause",
            "Probable_Secondary_Root_Cause",
            "Root_Cause_Status",
        ]
    ]


def build_item_quality_issues(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    products: pd.DataFrame,
    workers: pd.DataFrame,
    picker_operations: pd.DataFrame,
    delivery: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate connected missing, wrong, and damaged-item cases."""

    picker_experience = workers[
        ["Worker_ID", "Experience_Band"]
    ].rename(columns={"Worker_ID": "Picking_Worker_ID"})

    quality_base = (
        items.merge(
            orders[["Order_ID", "Store_ID", "Store_Date_Interval_ID"]],
            on="Order_ID",
            how="left",
            validate="many_to_one",
        )
        .merge(
            products[
                ["Product_ID", "Fragile_Flag", "Similar_Packaging_Flag"]
            ],
            on="Product_ID",
            how="left",
            validate="many_to_one",
        )
        .merge(
            picker_experience,
            on="Picking_Worker_ID",
            how="left",
            validate="many_to_one",
        )
        .merge(
            picker_operations[
                [
                    "Store_Date_Interval_ID",
                    "Putaway_Backlog_Units",
                    "Audit_Backlog_Hours",
                    "Orders_Per_Picking_Picker",
                ]
            ],
            on="Store_Date_Interval_ID",
            how="left",
            validate="many_to_one",
        )
        .merge(
            delivery[["Order_ID", "Weather_Type", "Last_Mile_Minutes"]],
            on="Order_ID",
            how="left",
            validate="many_to_one",
        )
    )

    audit_risk = np.clip(quality_base["Audit_Backlog_Hours"] / 50.0, 0, 1)
    putaway_risk = np.clip(quality_base["Putaway_Backlog_Units"] / 10000.0, 0, 1)
    congestion_risk = np.clip(
        (quality_base["Orders_Per_Picking_Picker"] - 12) / 12,
        0,
        1,
    ).fillna(0)
    new_picker = quality_base["Experience_Band"].eq("New").astype(float)

    missing_probability = np.clip(
        0.0020 + 0.0040 * audit_risk + 0.0040 * putaway_risk,
        0,
        0.018,
    )
    wrong_probability = np.clip(
        0.0010
        + 0.0040 * quality_base["Similar_Packaging_Flag"].astype(float)
        + 0.0020 * new_picker
        + 0.0020 * congestion_risk,
        0,
        0.015,
    )
    damaged_probability = np.clip(
        0.0008
        + 0.0060 * quality_base["Fragile_Flag"].astype(float)
        + 0.0015 * quality_base["Weather_Type"].eq("Heavy Rain").astype(float)
        + 0.0015 * quality_base["Last_Mile_Minutes"].gt(10).astype(float),
        0,
        0.015,
    )

    draw = rng.random(len(quality_base))
    missing = draw < missing_probability
    wrong = (~missing) & (draw < missing_probability + wrong_probability)
    damaged = (~missing) & (~wrong) & (
        draw < missing_probability + wrong_probability + damaged_probability
    )

    issue_type = np.select(
        [missing, wrong, damaged],
        ["Missing Item", "Wrong Item", "Damaged Item"],
        default="No Issue",
    )
    quality_base["Issue_Type"] = issue_type
    issues = quality_base.loc[quality_base["Issue_Type"] != "No Issue"].copy()

    def probable_stage(row) -> str:
        if row.Issue_Type == "Missing Item":
            if row.Audit_Backlog_Hours > 25 or row.Putaway_Backlog_Units > 5000:
                return "Inventory Accuracy / Putaway"
            return "Picking"
        if row.Issue_Type == "Wrong Item":
            if row.Similar_Packaging_Flag:
                return "Shelf Placement / Picking Verification"
            return "Picking"
        if row.Issue_Type == "Damaged Item":
            if row.Fragile_Flag and row.Last_Mile_Minutes > 10:
                return "Rider Handling / Last Mile"
            return "Putaway / Picking Handling"
        return "Further Investigation"

    issues["Probable_Process_Stage"] = issues.apply(probable_stage, axis=1)
    issues["Probable_Worker_ID"] = np.where(
        issues["Issue_Type"].isin(["Missing Item", "Wrong Item"]),
        issues["Picking_Worker_ID"],
        pd.NA,
    )
    issues["Financial_Impact_INR"] = issues["Line_Value_INR"]
    issues["Root_Cause_Status"] = "Unconfirmed"
    issues["Resolution_Type"] = rng.choice(
        ["Refund", "Replacement"], size=len(issues), p=[0.72, 0.28]
    )
    issues.insert(0, "Issue_ID", [f"QI{number:08d}" for number in range(1, len(issues) + 1)])

    return issues[
        [
            "Issue_ID",
            "Order_ID",
            "Order_Item_ID",
            "Store_ID",
            "Product_ID",
            "Issue_Type",
            "Financial_Impact_INR",
            "Probable_Process_Stage",
            "Probable_Worker_ID",
            "Root_Cause_Status",
            "Resolution_Type",
        ]
    ]


def build_handoff_issues(
    orders: pd.DataFrame,
    fulfilment: pd.DataFrame,
    delivery: pd.DataFrame,
    rider_assignments: pd.DataFrame,
    start_number: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate rare wrong-handoff cases; stacking remains out of scope."""

    base = (
        orders[["Order_ID", "Store_ID"]]
        .merge(fulfilment[["Order_ID", "Drop_Zone_Delay_Minutes"]], on="Order_ID", validate="one_to_one")
        .merge(delivery[["Order_ID", "Pickup_Verification_Minutes"]], on="Order_ID", validate="one_to_one")
        .merge(rider_assignments[["Order_ID", "Rider_ID"]], on="Order_ID", validate="one_to_one")
    )
    probability = (
        0.0002
        + 0.0008 * base["Drop_Zone_Delay_Minutes"].gt(1.5).astype(float)
        + 0.0006 * base["Pickup_Verification_Minutes"].lt(0.5).astype(float)
    )
    cases = base.loc[rng.random(len(base)) < probability].copy()
    cases["Issue_ID"] = [
        f"QI{number:08d}"
        for number in range(start_number, start_number + len(cases))
    ]
    cases["Order_Item_ID"] = pd.NA
    cases["Product_ID"] = pd.NA
    cases["Issue_Type"] = "Wrong Order Handoff"
    cases["Financial_Impact_INR"] = 150.0
    cases["Probable_Process_Stage"] = "Drop Zone / Rider Verification"
    cases["Probable_Worker_ID"] = cases["Rider_ID"]
    cases["Root_Cause_Status"] = "Unconfirmed"
    cases["Resolution_Type"] = "Refund"
    return cases[
        [
            "Issue_ID", "Order_ID", "Order_Item_ID", "Store_ID", "Product_ID",
            "Issue_Type", "Financial_Impact_INR", "Probable_Process_Stage",
            "Probable_Worker_ID", "Root_Cause_Status", "Resolution_Type",
        ]
    ]


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {"Table": table, "Validation_Test": test, "Passed": bool(passed), "Failure_Count": int(failures)}


def validate(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    workers: pd.DataFrame,
    issues: pd.DataFrame,
    root_causes: pd.DataFrame,
) -> pd.DataFrame:
    invalid_issue_order = (~issues["Order_ID"].isin(orders["Order_ID"])).sum()
    populated_item = issues["Order_Item_ID"].notna()
    invalid_issue_item = (~issues.loc[populated_item, "Order_Item_ID"].isin(items["Order_Item_ID"])).sum()
    populated_worker = issues["Probable_Worker_ID"].notna()
    invalid_worker = (~issues.loc[populated_worker, "Probable_Worker_ID"].isin(workers["Worker_ID"])).sum()
    missing_root_cause = root_causes["Probable_Primary_Root_Cause"].isna().sum()
    breach_with_within_sla_cause = (
        root_causes["SLA_Breach_Flag"]
        & root_causes["Probable_Primary_Root_Cause"].eq("Within SLA")
    ).sum()
    nonbreach_with_breach_cause = (
        ~root_causes["SLA_Breach_Flag"]
        & ~root_causes["Probable_Primary_Root_Cause"].eq("Within SLA")
    ).sum()
    negative_impact = (issues["Financial_Impact_INR"] < 0).sum()

    tests = [
        check("Quality Issues", "Issue_ID is unique", issues["Issue_ID"].is_unique, issues["Issue_ID"].duplicated().sum()),
        check("Quality Issues", "Every Order_ID exists", invalid_issue_order == 0, invalid_issue_order),
        check("Quality Issues", "Every populated Order_Item_ID exists", invalid_issue_item == 0, invalid_issue_item),
        check("Quality Issues", "Every probable worker exists", invalid_worker == 0, invalid_worker),
        check("Quality Issues", "Financial impact is non-negative", negative_impact == 0, negative_impact),
        check("SLA Root Causes", "One root-cause row exists per order", len(root_causes) == len(orders) and root_causes["Order_ID"].is_unique, abs(len(orders) - len(root_causes))),
        check("SLA Root Causes", "Every row has a primary classification", missing_root_cause == 0, missing_root_cause),
        check("SLA Root Causes", "Breaches are not classified Within SLA", breach_with_within_sla_cause == 0, breach_with_within_sla_cause),
        check("SLA Root Causes", "Non-breaches are classified Within SLA", nonbreach_with_breach_cause == 0, nonbreach_with_breach_cause),
    ]
    return pd.DataFrame(tests)


def main() -> None:
    require_files()
    rng = np.random.default_rng(SEED)

    orders = pd.read_csv(
        FILES["orders"], parse_dates=["Order_Date", "Order_Created_Time"],
        dtype={"Promotion_ID": "string"}, low_memory=False,
    )
    items = pd.read_csv(FILES["items"], low_memory=False)
    products = pd.read_csv(FILES["products"])
    workers = pd.read_csv(FILES["workers"])
    picker_operations = pd.read_csv(FILES["picker_operations"], parse_dates=["Date"])
    fulfilment = pd.read_csv(
        FILES["fulfilment"],
        parse_dates=["Picking_Started_Time", "Picking_Completed_Time", "Drop_Zone_Ready_Time"],
    )
    rider_assignments = pd.read_csv(FILES["rider_assignments"])
    delivery = pd.read_csv(
        FILES["delivery"],
        parse_dates=["Rider_Arrival_Time", "Rider_Pickup_Time", "Delivered_Time"],
    )

    root_causes = build_sla_root_causes(orders, fulfilment, delivery)
    item_issues = build_item_quality_issues(
        orders, items, products, workers, picker_operations, delivery, rng
    )
    handoff_issues = build_handoff_issues(
        orders, fulfilment, delivery, rider_assignments, len(item_issues) + 1, rng
    )
    issues = pd.concat([item_issues, handoff_issues], ignore_index=True)
    validation = validate(orders, items, workers, issues, root_causes)

    print("\nQUALITY SUMMARY")
    print(f"Orders analyzed: {len(orders):,}")
    print(f"Quality issues: {len(issues):,}")
    print(f"Orders with an issue: {issues['Order_ID'].nunique():,}")
    print(f"Issue-order rate: {issues['Order_ID'].nunique() / len(orders):.2%}")
    print(issues["Issue_Type"].value_counts().to_string())

    print("\nSLA ROOT-CAUSE SUMMARY")
    print(
        root_causes.loc[root_causes["SLA_Breach_Flag"], "Probable_Primary_Root_Cause"]
        .value_counts()
        .to_string()
    )

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(f"{status} | {row.Table} | {row.Validation_Test} | Failures: {row.Failure_Count}")

    if not validation["Passed"].all():
        raise ValueError("Quality/root-cause validation failed. Review the report above.")

    issues.to_csv(RAW_DATA_DIR / "quality_issues.csv", index=False)
    root_causes.to_csv(RAW_DATA_DIR / "sla_root_cause_analysis.csv", index=False)
    validation.to_csv(
        VALIDATION_DIR / "quality_root_cause_validation.csv", index=False
    )

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "quality_issues.csv")
    print(RAW_DATA_DIR / "sla_root_cause_analysis.csv")
    print(VALIDATION_DIR / "quality_root_cause_validation.csv")


if __name__ == "__main__":
    main()
