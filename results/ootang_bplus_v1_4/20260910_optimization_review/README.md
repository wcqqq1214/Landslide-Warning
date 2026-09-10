# B+ optimization review before the v1.4 plan

This is a check of saved v1.2/v1.3 parameters, not a new calibration experiment.
`manifest.json` records 2,158 physical forward evaluations and zero optimizer
steps. No neural training occurred. Numerical derivative inputs are limited to
each 432/612-day training prefix; the selection replay reads already archived
historical metrics.

- `candidates.json`: repeated-forward identity, objective recomputation, gradient
  step sensitivity, proximity to bounds and stage-four parameter movement.
- `jacobian_columns.csv`: all 54 columns for all eight saved candidates.
- `singular_values.json`: local spectra after scaling each parameter by its bound
  width. These are step- and point-dependent diagnostics, not an identifiability
  proof.
- `state_and_selection_replay.json`: inactive O1_up plastic slip in 612-day C1 B,
  and a retrospective example where one internal validation window picks a worse
  recipe for the subsequent historical forecast.
- `probe.py`: exact diagnostic source; its default output is immutable and it
  refuses an existing directory. Use a new output directory when rerunning.
- `replay.py`: independent verification of the saved state/selection replay.
- `execution.log`, `protected_before.json`, `manifest.json`: raw warnings,
  original/archived source identity and execution provenance.

Small-step gradient comparisons found local sensitivity in the 432-day C1
solutions; the 612-day candidates have no columns above the stated 10% threshold.
The absence of such a flag does not prove a global solution. Four locally zero
Jacobian columns and zero training plastic slip in 612-day C1 B limit what can be
learned about those parameters in that trajectory. Do not delete or freeze them
globally on this evidence.

The new plan is recorded separately. No changed optimizer, loss, validation
selector, additional fitting budget or new neural model has been executed here.
