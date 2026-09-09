# Ootang B+ v1.2 prefix diagnostics

Run complete; all numerical audits passed; stable four-point predictive improvement was not established. User acceptance remains pending.

See [Chinese results report](../../../docs/ootang_bplus_diagnostics_results.v1.2.md) and [pre-execution plan](plan_before_execution.md).

The two new rolling fits use only the first 432/612 days, each followed by 180 days within the original first 792 days. The 792-day scope only continues existing prefix fits. No neural network training or later-label scoring occurred. Each baseline/continued A/B candidate is preserved.

`manifest.json` and `source_snapshot/` identify the scientific implementation; `preservation.json` verifies unchanged v1.1 artifacts. `summary.json`, `convergence_summary.csv`, and `selected_summary.csv` report outcomes. Raw per-scope files provide optimization, prediction, state, constraint and failure evidence. `tests.log` records independent checks.
