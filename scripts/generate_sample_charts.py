#!/usr/bin/env python3
"""
Generate three charts from data/sample/interval_operations_sample.csv:
- SLA breach rate heatmap (store × interval)
- Required vs Active Riders by interval
- Picker utilization by interval

Saves PNGs to docs/images/
"""
import csv
import math
import os
from collections import defaultdict

SAMPLE_CSV = os.path.join('data', 'sample', 'interval_operations_sample.csv')
OUT_DIR = os.path.join('docs', 'images')

os.makedirs(OUT_DIR, exist_ok=True)

# attempt import matplotlib
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception as e:
    print('matplotlib is required. Install with: pip install matplotlib')
    raise

# helper to parse float safely
def to_float(x):
    try:
        if x is None or x == '':
            return None
        return float(x)
    except Exception:
        return None

rows = []
with open(SAMPLE_CSV, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        # normalize keys: strip whitespace
        r2 = {k.strip(): v for k, v in r.items()}
        rows.append(r2)

if not rows:
    print('No rows found in', SAMPLE_CSV)
    raise SystemExit(1)

# find candidate column names
def find_col(candidates):
    for c in candidates:
        if c in rows[0]:
            return c
    return None

col_store = find_col(['Store_ID','StoreID','Store Id','Store'])
col_interval = find_col(['Interval_ID','IntervalID','Interval Id','Interval'])
col_sla = find_col(['SLA_Breach_Rate','SLA_BreachRate','SLA_Breach_Rate'])
col_required_riders = find_col(['Required_Riders_At_Target','Required_Riders','Required_Riders_At_Target'])
col_active_riders = find_col(['Active_Riders','ActiveRiders','Active_Riders'])
col_picker_util = find_col(['Picker_Utilization','Picker Utilization','Picker_Util'])

if not (col_store and col_interval):
    print('Missing required grouping columns. Found:', col_store, col_interval)
    raise SystemExit(1)

# Build structures
stores = sorted({r[col_store] for r in rows})
intervals = sorted({r[col_interval] for r in rows}, key=lambda x: int(x) if x and x.isdigit() else x)

# heatmap matrix: store x interval -> avg sla
sla_acc = defaultdict(lambda: [0.0, 0])
for r in rows:
    key = (r[col_store], r[col_interval])
    val = to_float(r.get(col_sla)) if col_sla else None
    if val is not None:
        sla_acc[key][0] += val
        sla_acc[key][1] += 1

import numpy as np
matrix = np.full((len(stores), len(intervals)), np.nan)
for i, s in enumerate(stores):
    for j, it in enumerate(intervals):
        total, cnt = sla_acc[(s, it)]
        matrix[i, j] = (total / cnt) if cnt > 0 else float('nan')

# Plot heatmap
plt.figure(figsize=(12, max(4, len(stores)*0.25)))
plt.imshow(np.nan_to_num(matrix, nan=0.0), aspect='auto', cmap='Reds')
plt.colorbar(label='SLA breach rate')
plt.yticks(range(len(stores)), stores)
# reduce x ticks if many intervals
step = max(1, len(intervals)//12)
xticks = list(range(0, len(intervals), step))
plt.xticks(xticks, [intervals[x] for x in xticks], rotation=45)
plt.xlabel('Interval_ID')
plt.ylabel('Store_ID')
plt.title('SLA Breach Rate Heatmap (sample)')
heatmap_out = os.path.join(OUT_DIR, 'fig-sla-heatmap.png')
plt.tight_layout()
plt.savefig(heatmap_out, dpi=150)
plt.close()
print('Wrote', heatmap_out)

# Required vs Active riders by interval (aggregate mean across stores)
req_acc = defaultdict(lambda: [0.0, 0])
act_acc = defaultdict(lambda: [0.0, 0])
for r in rows:
    it = r[col_interval]
    req = to_float(r.get(col_required_riders)) if col_required_riders else None
    act = to_float(r.get(col_active_riders)) if col_active_riders else None
    if req is not None:
        req_acc[it][0] += req; req_acc[it][1] += 1
    if act is not None:
        act_acc[it][0] += act; act_acc[it][1] += 1

ints_sorted = intervals
req_series = [ (req_acc[it][0]/req_acc[it][1]) if req_acc[it][1]>0 else 0.0 for it in ints_sorted ]
act_series = [ (act_acc[it][0]/act_acc[it][1]) if act_acc[it][1]>0 else 0.0 for it in ints_sorted ]

plt.figure(figsize=(12,4))
plt.plot(ints_sorted, req_series, label='Required Riders (mean)')
plt.plot(ints_sorted, act_series, label='Active Riders (mean)')
plt.xticks(xticks, [intervals[x] for x in xticks], rotation=45)
plt.xlabel('Interval_ID')
plt.ylabel('Riders')
plt.title('Required vs Active Riders (sample, mean across stores)')
plt.legend()
plt.tight_layout()
riders_out = os.path.join(OUT_DIR, 'fig-required-vs-active-riders.png')
plt.savefig(riders_out, dpi=150)
plt.close()
print('Wrote', riders_out)

# Picker utilization by interval
if col_picker_util:
    util_acc = defaultdict(lambda: [0.0, 0])
    for r in rows:
        it = r[col_interval]
        u = to_float(r.get(col_picker_util))
        if u is not None:
            util_acc[it][0] += u; util_acc[it][1] += 1
    util_series = [ (util_acc[it][0]/util_acc[it][1]) if util_acc[it][1]>0 else 0.0 for it in ints_sorted ]
    plt.figure(figsize=(12,4))
    plt.plot(ints_sorted, util_series, label='Picker Utilization (mean)')
    plt.xticks(xticks, [intervals[x] for x in xticks], rotation=45)
    plt.xlabel('Interval_ID')
    plt.ylabel('Utilization')
    plt.title('Picker Utilization by Interval (sample)')
    plt.legend()
    plt.tight_layout()
    util_out = os.path.join(OUT_DIR, 'fig-picker-utilization.png')
    plt.savefig(util_out, dpi=150)
    plt.close()
    print('Wrote', util_out)
else:
    print('Picker utilization column not found; skipped picker utilization plot')

print('Done')
