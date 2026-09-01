Representative sample: interval_operations_sample.csv

This folder contains a small, representative sample of the canonical fact `data/processed/interval_operations_analysis.csv` intended for onboarding and quick exploration without downloading the full dataset.

Sampling method:
- Reservoir sampling from the processed fact to produce a 200-row representative file.
- The sample preserves distribution across `Daypart` and `Store_ID` where possible and includes an additional column `Sample_Notes` for human-readable annotations.

Usage:
- Use `notebooks/quick_exploration.ipynb` to load and validate the sample.
- If you need more rows, regenerate using the reservoir-sampling script or run the pipeline and create your own slice from `data/processed/interval_operations_analysis.csv`.

Provenance:
- Generated from `data/processed/interval_operations_analysis.csv` using a pure-Python reservoir sampling script to avoid heavy package dependencies in constrained environments.
