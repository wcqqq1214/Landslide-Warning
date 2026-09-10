# v1.4 task A: same-prefix continuation

Run: 20260911_optimization. Plan and source snapshots predate fitting.

- Two prescribed 800-nfev continuations completed, 1,600 total.
- 432-day J1: 16.3678843409 -> 16.1607473528 (1.2655% lower).
- 612-day J1: 32.4441170424 -> 31.4977985109 (2.9168% lower).
- Both reached the budget limit; neither met gtol=1e-7.
- No forecast scoring, temporal selection, or neural training in task A.
- Independent verification reproduced 10 saved trajectories, 333,440 substeps,
  and both stages. Maximum saved-curve replay error: 0 mm.
- 540 protected files and 19 source/test files retained their hashes.

`summary.json` is descriptive; `verification.json` reports numerical checks,
not optimizer rerun or scientific effectiveness. All original warnings remain
in logs. Raw anchor, clipped initial parameters, objective components,
retention decisions and gradient checks are retained separately.

Calls during execution: 80,415 optimization/Jacobian forwards; 4 initial
objective checks; 2 terminal objective checks; 10 native trajectory forwards;
112 derivative-check forwards. The 10 custom audit trajectories cover
333,440 mechanical substeps. Independent verification adds 16 native forwards
and no fitting. Counts are not interchangeable with nfev.

The artifact manifest indexes every other archived file; task B refers to
this sealed run and inherits the remaining portion of the 30-minute total
fitting timeout. No automatic retry or budget extension.
