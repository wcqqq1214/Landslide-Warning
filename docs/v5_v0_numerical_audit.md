# V5 automatic V0 numerical audit

**Status:** closed  
**Started:** 2026-08-20  
**Completed:** 2026-08-20  
**Baseline commit:** `07ad977f762309ed3f6c1574313abc22631cfbb3`

## Purpose

Determine whether the `negative_segment_sse` result for Ootang stations ATU3 and
MJ9 is a genuine data/method limitation or a numerical implementation defect.
The result gates any later interpretation of the current 2/8 automatic-V0
availability count.

This audit is deliberately limited to the fit-only automatic-V0 diagnostic. It
must not:

- change the historical v4 implementation, thresholds, fusion, or artifacts;
- introduce a manual stage range, fallback V0, KMeans promotion, imputation, or
  borrowed comparator;
- use calibration/test observations to select V0;
- inspect or use the untracked Vajont workbook;
- create formal v5 warning, color, NGBoost inference, or fusion output.

## Reproduction baseline

The diagnostic feedback loop loads `data/ootang_kinematics_long.csv`, retains
valid displacement rows through the frozen fit boundary `2019-02-02`, and calls
`segment_piecewise_linear_signal` for ATU3 and MJ9. It asserts that the real
segmentation path must not terminate with `negative_segment_sse`.

The loop was run twice from the baseline commit and deterministically failed in
about 2.3 seconds per attempt:

```text
attempt=1 station=ATU3 rows=947 error=negative_segment_sse
attempt=1 station=MJ9 rows=947 error=negative_segment_sse
attempt=2 station=ATU3 rows=947 error=negative_segment_sse
attempt=2 station=MJ9 rows=947 error=negative_segment_sse
```

This is a red-capable reproduction of the exact materialized failure reason,
not an inference from `candidates.csv`. No repository artifact is written by the
loop.

## Final interpretation

The failure was a numerical implementation defect, not a negative physical or
statistical residual. The corrected implementation lets all eight stations
complete the declared segmentation algorithm before the scientific selection
gate is applied. ATU3 and MJ9 remain `unavailable`, but now for auditable method
reasons rather than a numerical exception. The 2/8 availability count is
therefore numerically resolved for this frozen fit-only algorithm; it is still
an exploratory candidate result, not a field-validated or formal V0 conclusion.

## Minimal reproduction

Scanning eligible segments by increasing end index found the earliest failing
prefix and segment for each station:

| Station | Earliest failing prefix | Segment indices | Segment dates | Rows |
|---|---:|---:|---|---:|
| ATU3 | 776 | `[738, 776)` | 2018-07-09 through 2018-08-15 | 38 |
| MJ9 | 931 | `[901, 931)` | 2018-12-19 through 2019-01-17 | 30 |

Scanning by increasing segment width produced a 30-row failure for each
station, which is the configured minimum and therefore cannot be reduced
further without leaving the real algorithm contract:

| Station | Minimum failing segment | Segment dates | Result when isolated and rebased |
|---|---:|---|---|
| ATU3 | `[751, 781)` | 2018-07-22 through 2018-08-20 | passes, SSE `1.42e-14` through the public wrapper |
| MJ9 | `[901, 931)` | 2018-12-19 through 2019-01-17 | passes, SSE `2.54e-10` through the public wrapper |

The same observations fail only when their statistics are obtained by
subtracting the full-history prefix sums. This makes the preceding prefix
history a load-bearing part of the reproduction.

## Ranked hypotheses and probes

| Rank | Hypothesis and falsifiable prediction | Probe result | Status |
|---:|---|---|---|
| 1 | Late short segments lose precision when large prefix sums and closed-form OLS terms are subtracted. Direct residual evaluation and a stable reference fit should remain nonnegative. | The two float64 closed-form SSEs are `-6.565824151e-08` and `-1.811713446e-09`; direct residual SSEs are nonnegative. A 60-digit Decimal calculation gives `3.081224779e-26` and `2.540304042e-10`. | Supported |
| 2 | The existing tolerance omits error inherited from the prefix operands. The failures should lie just outside the current post-subtraction scale but inside a prefix-aware rounding scale. | The negative magnitudes are only `1.061x` and `1.076x` the current tolerances, while both are below `eps * 32 * max(prefix operands)`. | Supported, but a larger clamp alone is not accepted as a fix |
| 3 | Duplicate/near-duplicate time values or rank deficiency cause an invalid fit. | Both windows have one-day increments, rank 2, and centered design condition number `8.66`. | Falsified |
| 4 | Nearly exact local linearity makes true residual error small enough for rounding error to dominate. | Decimal SSE is about `3.08e-26` for ATU3 and `2.54e-10` for MJ9. | Supported as trigger, not root defect |
| 5 | Higher-precision accumulation should remove the sign error. | This platform's NumPy `longdouble` is 8 bytes and has the same epsilon as float64, so it is not an independent probe. The independent 60-digit Decimal calculation removes the sign error. | Supported by Decimal; `longdouble` unavailable here |

Across every eligible segment under the current coordinate convention, ATU3
has 167 and MJ9 has 160 negative SSE values beyond the existing tolerance.
The other six stations have none beyond tolerance. Some accepted stations do
contain tiny negative closed-form values, but the existing tolerance correctly
clamps them.

## Rejected shortcut

Translating both axes to their global means makes the complete ATU3 and MJ9
engines run, but it is not a general repair: the same translation creates 33
above-tolerance negative segments for ATU2. A coordinate translation is
mathematically invariant, yet choosing one translation that happens to pass a
particular station would be a data-dependent numerical workaround. It will not
be used as the production fix.

## Implemented correction

Profile `ootang-auto-v0-direct-bai-perron-v1` was patch-bumped from
`1.0-candidate` to `1.0.1-candidate`. The automatic-V0 wrapper now applies one
uniform computation to every station and every eligible segment:

1. translate time and displacement to the candidate segment's own first point;
2. form that segment's local float64 sufficient statistics without subtracting
   a full-history prefix;
3. calculate the same unconstrained OLS slope and SSE;
4. apply the existing `32 * eps * local_OLS_scale` roundoff clamp;
5. run the same minimum-length, exact dynamic program, BIC formula, tie-break,
   and first-segment acceptance rule;
6. transform the intercept back with
   `a_global = a_local + y_start - slope * x_start`.

This is not an error-triggered retry and does not select a coordinate origin by
station result. The shared historical v4 implementation
`code/warning/bai_perron_initial_slope.py` was not modified. The automatic-V0
manifest now records the local-statistics method and hashes both the wrapper
and the shared BIC definition.

## Result impact

| Station group | Corrected outcome | Impact |
|---|---|---|
| ATU1, ATU2, ATU4, ATU5 | `unavailable / first_break_is_not_accelerating` | Segment count, boundaries, and status unchanged. |
| ATU3 | `unavailable / first_break_is_not_accelerating` | Six BIC-selected segments are now auditable instead of an empty numerical failure. |
| MJ9 | `unavailable / nonpositive_initial_segment_slope` | Six BIC-selected segments are now auditable instead of an empty numerical failure. |
| MJ1, MJ3 | `initial_segment_selected` | V0 remains exactly `0.25034204499655494` and `0.24813126112408163` mm/day in the materialized CSV. |

The selected segmentation still has six segments per station. The audit table
therefore grows from 36 to 48 rows. For the six stations that completed before
the correction, every segment boundary and selection status is unchanged; the
largest absolute selected-BIC drift is `4.1345856516272761e-08` (MJ3), while
the largest materialized MJ1/MJ3 V0 drift is exactly zero.

The downstream candidate display remains 4,112 rows over 514 dates and eight
stations: 1,028 `candidate_available` rows for MJ1/MJ3 and 3,084
`not_applicable_v0_unavailable` rows for the other six stations. It still emits
no candidate warning color, NGBoost inference, v5 fusion, or formal warning.

## Verification record

The regression test was added before the correction. It initially failed for
both ATU3 and MJ9 with `negative_segment_sse`; after the correction it verifies
all eight real fit windows, the original six-station boundary/status contract,
the corrected ATU3/MJ9 scientific failure reasons, nonnegative selected SSEs,
and late-segment intercept back-transformation against direct least squares.

Final commands and results:

```text
explicit stages: ootang-auto-v0-direct-bai-perron -> ootang-v5-candidate-display
explicit runtime: 4.7 s; both input/output contracts passed
targeted unittest modules: 48 tests in 7.943 s, OK
full unittest discovery: 330 tests in 177.820 s, OK
ruff check code tests main.py: passed
python compileall code main.py tests: passed
git diff --check: passed
default dry-run: features -> convlstm -> ootang-operational-v4
explicit dry-run: ootang-auto-v0-direct-bai-perron -> ootang-v5-candidate-display
protected comparison: 97/97 v4, ConvLSTM, NGBoost, and model files unchanged
debug instrumentation remaining: none
```

The targeted tests include deterministic CSV/PNG/SVG regeneration, source and
output hash validation, v4 immutability, and a real XeLaTeX report build. The
downstream display also fails closed when the automatic-V0 method, V0 formula,
runner/shared-BIC record, kinematics/predictions/forecast/v4 lineage, or any
station fit boundary is missing or differs from the validated current inputs.

The exact acceptance commands are preserved below. Run the first hash snapshot
before the two explicit stages and the second immediately afterwards. The
versioned 97-path scope is `docs/v5_v0_protected_paths.txt`; the shared v4
Bai--Perron source is checked separately because it is the frozen definition
used for the historical v4 path.

```bash
test "$(wc -l < docs/v5_v0_protected_paths.txt | tr -d ' ')" = 97
xargs shasum -a 256 < docs/v5_v0_protected_paths.txt \
  > /tmp/ootang-v5-protected-before.sha256

uv run python main.py \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display \
  --manifest /tmp/ootang-v5-numerical-fix-run.json

xargs shasum -a 256 < docs/v5_v0_protected_paths.txt \
  > /tmp/ootang-v5-protected-after.sha256
diff --unified=0 \
  /tmp/ootang-v5-protected-before.sha256 \
  /tmp/ootang-v5-protected-after.sha256
git diff --exit-code -- code/warning/bai_perron_initial_slope.py

uv run python -m unittest \
  tests.test_auto_v0_direct_bai_perron \
  tests.test_ootang_v5_candidate_display \
  tests.test_main
uv run python -m unittest discover -s tests
uv run ruff check code tests main.py
uv run python -m compileall -q code main.py tests
git diff --check
uv run python main.py --dry-run
uv run python main.py --dry-run \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display
```

Key post-run SHA-256 fingerprints:

| Item | SHA-256 |
|---|---|
| automatic-V0 implementation | `d8b63f24c119011f4e7b0351965cb840915bfca4cebcefb45c39b04cd52a98f3` |
| v5 candidate-display implementation | `bca678d7c8d6f357b872f41b81dd022bf4dbdf31abb4133ff1a202e70d29f856` |
| automatic-V0 profile | `47c0b639917cd0b96e797b21d8095898ebc4f2088038925036f9c51aaa1273d9` |
| automatic-V0 manifest | `fb826a734d0d9ec11e2bfca9097080b3eb28b4325760ad62c26b799c15f1ef71` |
| v5 candidate-display manifest | `dfa5b62e8a12215c4ebe13404edc62e8294b276e9d5d8940a4bfd9b9ab18d6a6` |
| shared v4 Bai--Perron source | `85bdc543b118926582532f583beab9336646f8b6d13565fe1420a8e3eab01280` |
| v4 operational manifest | `ae83bb9538d15e3715df9115b9ec244dfab555ee01e2d8a3b63a493735f7f120` |

## Investigation log

| Date | Phase | Evidence | Decision |
|---|---|---|---|
| 2026-08-20 | Reproduction | Both stations fail twice on 947 fit-only rows with `negative_segment_sse`. | Proceed to minimal reproduction and numerical probes; do not alter candidate artifacts yet. |
| 2026-08-20 | Minimization | Minimum allowed 30-row windows fail only with full-history prefix statistics and pass when locally rebased. | Retain prefix history in the regression test. |
| 2026-08-20 | Root cause | High-precision and direct-residual fits are nonnegative; date/rank checks pass. | Treat the result as catastrophic cancellation in the prefix/closed-form SSE path, not a data-derived unavailable state. |
| 2026-08-20 | Shortcut check | Global mean centering fixes the two reported stations but makes 33 ATU2 segments exceed tolerance. | Reject station-sensitive translation as the fix. |
| 2026-08-20 | Regression | Real fit-window test failed before the fix for ATU3/MJ9 and passed after the uniform local-statistics implementation. | Accept the public automatic-V0 seam as the permanent regression guard. |
| 2026-08-20 | Regeneration | Eight stations complete segmentation; ATU3 and MJ9 receive scientific failure reasons; candidate availability remains 2/8. | Replace the old numeric-failure artifacts and update the report/progress records. |
| 2026-08-20 | Closure | 48 targeted and 330 full tests pass; 97 protected hashes and the shared v4 source are unchanged. | Close the numerical gate only; retain all formal-v5 gates. |

## Completion checklist

- [x] exact failing segments and negative-SSE magnitudes recorded;
- [x] direct residual and high-precision reference fits compared;
- [x] ranked falsifiable hypotheses tested;
- [x] regression test observed failing before the correction;
- [x] uniform wrapper-only correction implemented and tested;
- [x] both explicit bundles regenerated with hashes;
- [x] shared v4 source and 97 protected files verified unchanged;
- [x] report, design, progress, and formal-v5 protocol synchronized;
- [x] numerical gate closed without promoting a formal V0 or v5 warning.
