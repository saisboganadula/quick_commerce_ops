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
# Quick Commerce Operations — Simulated Operational Lifecycle

This repository provides a reproducible simulation and analytics pipeline that models core quick-commerce operations: store fulfilment (pickers), last-mile delivery (riders), order-level events, quality issues, and an interval-level analytical fact. Use this repo to build dashboards, explore operational trade-offs, and test rostering or fleet-sizing policies.

**Quick start (one-minute orientation)**
- **Run order:** execute scripts `01` → `09` in `scripts/` (see `How to run` below).
- **Primary artifact:** `data/processed/interval_operations_analysis.csv` — a canonical fact table at the store × date × half-hour interval grain.
- **Onboarding:** open `notebooks/quick_exploration.ipynb` and `data/sample/interval_operations_sample.csv` for a fast, dependency-light tour.

**What this repo contains (short)**
- **Simulation scripts:** `scripts/01_generate_master_data.py` … `scripts/09_build_interval_operations_analysis.py`
- **Raw outputs:** `data/raw/` — event-level CSVs used to build the fact
- **Processed fact:** `data/processed/interval_operations_analysis.csv`
- **Sample for onboarding:** `data/sample/interval_operations_sample.csv` and `data/sample/README.md`
- **Notebook:** `notebooks/quick_exploration.ipynb` — example analyses and charts

**Learning path (how we’ll teach this project)**
Follow this order when reviewing the project; each step builds on the previous and we'll walk through formulas, joins, and the Solver walkthrough when you are ready:

1. **Business objective:** what SLA and service trade-offs we model
2. **Master data:** stores, time intervals, and store attributes
3. **Daily demand:** how daily orders are generated and drivers
4. **Interval allocation:** splitting daily demand into 48 half-hour buckets
5. **Workforce model:** shifts, attendance, pickers, and riders
6. **Fulfilment & last-mile:** pick sequencing and rider assignment
7. **Quality & root causes:** synthetic item-level quality failures
8. **Build fact:** `interval_operations_analysis.csv` — joins and keys
9. **Dashboards & charts:** heatmaps, utilization, and cost analysis
10. **Rostering & Solver:** how shift matrices and Solver reduce excess capacity
11. **Validation & sensitivity:** tests, parameter sweeps, and scenario analysis
12. **Operational playbook:** recommended actions and how to apply them

---

**Primary files to inspect (fast links)**
- `scripts/01_generate_master_data.py` … `scripts/09_build_interval_operations_analysis.py` — pipeline scripts
- `data/processed/interval_operations_analysis.csv` — canonical fact
- `data/sample/interval_operations_sample.csv` — representative sample for onboarding
- `notebooks/quick_exploration.ipynb` — exploration notebook

**How to run (developer quickstart)**
1. Create and activate a venv and install dependencies (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # or pip install pandas numpy matplotlib
```

2. Run scripts in order from the project root:

```bash
python3 scripts/01_generate_master_data.py
python3 scripts/02_generate_daily_demand.py
...
python3 scripts/09_build_interval_operations_analysis.py
```

---

**Canonical fact: structure & key fields**
The canonical fact `interval_operations_analysis.csv` (produced by `scripts/09_build_interval_operations_analysis.py`) is at the store × date × half-hour interval grain and contains fields such as:

- **keys:** `Store_Date_Interval_ID`, `Store_ID`, `Date`, `Interval_ID`, `Daypart`
- **picker capacity:** `Active_Pickers`, `Picker_Utilization`, `Required_Pickers_At_Target`, `Picker_Supply_Gap`
- **rider supply:** `Active_Riders`, `Rider_Utilization`, `Required_Riders_At_Target`, `Rider_Supply_Gap`, `Rider_Cost_INR`
- **service KPIs:** `Orders`, `Units`, `Revenue_INR`, `SLA_Breaches`, `SLA_Breach_Rate`, `Average_Total_Delivery_Min`
- **quality signals:** `Quality_Issue_Count`, `Missing_Item_Count`, `Wrong_Item_Count`, `Quality_Financial_Impact_INR`
- **diagnostics:** `Dominant_Root_Cause`, `Recommended_Action`

Keep `Store_Date_Interval_ID` as the primary key for joins and aggregations.

---

**Recommended charts and interpretation (prioritized)**
- **SLA Breach Heatmap (Top):** pivot `SLA_Breach_Rate` by `Store_ID` × `Interval_ID` to spot hotspots.
- **Required vs Active Riders/Pickers:** line charts compare `Required_*` vs `Active_*` to reveal supply gaps.
- **Utilization vs Cost:** scatter plots of `Rider_Utilization` vs `Rider_Cost_Per_Order_INR` to spot inefficiencies.
- **Picker queue & picking time:** time-series of `Average_Pick_Queue_Min` and `Average_Picking_Min` to find internal bottlenecks.
- **Root-cause distribution:** stacked bars/treemap of `Dominant_Root_Cause` to prioritize interventions.

Practical tips: prefer 3–7 day rolling averages for KPIs and flag intervals with `SLA_Breach_Rate > 0.15` plus a negative supply gap.

---

**Images and figures**
The repo contains a set of sample charts used for onboarding (in `docs/images/`). These are embedded below and are intended as examples; replace them with exported charts from your workbook if needed.

- Figure: SLA breach heatmap — `docs/images/fig-sla-heatmap.png`
- Figure: Required vs Active Riders — `docs/images/fig-required-vs-active-riders.png`
- Figure: Picker Utilization by interval — `docs/images/fig-picker-utilization.png`
- Figure: Top stores by SLA — `docs/images/fig-top-stores-sla.png`
- Figure: Daypart SLA — `docs/images/fig-daypart-sla.png`
- Figure: Daypart riders — `docs/images/fig-daypart-required-vs-active.png`

(These images are generated by `scripts/generate_sample_charts.py`.)

---

**Simulation methodology (brief)**
- **Demand:** baseline orders multiplied by explicit factors (day-of-week, weather, promotions, salary-week) and sampled via Poisson draws.
- **Interval split:** store-specific multinomial allocation to 48 half-hour buckets so interval totals reconcile to daily totals.
- **Workforce:** scheduled shifts, attendance probabilities, and short OD shifts for surge.
- **Fulfilment:** item-level pick-time model adjusted for product complexity and congestion; deterministic assignment simulates queues.
- **Last-mile:** rider assignment with distance/speed estimates and weather adjustments; SLA combines distance, load and congestion.
- **Quality:** item-level risk model produces missing/wrong/damaged counts and classifies dominant root causes.

---

**Validation & reproducibility**
- Every script includes `validate_*` checks (structural, volumetric, behavioral). Failing validations raise exceptions to avoid contaminating downstream data.
- All scripts use a documented random `SEED` for determinism; outputs are CSVs in `data/` for auditability.

---

**Operational findings (quick summary)**
- **Key achievement so far:** the roster redesign reduced scheduled rider-hours from **4,090** to **3,978** while preserving coverage — a 112 rider-hour reduction (~2.7%).
- **Management lesson:** headcount optimization without aligning shift boundaries yields excess capacity; shift redesign can cut excess without losing coverage.

---

**Files & next steps**
- `scripts/generate_sample_charts.py` — regenerates onboarding PNGs in `docs/images/` from the sample CSV.
- `data/sample/README.md` — explains how the sample was created (reservoir sampling) and its provenance.

If you'd like, I will:
- regenerate alternate aggregates (rolling-window heatmaps, store-level trends), or
- produce a polished PDF presenation of the findings for reviewers.

---

**Contact & citation**
If you publish analyses based on this simulation, please cite the repository and include parameter choices (seed, modified assumptions) so others can reproduce your work.

