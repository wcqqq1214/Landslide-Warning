# v1.4 task B: internal temporal recipe selection

Run: 20260911_selection. Plan `2a53df6`, implementation `7a6db5d`; task A was
archived as `a2299c9` before task B started. The pre-execution configuration,
sources, parameters, observations and provenance are retained here.

- Four new J0 chains (252/342 days, recipes A/B), four stages per chain.
- Actual new nfev: 10,870 / 13,200; optimization/Jacobian forwards: 535,240.
- Task A+B combined: 12,470 / 14,800 nfev, 615,655 optimization forwards,
  1,204.845 seconds of monitored fitting, below the 30-minute cap.
- All chains completed. Final stages: three budget stops, one ftol stop;
  none met gtol=1e-7. No retry, new neural training or expanded budget.
- T selected recipe B and V selected recipe A for both outer prefixes.
  Four-point mean prediction RMSE (T -> V): 432 days, 58.0122 -> 25.4663 mm;
  612 days, 22.2448 -> 63.3987 mm. Mean training RMSE worsened in both.
- Strict simultaneous improvement of training and prediction RMSE/MAE:
  0/4 in each outer window, 0/8 combined. These are reused historical windows,
  not independent repetitions or a new blind evaluation.

The 432-day outer prefix has one internal window. The 612-day outer prefix
has three overlapping windows (540 window-days, 360 unique dates). Each
internal window favored A; that did not predict its next-window superiority.
Selection concerns the initialization/fitting recipe, followed by use of its
full-outer-prefix parameters, not transfer of short-prefix weights.

`fitted_parameters_locked.json`, `inner_scores_locked.json` and
`selection_locked.json` precede outer scoring. `metrics.csv`, `comparison.csv`
and `acceptance.csv` retain every station and both phases. Each fitted source,
stored curve, objective, selector and score was independently checked; see
`verification.json` and `validation_notes.json`.

The numerical replay checked 16 fitted stages and 16 trajectories, covering
568,064 mechanical substeps. Saved-curve forward error was 0 mm; the separate
mechanics implementation differed by at most 2.2737367544323206e-13 mm.
611 protected files and 19 source/test files retained their hashes.

Execution adds 32 initial objective checks, 16 terminal checks, and 16 native
trajectory forwards plus their custom audits. The independent verification
invocation adds 64 native forwards and zero fitting. Unit-test calls are
separate from these experiment counts. All 25 related unittest tests passed.
The initial pytest invocation found no installed pytest; that startup failure
is preserved in `pytest_unavailable.log`, and no dependency was installed.
Original NumPy warnings remain in the logs; the finite-value, trajectory and
constraint checks passed without changing the equations.

Original plot exports are retained at the run root. The `figures/` copies use
readable date spacing and margins, with the same curves and 60+180-day display;
both were visually checked. `render_figures.py` records input hashes and never
overwrites the original plots or observations.

To replay a sealed run without fitting or writing, from the repository root:

```bash
PYTHONPATH=code .venv/bin/python -m physics_guided_optimization_selection.verify results/ootang_bplus_v1_4/20260911_selection
```

The artifact manifest indexes all other files in this directory. Numerical
validation does not establish effectiveness, convergence or user/advisor
acceptance. The finite v1.4 comparison ends here with the negative result.
