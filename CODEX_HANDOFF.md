# Codex handoff: Ootang machine prequential track and prior v5 work

**Prepared:** 2026-08-26
**Repository:** `/Users/wcqqq1214/Project/Landslide-Warning`
**Branch:** `main`
**Committed baseline:** `b4c04aa feat: add autonomous ootang monitoring safeguards`
**State:** the numerical audit, G1--G4 fail-closed preflight, and machine-only E1
prequential monitor are committed. E2-A engineering infrastructure is implemented,
fully verified, and forms the next commit boundary; nothing from this continuation
has been pushed.

## 2026-08-26 E2-A append-only live engineering continuation

The explicit-only `ootang-prequential-live` stage now performs one fully automatic
machine poll and exits. It uses a separate cold-start namespace, strict issue and
outcome inboxes, a SQLite WAL append-only hash chain, atomic multi-event issue and
outcome transactions, complete mathematical replay, revision-only retrospective
rescoring, lock-based single-writer recovery, and automatic waiting states. No
daily human date selection, threshold selection, or manual freeze is part of the
runner.

The runner is intentionally fail-closed and engineering-only. With no activation
artifacts it writes
`waiting_for_production_bundle_or_source_snapshot` and creates no ledger. A
historical complete outcome is appended as `backfill_not_blind` without inventing
an issue or changing online model state. A target on or before the current local
day cannot be retroactively issued. Outcome files are not loaded until all eight
station issues are durably sealed and an automatic anchor attempt has been
recorded.

E2-A does **not** yet generate the five predictions from checkpoint bytes, validate
the semantic contents of the external issue input manifest, cryptographically
verify a pinned time-stamp provider, or rotate immutable epochs automatically.
Those four facts are machine-readable in the profile and status. Consequently an
arbitrary HTTPS JSON receipt can be, at most, an
`engineering_blind_time_order_candidate`; v1 hard-codes
`trusted_anchor_receipt_verified=false`, `e2_live_evidence_eligible=false`, and
`real_activation_ready=false`.

Primary E2-A files:

- `config/ootang_prequential_live.v1.json`;
- `code/monitoring/prequential_core.py`;
- `code/monitoring/ootang_live_ledger.py`;
- `code/monitoring/ootang_prequential_live.py`;
- `tests/test_prequential_core.py`;
- `tests/test_ootang_live_ledger.py`;
- `tests/test_ootang_prequential_live.py`;
- `docs/ootang_prequential_live_engineering.md`.

The next implementation target is not manual live-data freezing. It is an
automatic content-addressed five-seed deployment/issue producer with verified
input semantics, checkpoint inference replay, immutable per-epoch registry and
safe machine epoch rotation. A trusted cryptographic time-receipt verifier is a
separate activation gate. Until those exist, the correct runtime state is an
automatic wait or fail-closed block, not fabricated live evidence.

Final E2-A verification: 64 targeted tests, 23 frozen gate tests, and 414/414
full-suite tests passed. Ruff and compileall passed; the real missing-prerequisite
poll returned the automatic waiting state without creating a ledger. All 18
register-referenced frozen files match, the four E1 output hashes are unchanged,
and the 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
The final suite exposed a pre-existing Markdown hard-break normalization in
`docs/v5_g1_g4_preflight.md` and `docs/v5_v0_numerical_audit.md`; their exact
registered bytes were restored without changing the frozen register. The five
resulting trailing-space lines are intentional hash-locked snapshot bytes.

## 2026-08-26 machine-only continuation note

The user explicitly rejected per-run manual freezing and daily human operation.
The current continuation therefore adds a separate machine research track; it
does not alter the formal-v5 gate snapshot or pretend that internal residuals
are independent disaster truth.

Implemented explicit-only stage:

```bash
uv run python main.py \
  --stage ootang-prequential-monitor \
  --manifest /tmp/ootang-prequential-monitor-v1-run.json
```

The source is the fixed 5-seed, 3-fold strict-temporal OOF bundle at
`figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv`
(34,440 rows). It is not the in-fit `figures/convlstm/forecast_predictions.csv`.
For each of 861 fold-dates the runner issues all eight stations from prior
state, computes and chains the issue-only batch hash in retrospective replay,
then reveals all eight outcomes and updates future state. Fold changes reset
online state; all three folds remain in one run-wide retrospective audit chain.

The persistence contract does not trust the prediction CSV by itself. The
runner retrieves `data/features.csv` from the source manifest commit
`1e06629119e08b33ded2540a435e726c2d2da97a`, verifies blob SHA-256
`366188ba1f55fd56b6606f5333e34566fca26c4771dd9745eebfabfcbd4286d1`
and size 1,424,208 bytes, then exactly checks all 6,888 target actuals and all
6,888 previous-natural-day persistence values.

The algorithm is fully machine-operated within E1:

- persistence + seed0--4 P50 online experts with scale-free exponential weights;
- symmetric absolute-residual conformal interval with ACI;
- one-sided underprediction residual p-value and continuous anomaly score;
- ADWIN-inspired bounded Hoeffding drift detector with automatic reset/rewarm;
- automatic abstain when history or spatial coverage is inadequate;
- O1/O2/O3 block max and cross-block min continuous aggregation;
- atomic bundle promotion, source/config/code/project/dependency-lock hashes,
  runtime version records, a recomputed run-wide issue chain, and cross-date
  station state-hash continuity checks;
- exact full-profile config validation and staged `%.17g` CSV round-trip reload,
  followed by a full replay of station state math, ACI, anomaly, drift, site
  aggregation, and metrics before promotion.

Materialized outputs are under `figures/prequential_anomaly_ootang_v1/`:

- `station_timeline.csv`: 6,888 rows, SHA-256
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`;
- `site_timeline.csv`: 861 rows, SHA-256
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`;
- `prequential_metrics.csv`: 27 rows, SHA-256
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`;
- `manifest.json`: SHA-256
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`.

The same explicit stage was run twice after final validator hardening; all four
hashes were identical. Final verification is 19 monitor tests, 91 combined
targeted tests, and 373 full tests, plus Ruff, compileall, diff-check, and both
dry-runs. The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.

Scientific result: MAE skill versus persistence is positive in all three folds,
but RMSE skill is not stable. Target-0.8 interval coverage falls from 0.791 to
0.695 and 0.631, while abstention is 0.426/0.392/0.422. Do not promote v1 to a
production warning product. The maximum anomaly score is the finite empirical
p-value floor, not a disaster label.

Durable machine-track sources:

- `docs/ootang_autonomous_research_protocol.md` -- E0--E3 evidence ladder,
  no-daily-manual-operation contract, E1/E2 distinction, and literature basis;
- `docs/ootang_prequential_monitor_results.md` -- exact metrics, counts, hashes,
  verification, interpretation, and reproduce commands;
- `config/ootang_prequential_monitor.v1.json` -- versioned E1 algorithm/source
  contract;
- `code/monitoring/ootang_prequential_monitor.py` -- runner and fail-closed
  validators;
- `tests/test_ootang_prequential_monitor.py` -- causality, tamper, determinism,
  provenance, and boundary tests;
- `figures/prequential_anomaly_ootang_v1/manifest.json` -- machine-readable run
  record.

The next engineering step is E2, not another retrospective threshold search:
implement automatic live ingest, real issue/outcome separation, append-only
event ledger, idempotent recovery, outcome revisions, and an automatic external
time anchor. With no new data the runner should stay in
`waiting_for_new_data`; no human should select dates. SPCI/AgACI or other
calibration challengers may follow under a new predeclared version, because the
current three-fold results have already been viewed. E3 remains blocked until
an independent, machine-readable outcome source with availability timestamps
exists; model residuals cannot generate their own disaster truth.

The untracked Vajont workbook and root `review.md` were not read or modified.

## 2026-08-20 current continuation note

The original candidate-display work below was subsequently committed and
pushed as `07ad977 feat: add automatic v0 candidate display`. A follow-up
numerical audit and a separate G1--G4 evidence/preflight pass have now been
completed in the working tree but are **not yet committed or pushed**.

The follow-up resolved ATU3/MJ9 `negative_segment_sse` as catastrophic
float64 cancellation in full-history prefix raw moments. The automatic-V0
wrapper now uses uniform per-segment-start local sufficient statistics with the
same minimum segment length, dynamic program, BIC, and scientific selection
rule. The shared historical v4 Bai--Perron source remains byte-for-byte
unchanged.

Post-audit outcomes:

- MJ1/MJ3 remain the only candidates, with exactly the same materialized V0;
- ATU1--ATU5 are unavailable because the first break is not accelerating;
- MJ9 is unavailable because the initial segment slope is nonpositive;
- all eight stations now retain six auditable selected segments (48 rows);
- the v5 display remains 4,112 rows, 1,028 available and 3,084 not applicable;
- the earlier numerical-audit run passed 48 targeted/330 full tests; the final
  combined tree now passes 71 targeted/353 full tests;
- 97 protected v4/ConvLSTM/NGBoost/model files are unchanged.

The subsequent G1--G4 inventory did not invent missing scientific evidence or
promote exploratory outputs:

- G0 is `PASS`; G1, G2, G3, and G4 are `BLOCKED`;
- there is no independent field/event label file or label manifest;
- the current Ootang range has already been exposed through fit, calibration,
  historical test, rolling validation, V0 selection, and review, so it contains
  no current unseen confirmation block;
- model fit starts on 2016-08-06, while the automatic-V0 runner intentionally
  uses the separate fit-end-bounded kinematics window 2016-07-01 through
  2019-02-02 (947 rows per station); do not collapse those two boundaries;
- automatic-V0 availability is 2/8 stations and 1,028/4,112 station-days
  (25%); the two stations are both in O1 and formal site output remains 0/514;
- event-recall, FAR, coverage, fusion-increment, and minimum-support numeric
  thresholds remain null and unapproved;
- no new NGBoost training or inference, fusion, warning color, or formal output
  was run or created.

`config/ootang_v5_gate_register.v1.json` is the machine-authoritative frozen
blocked snapshot for G0--G4. It must not be edited in place to turn G1--G4 into
PASS. Unblocking requires a new schema/version and corresponding manifest,
cross-hashed evidence validator, review, and an explicit switch of the
production default source. The production guard and CLI deliberately do not
accept an alternate register path or root.

Passing G0--G4 would be necessary but insufficient for G5a. A future G5a
candidate runner still needs a separate versioned run contract and guard that
freeze features, search space, seeds, calibration and failure rules,
input/implementation hashes, and a one-shot output namespace. That contract
and guard do not exist, so G5a remains unevaluated and unauthorized.

Durable continuation sources:

- `docs/v5_v0_numerical_audit.md` -- closed numerical investigation, fix,
  hashes, commands, and outcome;
- `docs/v5_validation_protocol.md` -- formal-v5 decision gates; G0 passes, G1
  through G4 are blocked, while G5a is separately unevaluated/unauthorized;
- `docs/v5_g1_g4_preflight.md` -- label/time/coverage/metric inventory and the
  exact unresolved decisions;
- `config/ootang_v5_gate_register.v1.json` -- immutable G0--G4 blocked
  snapshot with evidence hashes;
- `code/warning/ootang_v5_gate_preflight.py` -- fail-closed validator,
  read-only report CLI, and fixed-default G0--G4 production guard;
- `docs/progress.md` -- current engineering summary.

Do not start formal NGBoost/fusion work from this note. The next work requires
new independent labels, a genuinely unseen confirmation time block, and human
approval of the V0-unavailable deployment policy, coverage denominator,
numeric metric thresholds, confidence-interval method, and minimum support.
The Vajont workbook and `review.md` remain outside scope and were not
inspected.

## 1. Read this first

This work deliberately stops at a **candidate-display boundary**. It does **not** create a formal v5 warning system.

Non-negotiable scientific and implementation boundaries:

1. Automatic V0 selection is Ootang-only and fit-only. It uses raw cumulative displacement, actual elapsed time, wrapper-local per-segment-start OLS/DP, the frozen shared `_bic_for_segment_count` definition, and point velocities from the accepted first segment. The shared v4 file is unchanged; its DP/cost-matrix path is not called, while that BIC helper and constants are deliberately reused.
2. Only MJ1 and MJ3 pass the automatic V0 gate:
   - MJ1: `0.25034204499655494 mm/day`
   - MJ3: `0.24813126112408163 mm/day`
3. ATU1, ATU2, ATU3, ATU4, ATU5, and MJ9 remain explicitly `unavailable`. There is no manual date range, KMeans fallback, hidden V0 imputation, or borrowed v4 comparator.
4. Unavailable automatic-V0 rows now leave `candidate_v_mm_per_day`, selected-velocity mean/sigma, and `candidate_v0_mm_per_day` null. Segment slopes remain only in the segmentation audit table.
5. The v5 bundle is display-only:
   - `candidate_display_only=true`
   - `candidate_warning_color_output=false`
   - `ngboost_inference_output=false`
   - `v5_fusion_output=false`
   - `formal_warning_output=false`
   - `vajont_used=false`
6. The candidate timeline has no unqualified candidate color column. Raw interval state is retained as `interval_level`; existing v4 colors are historical references only and use the `v4_reference_*` prefix.
7. The historical v4 implementation, thresholds, fusion, outputs, ConvLSTM artifacts, and all NGBoost experiment artifacts remain unchanged.
8. Do not run, adapt, inspect, or stage the untracked Vajont workbook without fresh explicit user authorization.
9. Do not edit the v1 gate register to simulate progress. A valid future PASS
   state must arrive as a reviewed new schema/version with its own cross-hashed
   manifest and validator; G5a still requires a second, model-specific run
   contract and guard.

The approved plan is at:

`/Users/wcqqq1214/.claude/plans/cozy-sleeping-swan.md`

## 2. What is implemented

### 2.1 Automatic fit-only V0 diagnostic

Key files:

- `config/ootang_auto_v0_direct_bai_perron.v1.json`
- `code/warning/auto_v0_direct_bai_perron.py`
- `tests/test_auto_v0_direct_bai_perron.py`
- `figures/auto_v0_direct_bai_perron_ootang_v1/`

Important entry points:

- `code/warning/auto_v0_direct_bai_perron.py` — per-segment-start OLS cost matrix and wrapper-local DP, using the frozen shared BIC definition;
- `select_direct_candidate` — station candidate selection and unavailable gate;
- `write_auto_v0_candidates` — atomic artifact writer.

Selection rule:

- BIC chooses the segment count.
- Accept the first segment when its slope is positive and the immediately following segment is faster.
- If BIC selects one positive full-fit segment, emit `stable_full_fit_baseline`.
- Otherwise emit `unavailable` with an auditable reason.
- Only accepted candidates receive V, selected-segment point-velocity sigma, and V0.

Materialized files:

- `candidates.csv`
- `segments.csv`
- `candidate_diagnostics.png`
- `candidate_diagnostics.svg`
- `manifest.json`

Current unavailable reasons after the closed numerical audit:

- ATU1--ATU5: `first_break_is_not_accelerating`
- MJ9: `nonpositive_initial_segment_slope`

### 2.2 V5 candidate-display bundle

Key files:

- `config/ootang_v5_candidate_display.v1.json`
- `code/warning/ootang_v5_candidate_display.py`
- `tests/test_ootang_v5_candidate_display.py`
- `figures/v5_candidate_display_ootang_v1/`

Important entry points:

- `build_candidate_display_timeline` — builds the full result grid and availability-gated fields;
- `write_candidate_display` — validates full automatic-V0 method/code provenance and writes the bundle atomically;
- `main.py` — explicit-only stage registration immediately after automatic V0.

Materialized outputs:

- `candidate_timeline.csv` — 4,112 rows = 514 dates × 8 stations
- `candidate_summary.csv` — 8 rows
- `candidate_display.png`
- `candidate_display.svg`
- `manifest.json`

Current counts:

- `candidate_available`: 1,028 rows (MJ1/MJ3)
- `not_applicable_v0_unavailable`: 3,084 rows (six stations)
- 514 rows per station and 8 stations per date
- no candidate warning color, no NGBoost probability, no station/site v5 grade

For MJ1/MJ3 the display includes:

- observed displacement
- raw point velocity and automatic V0
- raw ΔV and fit-only MAD near-zero tolerance
- velocity/V0 ratio in CSV
- continuous tangent angle
- raw interval level

For the six unavailable stations:

- raw displacement/velocity/ΔV/interval information remains visible in the table
- V0-dependent velocity ratio and tangent angle are null
- branch status is `not_applicable_v0_unavailable`

### 2.3 Pipeline integration

The default no-argument chain is unchanged:

```text
features -> convlstm -> ootang-operational-v4
```

The new stages are explicit-only:

```bash
uv run python main.py \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display
```

Do not run the default pipeline merely to verify this work: it retrains ConvLSTM and can rewrite protected artifacts. Use the explicit command above or `--dry-run`.

### 2.4 G0--G4 frozen preflight snapshot

Key files:

- `config/ootang_v5_gate_register.v1.json`
- `code/warning/ootang_v5_gate_preflight.py`
- `tests/test_ootang_v5_gate_preflight.py`
- `docs/v5_g1_g4_preflight.md`

The register freezes the evidence-backed current state rather than offering an
editable workflow switch. Its evidence records are repository-relative and
SHA-256 locked; malformed JSON, duplicate or unknown keys, stale hashes,
out-of-scope paths, non-finite values, and attempts to mark an unsupported gate
PASS fail closed.

Read-only reporting:

```bash
uv run python code/warning/ootang_v5_gate_preflight.py --report
```

The default mode and `--require-g0-g4` validate the sole production-default
snapshot and exit blocked while G1--G4 remain blocked. The production
`require_g0_g4_preflight()` entry point likewise accepts no alternate path or
root. It must be called before any future runner reads label/training payloads
or creates outputs. It does not authorize G5a; the report keeps
`g5a_authorization_evaluated=false` and `g5a_authorized=false`.

Final register fingerprints:

- file SHA-256: `84b1e53c9eeb908b09bca6e3720c970b8ea716f8a959f295c919bac82077a7ef`;
- canonical content SHA-256: `d8bf7badcdec935ccb1f86439534a18a397a924b42eae6fda2fa38071b5c9821`.

### 2.5 Mentor-facing report and indexes

Updated:

- `paper/process_report.tex`
- `README.md`
- `docs/design.md`
- `docs/progress.md`

Report boundary section:

- `paper/process_report.tex` — historical v4 versus v5 candidate display, automatic V0 method/results, 2/8 availability, and the explicit no-inference/no-fusion/nonformal boundary

The candidate figure was changed to a portrait 5-row × 2-station layout so it remains legible on an A4 report page. The report compiles to 17 pages. PDFs/build intermediates are ignored; rebuild from source.

## 3. Final verification completed

The recorded results below cover the completed numerical-audit,
candidate-display, and final G0--G4 preflight tree. The earlier audit-only
counts remain in `docs/v5_v0_numerical_audit.md`; the current combined counts
below include the new preflight tests.

### Explicit stages

Final explicit run:

```bash
uv run python main.py \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display \
  --manifest /tmp/ootang-v5-numerical-fix-run.json
```

Result: both stages completed with input/output contracts passed in 4.7 seconds. The `/tmp` manifest is session-local and should not be treated as a repository artifact.

### Tests and static checks

Final results:

- G0--G4 preflight module: **23 passed** in 0.199 seconds
- targeted automatic-V0 + v5 + preflight + pipeline tests: **71 passed** in 8.298 seconds
- full suite: **353 passed** in 274.412 seconds
- `uv run ruff check code tests main.py`: passed
- `uv run python -m compileall -q code main.py tests`: passed
- `git diff --check`: passed
- default dry-run: exactly `features -> convlstm -> ootang-operational-v4`
- explicit dry-run: exactly `ootang-auto-v0-direct-bai-perron -> ootang-v5-candidate-display`

The full unittest output includes expected argparse usage text and mocked pipeline-failure messages from negative tests; the suite still ends `OK`.

### Added regression coverage

- unavailable automatic-V0 records must not contain V/sigma/V0 estimates
- no unqualified color column may enter the v5 timeline
- only MJ1/MJ3 receive V0-dependent fields
- all 4,112 rows remain present
- no v4 fusion function is called
- writing a candidate bundle leaves v4 station/site/threshold/manifest hashes unchanged
- output CSV/PNG/SVG determinism
- source/output manifest hashes
- report source nonclaims
- real `latexmk`/XeLaTeX report build (skipped only when those tools are unavailable)

### Protected-state checks

- `code/warning/bai_perron_initial_slope.py` is restored exactly to `HEAD`; the new wrapper is local to `auto_v0_direct_bai_perron.py`.
- The versioned list `docs/v5_v0_protected_paths.txt` freezes 97 v4, ConvLSTM, NGBoost, and model paths; their before/after hashes match **97/97**, and the shared v4 Bai--Perron source is separately unchanged.
- The v5 manifest now records producer provenance (`git_commit`, dirty flag, and implementation-source SHA-256 records) in addition to input/output hashes.

### Report and figure checks

- report compiled successfully with XeLaTeX
- final log had no LaTeX errors, undefined references, or `Float too large`
- 17-page contact sheet was visually inspected; candidate figure is legible on page 16
- categorical figure colors `#2a78d6,#1baf7a,#eb6834,#4a3aa7` passed the dataviz palette validator; the green line had a contrast warning, mitigated by direct panel titles/axis labels and the CSV/table view

## 4. Current git state

No commit or push was performed.

Tracked files modified:

- `README.md`
- `main.py`
- `code/warning/auto_v0_direct_bai_perron.py`
- `code/warning/ootang_v5_candidate_display.py`
- `config/ootang_auto_v0_direct_bai_perron.v1.json`
- `docs/design.md`
- `docs/progress.md`
- `figures/auto_v0_direct_bai_perron_ootang_v1/` candidate/segment/diagnostic/manifest artifacts
- `figures/v5_candidate_display_ootang_v1/manifest.json`
- `paper/process_report.tex`
- `tests/test_auto_v0_direct_bai_perron.py`
- `tests/test_main.py`
- `tests/test_ootang_v5_candidate_display.py`

New numerical-audit and G0--G4 preflight files:

- `CODEX_HANDOFF.md` (this document)
- `code/warning/ootang_v5_gate_preflight.py`
- `config/ootang_v5_gate_register.v1.json`
- `docs/v5_v0_numerical_audit.md`
- `docs/v5_v0_protected_paths.txt`
- `docs/v5_validation_protocol.md`
- `docs/v5_g1_g4_preflight.md`
- `tests/test_ootang_v5_gate_preflight.py`

New machine-prequential files and outputs:

- `code/monitoring/__init__.py`
- `code/monitoring/ootang_prequential_monitor.py`
- `config/ootang_prequential_monitor.v1.json`
- `tests/test_ootang_prequential_monitor.py`
- `docs/ootang_autonomous_research_protocol.md`
- `docs/ootang_prequential_monitor_results.md`
- `figures/prequential_anomaly_ootang_v1/`

Pre-existing untracked files that are outside this task and must not be staged or modified without an explicit decision:

- `data/vajont_fig5a_curves_2_3_4_5_58_mm_velocity.xlsx`
- `review.md`

Before committing, use an explicit path list; do not use a blind `git add .`.

## 5. Recommended next Codex actions

1. Read this handoff, `AGENTS.md`,
   `docs/ootang_autonomous_research_protocol.md`,
   `docs/ootang_prequential_monitor_results.md`, the prequential config, and its
   output manifest before continuing the machine track.
2. Recheck the final four output hashes and the 97 protected paths. Also verify
   the shared v4 Bai–Perron module is still clean:

   ```bash
   git diff --exit-code -- code/warning/bai_perron_initial_slope.py
   xargs shasum -a 256 < docs/v5_v0_protected_paths.txt | shasum -a 256
   ```

3. Continue with E2-B activation prerequisites: build a content-addressed
   five-seed production bundle and issue producer that replay checkpoint
   inference, validate the input-manifest semantics, retain immutable per-epoch
   artifacts, and rotate epochs by machine policy. Do not use historical OOF CSV
   rows as future predictions and do not select a best seed.
4. Add a pinned, cryptographically verified time-receipt adapter before any
   `engineering_blind_time_order_candidate` can become E2 evidence. Keep operation
   machine-only. No new data means
   `waiting_for_new_data`; schema/hash/state conflict means
   `blocked_integrity`; neither state should trigger a human date-selection
   workflow or fabricated backfill.
5. If improving interval calibration, create a separately versioned,
   predeclared challenger such as SPCI/AgACI and compare it on future E2 data or
   a valid new evaluation protocol. Do not tune the current v1 from the already
   viewed replay and then report the same folds as confirmation.
6. Keep the formal-v5 path separate. Its read-only preflight must still show G0
   PASS, G1--G4 BLOCKED, and G5a unauthorized; do not edit the v1 gate register
   or run NGBoost/fusion to manufacture missing evidence. E3 requires an
   independent machine-readable outcome source, not monitor-derived labels.
7. If changing any current producer, regenerate its explicit outputs and rerun
   targeted tests, the full suite, static checks, deterministic replay, and the
   97-path comparison.
8. Commit or push only when the user explicitly requests it. Keep
   implementation, tests, artifacts, and documentation in the same non-`test:`
   commit; do not create a pull request.

## 6. Known non-blocking follow-ups

These are not blockers for the completed candidate-display task:

- The automatic-V0 runner still requires ConvLSTM/v4 lineage artifacts for fit-boundary and provenance validation, even though no v4 value participates in V0 selection. A future refactor could separate boundary metadata from v4 lineage, but changing the frozen input contract should be an explicit decision.
- `ootang_v5_candidate_display.py` duplicates a few hashing/profile helper patterns used by the NGBoost pilot. Consolidation is optional and should not blur artifact-specific contracts.
- The automatic V0 module owns its numerically stable per-segment-start OLS cost matrix and dynamic program, but imports the frozen shared BIC definition/constants. Do not move the wrapper into or edit the shared v4 module without reopening G0 and the v4 freeze.
- No current `figures/pipeline/latest_run.json` exists because the default pipeline was intentionally not run.

## 7. User/project instruction snapshot

The following is included so Codex does not lose the session's persistent context. Verify live files before relying on any old memory.

### `/Users/wcqqq1214/.claude/projects/-Users-wcqqq1214-Project-Landslide-Warning/memory/MEMORY.md`

```markdown
# Memory Index

- [Git workflow rules](feedback_git_workflow.md) — no PRs, push to main directly, no Co-Authored-By, linear history, no superpowers docs
```

### `/Users/wcqqq1214/.claude/projects/-Users-wcqqq1214-Project-Landslide-Warning/memory/feedback_git_workflow.md`

```markdown
---
name: feedback_git_workflow
description: "Git workflow rules for this project — no PRs, push directly to main, no Co-Authored-By, linear history"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f6e2f739-d868-47e0-893e-66a510f37d01
---

- Do NOT create pull requests. Push directly to main — personal repository.
- Do NOT add `Co-Authored-By` lines to commit messages.
- Commit format: `type: description` — English, lowercase, concise.
- Do NOT commit `docs/superpowers/` or any superpowers-generated docs.
- Keep linear history; force push when needed (e.g., after amend or filter-repo).

**Why:** Personal repo, no review process needed. User prefers clean, linear git history.
**How to apply:** On every commit/push operation in this project.
```

### `/Users/wcqqq1214/.claude/CLAUDE.md`

```markdown
# Git commits

- Unless explicitly requested otherwise, keep commit messages concise: use a short subject line and add a body only when it provides necessary context.
- Do not add `Co-Authored-By` or other attribution trailers unless explicitly requested.
```

### Repository `CLAUDE.md`

```markdown
@AGENTS.md
```

### Repository `AGENTS.md`

```markdown
# Interaction Principles

- You may challenge my views — I'm not always right. Maintain a critical mindset: question my instructions and opinions, point out flaws when you see them, and suggest better alternatives.

# Research Tooling

- `academic-research-suite` is available for literature review, manuscript structure, citation checks, revision, and peer-review simulation.
- Use it as an advisory research-writing tool only; do not let it modify frozen validation splits, metrics, thresholds, model structure, or experimental conclusions.
- All literature claims must be checked against source papers, versioned data, and reproducible project artifacts before being treated as final.

# Commit Guidelines

- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

# Git Rules

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
```

## 8. Final review status

The original independent review found four blockers: estimates on unavailable stations, an unqualified interval color, modification of a shared v4 module, and missing automated v4/report checks. All four were fixed before the original candidate-display commit:

- unavailable estimates are now null
- `interval_color` is no longer materialized
- the shared v4 Bai–Perron module matches `HEAD`
- hash-immutability and real LaTeX-build tests were added

A second independent review of the numerical-audit follow-up found the numerical implementation sound, then identified integration blockers in provenance validation and the G5/G6 gate wording. The current working tree adds fail-closed drift tests for the full profile method, V0 formula, runner/shared-BIC records, complete kinematics/predictions/forecast/v4 lineage, and all station fit boundaries. The protocol now separates G5a/G6a generation authorization from G5b/G6b calibration acceptance and consistently reserves historical test/confirmation data from selection. The final read-only re-review reported no remaining blocker. Exact verification commands and the 97 protected paths are versioned in the audit documents; any later edit must repeat them before relying on this verdict.
