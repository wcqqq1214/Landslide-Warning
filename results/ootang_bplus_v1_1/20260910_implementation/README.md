# Ootang B+ v1.1 run: 20260910_implementation

Implementation and the fixed experiment budget are complete. Numerical checks passed.
Both routes selected e_mu=0 and e_sigma=0. Neither improves the frozen B+ mean;
M1 and M2 each satisfy 0/4 pointwise mean-improvement goals. Retain M0.
M2 wins the candidate tie only by parameter count. User/advisor acceptance is separate.

See the [Chinese implementation and results report](../../../docs/ootang_bplus_probabilistic_implementation.v1_1.md).

- `selection.json`: complete development checkpoint scores and ranking, locked before final evaluation.
- `predictions.csv`, `metrics.csv`, `acceptance.json`: forecasts, independent scores, and pointwise checks.
- `calibration_A.json`, `calibration_B.json`, `prefix_calibrated.json`: the fixed prefix-only calibration.
- `reference_provenance.json`, `physics_package_*.json`: source identity and frozen physical packages.
- `validation_*.json`, `gate_*.json`: reproduction, full-history gradients and branchwise mechanics checks.
- `development/`, `final/`: all prescribed checkpoints, per-seed training logs and selected predictions.
- `source_snapshot/`: exact scientific execution source/configuration before the export-only repair.
- `export_revision.json`: later plotting/failure-reporting repair; forecast/metric CSV hashes unchanged.
- `figures/`: four station plots. Means overlap because both selected mean epochs are zero.
- `delivery_status.json`, `artifact_manifest.json`: completion/evidence boundaries and artifact hashes.

The final period is a previously exposed historical backtest, not a new blind test.
Uncertainty is marginal. Final scale heads remain at their constant initialization;
the 90% interval substantially undercovers MJ3 despite average coverage near nominal.
