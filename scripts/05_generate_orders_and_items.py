"""Generate anonymous products, promotions, orders, and order items.

The 60-day interval dataset is retained, but order-level detail is generated for
the first 14 days so the resulting Excel/Power Query project remains practical.

Prerequisites: run scripts 01-04 first.

Outputs:
    data/raw/products.csv
    data/raw/promotions.csv
    data/raw/orders.csv
    data/raw/order_items.csv
    data/validation/orders_items_validation.csv

Run from the quick_commerce_ops project directory:
    python3 scripts/05_generate_orders_and_items.py
"""

from pathlib import Path

import numpy as np
import pandas as pd


SEED = 45
DETAIL_DAYS = 14
PRODUCT_COUNT = 300

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
VALIDATION_DIR = PROJECT_ROOT / "data" / "validation"

STORES_FILE = RAW_DATA_DIR / "stores.csv"
INTERVAL_DEMAND_FILE = RAW_DATA_DIR / "interval_demand.csv"
DAILY_CONDITIONS_FILE = RAW_DATA_DIR / "daily_store_conditions.csv"


CATEGORY_RULES = {
    "Staples": {
        "weight": 0.18,
        "price_range": (30, 450),
        "storage": "Ambient",
        "complexity": "Low",
        "fragile_probability": 0.02,
    },
    "Snacks": {
        "weight": 0.17,
        "price_range": (10, 250),
        "storage": "Ambient",
        "complexity": "Low",
        "fragile_probability": 0.08,
    },
    "Beverages": {
        "weight": 0.13,
        "price_range": (20, 300),
        "storage": "Ambient",
        "complexity": "Medium",
        "fragile_probability": 0.18,
    },
    "Dairy": {
        "weight": 0.12,
        "price_range": (25, 350),
        "storage": "Chilled",
        "complexity": "Medium",
        "fragile_probability": 0.08,
    },
    "Produce": {
        "weight": 0.12,
        "price_range": (20, 300),
        "storage": "Ambient",
        "complexity": "High",
        "fragile_probability": 0.25,
    },
    "Frozen": {
        "weight": 0.08,
        "price_range": (60, 500),
        "storage": "Frozen",
        "complexity": "High",
        "fragile_probability": 0.05,
    },
    "Personal Care": {
        "weight": 0.10,
        "price_range": (40, 700),
        "storage": "Ambient",
        "complexity": "Medium",
        "fragile_probability": 0.12,
    },
    "Household": {
        "weight": 0.10,
        "price_range": (30, 650),
        "storage": "Ambient",
        "complexity": "Medium",
        "fragile_probability": 0.10,
    },
}


def require_files(paths: list[Path]) -> None:
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing prerequisite files:\n"
            + "\n".join(str(path) for path in missing)
            + "\nRun scripts 01-04 first."
        )


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    """Create anonymous SKU master data; names can be replaced later."""

    categories = list(CATEGORY_RULES)
    probabilities = [CATEGORY_RULES[name]["weight"] for name in categories]
    assigned_categories = rng.choice(
        categories,
        size=PRODUCT_COUNT,
        p=probabilities,
    )

    records = []
    for number, category in enumerate(assigned_categories, start=1):
        rule = CATEGORY_RULES[category]
        minimum_price, maximum_price = rule["price_range"]
        price = round(float(rng.uniform(minimum_price, maximum_price)), 2)

        records.append(
            {
                "Product_ID": f"SKU{number:04d}",
                "Category": category,
                "Storage_Type": rule["storage"],
                "Handling_Complexity": rule["complexity"],
                "Fragile_Flag": bool(rng.random() < rule["fragile_probability"]),
                "Similar_Packaging_Flag": bool(rng.random() < 0.12),
                "Unit_Price_INR": price,
                "Popularity_Weight": round(float(rng.lognormal(0, 0.8)), 4),
                "Active_Flag": True,
            }
        )

    products = pd.DataFrame(records)
    products["Popularity_Probability"] = (
        products["Popularity_Weight"] / products["Popularity_Weight"].sum()
    )
    return products


def build_promotions(conditions: pd.DataFrame) -> pd.DataFrame:
    """Create one promotion master row for each promoted store-date."""

    promoted = conditions.loc[conditions["Promotion_Flag"]].copy()
    promoted["Promotion_ID"] = (
        "PROMO_"
        + promoted["Store_ID"]
        + "_"
        + promoted["Date"].dt.strftime("%Y%m%d")
    )
    promoted["Promotion_Type"] = "Store-wide offer"
    promoted["Start_Datetime"] = promoted["Date"]
    promoted["End_Datetime"] = promoted["Date"] + pd.Timedelta(days=1)
    promoted["Demand_Lift_Assumption"] = promoted["Promotion_Factor"] - 1

    return promoted[
        [
            "Promotion_ID",
            "Store_ID",
            "Start_Datetime",
            "End_Datetime",
            "Promotion_Type",
            "Demand_Lift_Assumption",
        ]
    ]


def detail_interval_subset(interval_demand: pd.DataFrame) -> pd.DataFrame:
    """Select the first DETAIL_DAYS without altering interval totals."""

    first_date = interval_demand["Date"].min()
    final_date = first_date + pd.Timedelta(days=DETAIL_DAYS - 1)
    return interval_demand.loc[interval_demand["Date"].between(first_date, final_date)].copy()


def build_orders(
    detail_intervals: pd.DataFrame,
    stores: pd.DataFrame,
    promotions: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate one order per interval order count."""

    store_radius = stores.set_index("Store_ID")["Delivery_Radius_KM"].to_dict()
    promotion_lookup = {
        (row.Store_ID, row.Start_Datetime.date()): row.Promotion_ID
        for row in promotions.itertuples(index=False)
    }

    records = []
    order_number = 1

    for interval in detail_intervals.itertuples(index=False):
        interval_start = pd.Timestamp(
            f"{pd.Timestamp(interval.Date):%Y-%m-%d} {interval.Start_Time}"
        )

        for _ in range(int(interval.Actual_Orders)):
            created_time = interval_start + pd.Timedelta(
                seconds=int(rng.integers(0, 1800))
            )
            radius = store_radius[interval.Store_ID]
            # Triangular distribution favors shorter trips but permits the full radius.
            distance = round(float(rng.triangular(0.2, radius * 0.55, radius)), 2)

            promotion_id = promotion_lookup.get(
                (interval.Store_ID, pd.Timestamp(interval.Date).date())
            )

            records.append(
                {
                    "Order_ID": f"ORD{order_number:09d}",
                    "Store_Date_Interval_ID": interval.Store_Date_Interval_ID,
                    "Store_ID": interval.Store_ID,
                    "Order_Date": pd.Timestamp(interval.Date),
                    "Interval_ID": interval.Interval_ID,
                    "Order_Created_Time": created_time,
                    "Delivery_Distance_KM": distance,
                    "Promotion_ID": promotion_id,
                }
            )
            order_number += 1

    return pd.DataFrame(records)


def generate_unit_count(rng: np.random.Generator) -> int:
    """Generate basket units centered near five and capped at twenty."""

    return int(np.clip(rng.poisson(lam=4) + 1, 1, 20))


def split_units_across_lines(
    total_units: int,
    line_count: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Give every line at least one unit, then distribute the remainder."""

    quantities = np.ones(line_count, dtype=int)
    remaining = total_units - line_count
    if remaining > 0:
        quantities += rng.multinomial(remaining, np.full(line_count, 1 / line_count))
    return quantities


def build_order_items(
    orders: pd.DataFrame,
    products: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate one or more SKU lines per order and calculate order value."""

    product_ids = products["Product_ID"].to_numpy()
    product_probabilities = products["Popularity_Probability"].to_numpy()
    prices = products.set_index("Product_ID")["Unit_Price_INR"].to_dict()

    item_records = []
    order_summaries = []
    item_number = 1

    for order in orders.itertuples(index=False):
        unit_count = generate_unit_count(rng)
        # Multiple quantities of the same SKU mean lines are fewer than units.
        line_count = min(unit_count, max(1, int(round(unit_count * rng.uniform(0.60, 0.90)))))
        selected_products = rng.choice(
            product_ids,
            size=line_count,
            replace=False,
            p=product_probabilities,
        )
        quantities = split_units_across_lines(unit_count, line_count, rng)

        order_value = 0.0
        for product_id, quantity in zip(selected_products, quantities):
            unit_price = float(prices[product_id])
            line_value = round(unit_price * int(quantity), 2)
            order_value += line_value

            item_records.append(
                {
                    "Order_Item_ID": f"ITEM{item_number:010d}",
                    "Order_ID": order.Order_ID,
                    "Product_ID": product_id,
                    "Ordered_Quantity": int(quantity),
                    "Unit_Price_INR": unit_price,
                    "Line_Value_INR": line_value,
                }
            )
            item_number += 1

        order_summaries.append(
            {
                "Order_ID": order.Order_ID,
                "Total_Units": unit_count,
                "Distinct_SKU_Lines": line_count,
                "Order_Value_INR": round(order_value, 2),
            }
        )

    items = pd.DataFrame(item_records)
    summaries = pd.DataFrame(order_summaries)
    enriched_orders = orders.merge(summaries, on="Order_ID", how="left", validate="one_to_one")
    return items, enriched_orders


def check(table: str, test: str, passed: bool, failures: int) -> dict:
    return {
        "Table": table,
        "Validation_Test": test,
        "Passed": bool(passed),
        "Failure_Count": int(failures),
    }


def validate(
    stores: pd.DataFrame,
    detail_intervals: pd.DataFrame,
    products: pd.DataFrame,
    promotions: pd.DataFrame,
    orders: pd.DataFrame,
    items: pd.DataFrame,
) -> pd.DataFrame:
    expected_orders = int(detail_intervals["Actual_Orders"].sum())
    duplicate_orders = orders["Order_ID"].duplicated().sum()
    duplicate_items = items["Order_Item_ID"].duplicated().sum()
    invalid_order_store = (~orders["Store_ID"].isin(stores["Store_ID"])).sum()
    invalid_item_order = (~items["Order_ID"].isin(orders["Order_ID"])).sum()
    invalid_item_product = (~items["Product_ID"].isin(products["Product_ID"])).sum()
    invalid_promotion = (
        orders["Promotion_ID"].notna()
        & ~orders["Promotion_ID"].isin(promotions["Promotion_ID"])
    ).sum()

    generated_by_interval = orders.groupby("Store_Date_Interval_ID").size()
    expected_by_interval = detail_intervals.set_index("Store_Date_Interval_ID")["Actual_Orders"]
    interval_comparison = expected_by_interval.to_frame("Expected").join(
        generated_by_interval.rename("Generated"),
        how="left",
    ).fillna(0)
    interval_mismatches = (interval_comparison["Expected"] != interval_comparison["Generated"]).sum()

    item_units = items.groupby("Order_ID")["Ordered_Quantity"].sum()
    unit_check = orders.set_index("Order_ID")[["Total_Units"]].join(item_units.rename("Item_Units"))
    unit_mismatches = (unit_check["Total_Units"] != unit_check["Item_Units"]).sum()

    orphan_orders_without_items = (~orders["Order_ID"].isin(items["Order_ID"])).sum()
    nonpositive_quantities = (items["Ordered_Quantity"] <= 0).sum()
    distance_out_of_range = orders.merge(
        stores[["Store_ID", "Delivery_Radius_KM"]], on="Store_ID", validate="many_to_one"
    )
    invalid_distance = (
        (distance_out_of_range["Delivery_Distance_KM"] <= 0)
        | (
            distance_out_of_range["Delivery_Distance_KM"]
            > distance_out_of_range["Delivery_Radius_KM"]
        )
    ).sum()

    tests = [
        check("Products", "Product_ID is unique", products["Product_ID"].is_unique, products["Product_ID"].duplicated().sum()),
        check("Promotions", "Promotion_ID is unique", promotions["Promotion_ID"].is_unique, promotions["Promotion_ID"].duplicated().sum()),
        check("Orders", "Order_ID is unique", duplicate_orders == 0, duplicate_orders),
        check("Orders", "Order count reconciles to interval demand", len(orders) == expected_orders, abs(expected_orders - len(orders))),
        check("Orders", "Every interval order count reconciles", interval_mismatches == 0, interval_mismatches),
        check("Orders", "Every Store_ID exists", invalid_order_store == 0, invalid_order_store),
        check("Orders", "Every Promotion_ID exists when populated", invalid_promotion == 0, invalid_promotion),
        check("Orders", "Delivery distance is within store radius", invalid_distance == 0, invalid_distance),
        check("Order Items", "Order_Item_ID is unique", duplicate_items == 0, duplicate_items),
        check("Order Items", "Every Order_ID exists", invalid_item_order == 0, invalid_item_order),
        check("Order Items", "Every Product_ID exists", invalid_item_product == 0, invalid_item_product),
        check("Order Items", "Every order has at least one item", orphan_orders_without_items == 0, orphan_orders_without_items),
        check("Order Items", "Quantities are positive", nonpositive_quantities == 0, nonpositive_quantities),
        check("Order Items", "Item quantities reconcile to order units", unit_mismatches == 0, unit_mismatches),
    ]
    return pd.DataFrame(tests)


def main() -> None:
    require_files([STORES_FILE, INTERVAL_DEMAND_FILE, DAILY_CONDITIONS_FILE])

    stores = pd.read_csv(STORES_FILE)
    interval_demand = pd.read_csv(INTERVAL_DEMAND_FILE, parse_dates=["Date"])
    conditions = pd.read_csv(DAILY_CONDITIONS_FILE, parse_dates=["Date"])
    rng = np.random.default_rng(SEED)

    products = build_products(rng)
    promotions = build_promotions(conditions)
    detail_intervals = detail_interval_subset(interval_demand)
    orders = build_orders(detail_intervals, stores, promotions, rng)
    items, orders = build_order_items(orders, products, rng)
    validation = validate(stores, detail_intervals, products, promotions, orders, items)

    print("\nDETAIL WINDOW")
    print(f"Start: {orders['Order_Date'].min():%Y-%m-%d}")
    print(f"End:   {orders['Order_Date'].max():%Y-%m-%d}")
    print(f"Orders: {len(orders):,}")
    print(f"Order-item rows: {len(items):,}")
    print(f"Average units per order: {orders['Total_Units'].mean():.2f}")
    print(f"Average SKU lines per order: {orders['Distinct_SKU_Lines'].mean():.2f}")
    print(f"Average order value: INR {orders['Order_Value_INR'].mean():,.2f}")

    print("\nORDER SAMPLE")
    print(orders.head(5).to_string(index=False))

    print("\nVALIDATION REPORT")
    for row in validation.itertuples(index=False):
        status = "PASS" if row.Passed else "FAIL"
        print(f"{status} | {row.Table} | {row.Validation_Test} | Failures: {row.Failure_Count}")

    if not validation["Passed"].all():
        raise ValueError("Order/item validation failed. Review the report above.")

    products.to_csv(RAW_DATA_DIR / "products.csv", index=False)
    promotions.to_csv(RAW_DATA_DIR / "promotions.csv", index=False)
    orders.to_csv(RAW_DATA_DIR / "orders.csv", index=False)
    items.to_csv(RAW_DATA_DIR / "order_items.csv", index=False)
    validation.to_csv(
        VALIDATION_DIR / "orders_items_validation.csv",
        index=False,
    )

    print("\nFILES GENERATED")
    print(RAW_DATA_DIR / "products.csv")
    print(RAW_DATA_DIR / "promotions.csv")
    print(RAW_DATA_DIR / "orders.csv")
    print(RAW_DATA_DIR / "order_items.csv")
    print(VALIDATION_DIR / "orders_items_validation.csv")


if __name__ == "__main__":
    main()
