# Quick Commerce Operations — Simulated Operational Lifecycle

This repository contains a reproducible simulation and analytics pipeline that models core quick-commerce operations: in-store fulfilment (pickers), last-mile delivery (riders), order-level detail, quality issues, and interval-level operational analysis. The artifacts produced are intended to support dashboards, research, algorithm development (fleet sizing, rostering, route planning), and teaching about operational trade-offs in on-demand retail.

## Index

- [Run order (high level)](#run-order-high-level)
- [Purpose and audience](#purpose-and-audience)
- [Why this dataset is representative](#why-this-dataset-is-representative)
- [Reproducibility and determinism](#reproducibility-and-determinism)
- [Key generated artifacts (selected)](#key-generated-artifacts-selected)
- [Simulation methodology (high level)](#simulation-methodology-high-level)
- [Validation and data quality](#validation-and-data-quality)
- [Canonical fact: interval_operations_analysis.csv (fact_interval_operations)](#fact-interval-operations)
- [How the fact maps to dashboards (chart-by-chart guidance and ranking)](#how-the-fact-maps-to-dashboards)
- [Technical notes for building charts](#technical-notes-for-building-charts)
- [Dashboard layout recommendation](#dashboard-layout-recommendation)
- [Operational recommendations and how to use this dataset](#operational-recommendations)
- [Limitations and ethical considerations](#limitations-and-ethical-considerations)
- [How to run the pipeline](#how-to-run-the-pipeline)
- [Files to inspect for building dashboards](#files-to-inspect-for-building-dashboards)
- [Contributing and extension ideas](#contributing-and-extension-ideas)
- [Contact and citation](#contact-and-citation)
- [Files referenced in this README](#files-referenced-in-this-readme)

<a name="run-order-high-level"></a>
Run order (high level)
- **01_generate_master_data.py** — store master, time intervals, and basic validation.
- **02_generate_daily_demand.py** — calendar, weather, promotions, expected and realized daily orders.
- **03_generate_interval_demand.py** — split each store-day across 48 half-hour intervals.
- **04_generate_workers_and_shifts.py** — simulate pickers and riders, scheduled and actual attendance.
- **05_generate_orders_and_items.py** — generate skus, promotions, orders and order items (detail window for first 14 days).
- **06_generate_store_fulfilment.py** — assign pickers, produce pick timings and fulfilment events.
- **07_generate_last_mile_delivery.py** — assign riders, compute deliveries, SLA, cost, and rider performance.
- **08_generate_quality_and_root_causes.py** — generate item quality issues and classify SLA-breach root causes.
- **09_build_interval_operations_analysis.py** — combine everything into the canonical analytical fact table `interval_operations_analysis.csv` and produce recommended operational actions.

<a name="purpose-and-audience"></a>
Purpose and audience
- **Audience:** novice analysts, data scientists, operations researchers, and domain experts.
- **Purpose:** provide a complete, explainable, and auditable simulation of the operational lifecycle for quick commerce so researchers and practitioners can test policies (rostering, surge staffing, incentives), validate algorithms, or build dashboards that reflect realistic operational signals.

<a name="why-this-dataset-is-representative"></a>
Why this dataset is representative
- Layered construction: the simulation builds from master data (stores + time grid) through demand, interval allocation, workforce attendance, pick/pack events, and last-mile delivery. This mirrors the real operational flow from order creation to successful delivery.
- Explainability: each multiplier (day-of-week, weather, promotions, salary-week, noise) is explicit and parameterized for reproducibility and sensitivity analysis.
- Heterogeneity: store profiles, product categories, worker experience, and rider types (regular vs gig) produce variation representative of real operations.
- Validation built-in: each script runs a set of deterministic validation checks and fails early if assumptions are violated — this keeps the generated data trustworthy.

<a name="reproducibility-and-determinism"></a>
Reproducibility and determinism
- Every script uses a fixed random `SEED` and documents it at the top. Re-running scripts in sequence with the same seed reproduces identical outputs.
- All outputs are CSV files in `data/raw`, `data/processed`, and `data/validation` so downstream users can inspect and use them without re-running the full pipeline.

<a name="key-generated-artifacts-selected"></a>
Key generated artifacts (selected)
- `data/raw/stores.csv` — store list and baseline attributes.
- `data/raw/time_intervals.csv` — 48 half-hour intervals per day with `Interval_ID`, `Start_Time`, `Daypart`, `Default_Peak_Flag`.
- `data/raw/daily_store_demand.csv` — per store-date expected and actual daily orders with drivers (weather, promotions, salary week).
- `data/raw/interval_demand.csv` — 48 interval rows per store-date reconciling exactly to daily totals (multinomial draw).
- `data/raw/worker_shifts.csv` and `data/raw/workers.csv` — scheduled and attended workforce details (pickers, riders, gig vs regular).
- `data/raw/orders.csv`, `data/raw/order_items.csv` — order-level detail (detail window) and item lines used for fulfilment simulation.
- `data/raw/picker_interval_operations.csv` — interval-level picker allocations and capacity measurements.
- `data/raw/fulfilment_events.csv`, `data/raw/order_picker_assignments.csv` — pick timing and assignment events.
- `data/raw/delivery_events.csv`, `data/raw/order_rider_assignments.csv`, `data/raw/rider_shift_performance.csv` — last-mile events, assignment, and per-shift rider performance.
- `data/raw/quality_issues.csv`, `data/raw/sla_root_cause_analysis.csv` — synthetic quality failures and classified root causes for SLA breaches.
- `data/processed/interval_operations_analysis.csv` — the primary fact table (one row per store-date-interval) used to build dashboards and charts.

<a name="simulation-methodology-high-level"></a>
Simulation methodology (high level)
- Demand: daily expected orders are generated from a baseline `Base_Daily_Orders` multiplied by explicit factors: `Day_Of_Week_Factor`, `Salary_Week_Factor`, `Weather_Factor`, `Promotion_Factor`, and bounded random noise. Actual daily counts are sampled using a Poisson draw.
- Interval splitting: daily order totals are allocated across 48 half-hour buckets with a store-specific, weekend-aware demand curve. A multinomial draw assigns individual orders to intervals so interval-level totals exactly reconcile to daily totals.
- Workforce: headcounts are specified per store. Workers are assigned home shifts and scheduled; attendance (late, early, absent) is simulated with probabilities. Over-time short-OD shifts are added on surge days.
- Fulfilment: pick times are calculated per item using base seconds per unit adjusted by product complexity, picker experience, and congestion. A deterministic heap-based assignment simulates queueing and per-order pick sequencing.
- Last mile: riders are selected from attended rider shifts, travel times are estimated from distances and weather-modified speeds, and return availability is updated to model capacity. SLA estimates combine distance, load, peak, and weather.
- Quality and root causes: item-level probabilities for missing/wrong/damaged are computed from observable risk drivers (audit backlog, putaway backlog, fragile flag, congestion). SLA-breach root causes are ranked by which stage shows the largest excess delay.

<a name="validation-and-data-quality"></a>
Validation and data quality
- Each script includes a `validate_*` function which checks structural (uniqueness, foreign keys), volumetric (row counts), and behavioral (weekend > weekday demand; evening > night) expectations.
- If validations fail, scripts raise an exception and stop to prevent downstream contamination.

<a name="fact-interval-operations"></a>
Canonical fact: `interval_operations_analysis.csv` (fact_interval_operations)
This is the essential analytical table used by dashboards and analysis. It is built in `scripts/09_build_interval_operations_analysis.py` and contains (non-exhaustive) the following fields:
- `Store_Date_Interval_ID`, `Store_ID`, `Date`, `Interval_ID`, `Daypart`
- Operational capacity and allocations: `Active_Pickers`, `Picking_Pickers`, `Putaway_Pickers`, `Audit_Pickers`, `Putaway_Units_Capacity`, `Picker_Active_Minutes`, `Picker_Utilization`, `Required_Pickers_At_Target`, `Picker_Supply_Gap`
- Rider supply and usage: `Active_Riders`, `Busy_Riders`, `Rider_Busy_Minutes`, `Rider_Utilization`, `Required_Riders_At_Target`, `Rider_Supply_Gap`, `Rider_Cost_INR`, `Rider_Cost_Per_Order_INR`
- Order and service KPIs: `Orders`, `Units`, `Revenue_INR`, `SLA_Breaches`, `SLA_Breach_Rate`, `Average_Total_Delivery_Min`, `Average_Pick_Queue_Min`, `Average_Picking_Min`, `Average_Last_Mile_Min`
- Quality signals: `Quality_Issue_Count`, `Orders_With_Quality_Issue`, `Missing_Item_Count`, `Wrong_Item_Count`, `Damaged_Item_Count`, `Quality_Issue_Rate`, `Quality_Financial_Impact_INR`
- Diagnostics and decisions: `Dominant_Root_Cause`, `Dominant_Root_Cause_Count`, `Recommended_Action`

<a name="how-the-fact-maps-to-dashboards"></a>
How the fact maps to dashboards (chart-by-chart guidance and ranking)
I recommend the following prioritized charts for a production-ready operational dashboard. Each entry includes why it matters and how to interpret it. The ranking is by operational impact: how directly the visualization leads to actionable decisions.

1) **SLA Breach Rate Heatmap (Top priority)**
- Data: pivot `SLA_Breach_Rate` by `Store_ID` (rows) and `Interval_ID` (columns) (or Daypart aggregation).
- Why: quickly surfaces persistent intervals and stores with high breach frequency (capacity gaps, route issues).
- Interpretation: hotspots during evening/lunch indicate pick or rider shortages; consistent overnight hotspots may indicate long tail issues or routing.
- Action: use `Rider_Supply_Gap` and `Picker_Supply_Gap` to decide whether rider hiring, rebalancing, or picker protection is required.

2) **Required vs Available Riders / Picker Supply Gap (High)**
- Data: line or bar chart comparing `Required_Riders_At_Target` vs `Active_Riders` (and same for pickers) aggregated by store or network.
- Why: exposes systemic under/over-supply and times when utilization is likely suboptimal.
- Interpretation: positive gap → surplus; negative gap → shortage. Follow with utilization charts to confirm.
- Action: adjust rosters, create targeted incentives, or reassign riders across stores.

3) **Rider Utilization & Cost per Order (High)**
- Data: scatter of `Rider_Utilization` vs `Rider_Cost_Per_Order_INR`, sized by `Orders` or `Units` per interval.
- Why: shows efficiency—high utilization + low cost is desirable; high cost with low utilization indicates inefficiency.
- Interpretation: intervals with low utilization but high cost may be caused by long distances or inefficient routing; high utilization and high cost can indicate necessary incentives.
- Action: modify routing, incentive plan, or shift allocation.

4) **Picker Utilization & Pick Queue / Picking Times (High)**
- Data: time-series or heatmap of `Picker_Utilization`, `Average_Pick_Queue_Min`, `Average_Picking_Min` by interval.
- Why: reveals internal bottlenecks that directly cause SLA breaches and quality issues.
- Interpretation: rising pick queue correlates with increased SLA breaches and quality issues; congestion correlates with picking time increases.
- Action: increase picker protection during peaks, add OD pickers, or shift put-away schedules.

5) **Root Cause Distribution (Medium-High)**
- Data: stacked bar or treemap of `Dominant_Root_Cause` counts by store or interval.
- Why: prioritizes interventions by dominant cause (e.g., Rider Supply vs Picker Queue vs Drop-Zone Handoff).
- Interpretation: if `Rider Supply / Availability` dominates, focus on last-mile; if `Picker Capacity / Queue` dominates, focus on store fulfilment.
- Action: targeted operational playbooks per dominant cause.

6) **Quality Issue Dashboard (Medium)**
- Data: rates and financial impact: `Quality_Issue_Rate`, `Quality_Financial_Impact_INR` by store/interval, plus item-level drills.
- Why: ties quality to operational metrics (congestion, audit backlog) and quantifies cost.
- Interpretation: high quality issue rate during high congestion or high putaway backlog suggests root cause is internal process.
- Action: increase audits, retrain pickers, or adjust inbound putaway schedules.

7) **Order Volume & Revenue Heatmap / Trends (Medium)**
- Data: `Orders`, `Units`, `Revenue_INR` over time and by interval.
- Why: baseline demand signals for capacity planning.
- Interpretation: use with utilization and cost charts to balance service vs cost.

8) **Top-10 Stores by SLA Breaches / Cost (Medium)**
- Data: ranked table with `SLA_Breach_Rate`, `Rider_Cost_Per_Order_INR`, `Quality_Issue_Rate`.
- Why: executive summary and prioritization for station-level interventions.

9) **Recommended Actions Summary (Operational playbook)**
- Data: count of `Recommended_Action` by store/interval.
- Why: converts analytics into a simple to-follow operational to-do list for store managers and the operations center.

<a name="technical-notes-for-building-charts"></a>
Technical notes for building charts
- Grain: keep `Store_Date_Interval_ID` as the primary key. Aggregate thoughtfully (by store, daypart, or hour) to reduce noise.
- Rolling windows: use 3-day or 7-day rolling averages for network KPIs to smooth spiky intervals and identify persistent problems.
- Alerts: automatically flag intervals where `SLA_Breach_Rate > 0.15` and either `Rider_Supply_Gap < 0` or `Picker_Supply_Gap < 0`.

<a name="dashboard-layout-recommendation"></a>
Dashboard layout recommendation
- **Overview**: KPI cards — Average SLA breach rate, Average rider utilization, Average picker utilization, Network orders/day, Quality issue rate.
- **Capacity**: Rider/Picker supply vs required charts, utilization distribution, cost per order.
- **Service**: SLA Breach heatmap, SLA distribution, SLA breach root causes.
- **Fulfilment**: Pick queue times, picking minutes, putaway backlog, picker intervals with high `Orders_Per_Picking_Picker`.
- **Quality**: Quality issue heatmap, financial impact, issue types, sample order drilldown.
- **Actions**: Top recommended actions and a time-window to apply them, with a basic before/after comparison panel.

<a name="operational-recommendations"></a>
Operational recommendations and how to use this dataset
- Use the simulation to test roster changes: increase/decrease riders or putaway capacity and observe the downstream SLA and cost impact.
- Evaluate incentive programs by changing `PEAK_INCENTIVE_PER_ORDER` and `PROMOTION_PROBABILITY`.
- Test routing and speed assumptions by adjusting `WEATHER_SPEED_KMH` and check sensitivity.
- Use the root-cause table to build a prioritized remediation plan and measure impact in the simulation.

<a name="limitations-and-ethical-considerations"></a>
Limitations and ethical considerations
- Synthetic: even though the dataset is realistic and internally consistent, it is synthetic and should not be used to make irreversible operational decisions without validation on real data.
- Local assumptions: many parameters (Hyderabad-specific weather probabilities, salary-week logic) are chosen as realistic examples; adapt to your target geography before operationalizing.
- Privacy: no personal or PII is generated for riders, pickers, or customers.

<a name="how-to-run-the-pipeline"></a>
How to run the pipeline
1. Ensure Python 3 and `pandas`, `numpy` are installed (recommended: create a venv).
2. From the project root run scripts in order 01 → 09. Example:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # if available, otherwise pip install pandas numpy
python3 scripts/01_generate_master_data.py
python3 scripts/02_generate_daily_demand.py
... (run through) ...
python3 scripts/09_build_interval_operations_analysis.py
```

<a name="files-to-inspect-for-building-dashboards"></a>
Files to inspect for building dashboards
- `data/processed/interval_operations_analysis.csv` — primary fact used for dashboards.
- `data/raw/*` — supporting raw events for line-level investigation.
- `data/validation/*` — validation reports from each step, useful for QA and trust.

<a name="contributing-and-extension-ideas"></a>
Contributing and extension ideas
- Add more realistic routing or map-based distance estimation.
- Replace synthetic weather with a time-series derived from real historical weather.
- Add multi-zone rider repositioning logic or a route optimizer to study routing trade-offs.
- Add customer cancellation behavior and its effect on rider utilization.

<a name="contact-and-citation"></a>
Contact and citation
- If you publish analyses based on this simulation, cite the repository and include parameter choices (seed values and any changed assumptions) so readers can reproduce your work.

<a name="files-referenced-in-this-readme"></a>
Files referenced in this README
- Scripts: [scripts/01_generate_master_data.py](scripts/01_generate_master_data.py) — [scripts/09_build_interval_operations_analysis.py](scripts/09_build_interval_operations_analysis.py)
- Primary fact: [data/processed/interval_operations_analysis.csv](data/processed/interval_operations_analysis.csv)

---
This README was generated from a code inspection of the simulation pipeline in this repository and is intended as a publication-quality companion document explaining the mechanics, intent, and recommended analysis for the simulated quick-commerce operations dataset.
