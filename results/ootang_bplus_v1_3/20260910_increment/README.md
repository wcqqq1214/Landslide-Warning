# Ootang B+ v1.3 increment-objective comparison

Run and numerical checks complete. Adding the fixed 30-day increment term did not establish stable four-point improvement: 1/8 station-window cases meets all four displacement criteria. User acceptance remains pending.

The training-selected four-point forecast RMSE changes from 58.0122 to 68.8448 mm for 432 fitting days and from 22.2448 to 56.4485 mm for 612 fitting days. C0 retains B in both folds; C1 selects B then A. No final chain meets the prescribed first-order tolerance. For 612 days the old C0 B also has a better recalculated joint training objective than the best fresh C1 result.

[The execution plan](plan_before_execution.md), `config.json` and `source_snapshot/` identify the fixed comparison. Only the first 792 dates are parsed; each 432/612-day fit is followed by 180 days of conditional prediction with observed rainfall and reservoir level. No neural network or later-label scoring was added. A/B denote physical parameter starts, not neural seeds.

`summary.json`, `selected_summary.csv`, `comparison.csv` and `acceptance.csv` provide the primary and same-start paired outcomes. `objective_components.csv` permits common-objective recomputation across methods. Raw per-chain records retain all sixteen stages, call counts and stop reasons. Twelve NPZ files retain predictions, physical states and substep audits. `tests.log`, `delivery_verification.json` and `interpretation_audit.json` record verification and claim boundaries. `artifact_manifest.json` indexes all other files by size and SHA-256.
