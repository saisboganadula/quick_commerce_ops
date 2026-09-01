#!/usr/bin/env python3
"""
Draw a simple pipeline diagram as PNG for README (fallback for Mermaid).
"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT_PNG = os.path.join('docs', 'images', 'fig-pipeline.png')
OUT_SVG = os.path.join('docs', 'images', 'fig-pipeline.svg')

fig, ax = plt.subplots(figsize=(10,6))
ax.axis('off')

boxes = [
    (0.05, 0.8, 'Master data\nstores items\ntime_intervals'),
    (0.25, 0.8, 'Daily demand\ngenerator'),
    (0.45, 0.8, 'Interval allocation\n48 half-hour buckets'),
    (0.65, 0.8, 'Orders and\norder items'),
    (0.05, 0.6, 'Workers and\nshifts'),
     (0.48, 0.6, 'Picker\nfulfilment'),
    (0.65, 0.6, 'Rider assignment\nand delivery'),
     (0.68, 0.4, 'Quality issues\nand SLA root causes'),
    (0.65, 0.35, 'Quality events\nroot cause classification'),
     (0.48, 0.35, 'Interval analytical\nfact table'),
     (0.75, 0.25, 'Power Query\nPivotTables & charts'),
     (0.75, 0.05, 'Hourly rider\nrequirements\nShift matrix\nSolver')
]

for x,y,label in boxes:
    ax.add_patch(plt.Rectangle((x-0.08, y-0.04), 0.16, 0.08, fill=True, color='#f0f0ff', ec='k'))
    ax.text(x, y, label, ha='center', va='center', fontsize=9)

# arrows
def arrow(a, b):
    ax.annotate('', xy=b, xytext=a, arrowprops=dict(arrowstyle='->', lw=1.5))

arrow((0.13, 0.8), (0.22, 0.8))
arrow((0.33, 0.8), (0.42, 0.8))
arrow((0.53, 0.8), (0.62, 0.8))
arrow((0.15, 0.78), (0.15, 0.64))
arrow((0.45, 0.78), (0.45, 0.68))
arrow((0.55, 0.64), (0.62, 0.64))
arrow((0.62, 0.6), (0.62, 0.44))
arrow((0.62, 0.44), (0.45, 0.42))
arrow((0.45, 0.32), (0.72, 0.28))
arrow((0.72, 0.22), (0.72, 0.08))

plt.tight_layout()
plt.savefig(OUT_PNG, dpi=200)
plt.savefig(OUT_SVG, dpi=200, format='svg')
print('Wrote', OUT_PNG)
print('Wrote', OUT_SVG)
