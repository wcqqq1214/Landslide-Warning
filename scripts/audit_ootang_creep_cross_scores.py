"""Score six frozen mean/scale combinations; no training or native solver calls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import types

import numpy as np
import pandas as pd

import verify_ootang_creep_pinn as frozen

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/ootang_probability_pinn_v2_3/20260912_development"
SOURCE_COMMIT = "9a63ee5c332538e44b256663423c98f178735a68"
MANIFEST_SHA = "cd860669ee0858fffcb5176cae2701edb255328ad78dd00edc3aac8f7163f8b9"
VERIFIER_SHA = "967491d69097df8497cc92d041ef688c906fbb7177a3476146b4acca0e5a1c06"
MEANS = ("bplus", "neural", "replay")
SCALES = ("e0", "v23")
POINTS = frozen.POINTS
DOMAINS = ("O3", "O2", "O1_up", "O1_down")
ORIGINALS = {
    ("bplus", "e0"): "v1.1-e0",
    ("neural", "v23"): "PINN",
    ("replay", "v23"): "replay_diagnostic",
}


def phases(n, h):
    if not 30 < h < n:
        raise ValueError("Expected the unchanged 30-day exclusion and two full phases")
    return {"train": slice(30, h), "prediction": slice(h, n)}


def score_matrix(mean_sources, scale_sources, observed, fit_days, probability):
    """Keep seed k's mean paired with seed k's scale, including in cross scores."""
    if set(mean_sources) != set(MEANS) or set(scale_sources) != set(SCALES):
        raise ValueError("Exactly three frozen mean sources and two scales required")
    y = np.asarray(observed)
    if y.ndim != 2 or y.shape[1] != 4 or not np.isfinite(y).all():
        raise ValueError("Expected finite observations for all four points and dates")
    n = len(y)
    masks = phases(n, fit_days)
    for values in (*mean_sources.values(), *scale_sources.values()):
        if np.shape(values) != (3, n, 4):
            raise ValueError(
                "Exactly three seeds on the full shared date/point axis required"
            )
    rows = []
    for mean_source in MEANS:
        for scale_source in SCALES:
            origin = ORIGINALS.get((mean_source, scale_source))
            for phase, mask in masks.items():
                mu = mean_sources[mean_source][:, mask]
                sigma = scale_sources[scale_source][:, mask]
                probability.validate(mu, sigma)  # Reject missing rows; never drop them.
                summary = probability.summarize(mu, sigma)
                error = summary["mean"] - y[mask]
                values = {
                    "mae": np.abs(error).mean(axis=0),
                    "rmse": np.sqrt(np.mean(error**2, axis=0)),
                    "crps": probability.crps(mu, sigma, y[mask]).mean(axis=0),
                }
                for level in (80, 90, 95):
                    lo, hi = summary[f"lower_{level}"], summary[f"upper_{level}"]
                    values[f"coverage_{level}"] = (
                        (lo <= y[mask]) & (y[mask] <= hi)
                    ).mean(axis=0)
                    values[f"width_{level}"] = (hi - lo).mean(axis=0)
                    values[f"interval_score_{level}"] = probability.interval_score(
                        y[mask], lo, hi, level
                    ).mean(axis=0)
                common = dict(
                    mean_source=mean_source,
                    scale_source=scale_source,
                    role="frozen_original" if origin else "cross_diagnostic",
                    original_model=origin or "",
                    phase=phase,
                    days=len(y[mask]),
                )
                for metric, vector in values.items():
                    for station, value in zip(
                        (*POINTS, "point_mean"), (*vector, vector.mean())
                    ):
                        rows.append(
                            dict(
                                **common,
                                station=station,
                                metric=metric,
                                value=float(value),
                                unit="fraction"
                                if metric.startswith("coverage_")
                                else "mm",
                            )
                        )
                rows.append(
                    dict(
                        **common,
                        station="pooled",
                        metric="rmse",
                        value=float(np.sqrt(np.mean(error**2))),
                        unit="mm",
                    )
                )
    table = pd.DataFrame(rows)
    if not np.isfinite(table.value).all():
        raise ValueError("Nonfinite score")
    return table


def contrasts(table):
    """Signed left-minus-right score changes; these are not causal contributions."""
    pairs = []
    for mean in MEANS:
        pairs.append(("scale_v23_minus_e0", (mean, "v23"), (mean, "e0")))
    for scale in SCALES:
        pairs.extend(
            [
                ("neural_minus_replay", ("neural", scale), ("replay", scale)),
                ("replay_minus_bplus", ("replay", scale), ("bplus", scale)),
            ]
        )
    rows = []
    for contrast, left, right in pairs:

        def part(key):
            return table[(table.mean_source == key[0]) & (table.scale_source == key[1])]

        merged = part(left).merge(
            part(right),
            on=["phase", "station", "metric", "unit", "days"],
            validate="one_to_one",
            suffixes=("_left", "_right"),
        )
        for r in merged.itertuples(index=False):
            delta = r.value_left - r.value_right
            if contrast == "scale_v23_minus_e0" and r.metric in ("mae", "rmse"):
                assert delta == 0, "Changing only scales changed the mean score"
            rows.append(
                dict(
                    contrast=contrast,
                    left_mean=left[0],
                    left_scale=left[1],
                    right_mean=right[0],
                    right_scale=right[1],
                    phase=r.phase,
                    station=r.station,
                    metric=r.metric,
                    delta=delta,
                    unit=r.unit,
                    days=r.days,
                )
            )
    return pd.DataFrame(rows)


def state_differences(reference, selected, h):
    """Compare saved substep states and daily means without re-solving dynamics."""
    rows = []
    n = len(reference["dates"])
    blocks = (
        "motion",
        "plastic",
        "basal_reaction",
        "contact_reaction",
        "bulk_reaction",
        "background",
    )
    for seed in range(3):
        values = frozen.read_npz(RUN / f"seed_{seed}/prediction.npz")
        native = frozen.read_npz(RUN / f"seed_{seed}/reference_trace.npz")
        for phase, mask in phases(n, h).items():
            # Row k of current is the end of substep k+1. Score all substeps
            # ending on days in the original daily scoring mask, without reset.
            start, end = (mask.start - 1) * 64, (mask.stop - 1) * 64
            delta = values["states"][start + 1 : end + 1] - native["current"][start:end]
            assert delta.shape == ((mask.stop - mask.start) * 64, 24)
            vectors = [
                (
                    name,
                    delta[:, 4 * i : 4 * i + 4],
                    DOMAINS,
                    "mm" if i in (0, 1, 5) else "original_generalized_reaction",
                )
                for i, name in enumerate(blocks)
            ]
            vectors.append(
                (
                    "observed_mean",
                    selected["means"][seed, mask]
                    - selected["reference_means"][seed, mask],
                    POINTS,
                    "mm",
                )
            )
            for name, difference, axes, unit in vectors:
                assert np.isfinite(difference).all()
                for j, axis in enumerate(axes):
                    d = difference[:, j]
                    rows.append(
                        dict(
                            seed=seed,
                            phase=phase,
                            quantity=name,
                            axis=axis,
                            unit=unit,
                            samples=len(d),
                            signed_mean=float(d.mean()),
                            mean_absolute=float(np.abs(d).mean()),
                            rms=float(np.sqrt(np.mean(d**2))),
                            max_absolute=float(np.abs(d).max()),
                        )
                    )
    return pd.DataFrame(rows)


def run(output):
    output = output.resolve()
    if output.exists() or output.is_relative_to(RUN):
        raise ValueError("Output must be a new directory outside the frozen run")
    started = datetime.now(timezone.utc)
    assert frozen.sha(RUN / "artifact_manifest.json") == MANIFEST_SHA
    assert frozen.sha(frozen.__file__) == VERIFIER_SHA
    verification = frozen.verify(RUN)
    assert (
        verification["verification_passed"] and not verification["effectiveness_passed"]
    )
    spec_path = RUN / "sources/config/ootang_probability_pinn.v2_3.json"
    spec = json.loads(spec_path.read_text())
    h, n = spec["fit_days"], spec["end_days"]
    assert (h, n, spec["warmup_days"], spec["seeds"]) == (792, 1168, 30, [0, 1, 2])
    scorer_path = RUN / "sources/code/physics_guided/probability.py"
    probability = types.ModuleType("frozen_cross_scorer")
    exec(
        compile(scorer_path.read_text(), str(scorer_path), "exec"), probability.__dict__
    )
    reference = frozen.read_npz(RUN / "reference.npz")
    selected = frozen.read_npz(RUN / "selected_predictions.npz")
    old_path = ROOT / spec["source_run"] / "development/M1/selected_predictions.npz"
    old = frozen.read_npz(old_path)
    observation_path = ROOT / spec["input"]
    frame = pd.read_csv(observation_path, nrows=n)
    dates = pd.to_datetime(frame.Date).dt.strftime("%Y-%m-%d").to_numpy()
    assert np.array_equal(dates, reference["dates"])
    observed = frame[[point + "/mm" for point in POINTS]].to_numpy(float)
    mean_sources = {
        "bplus": np.broadcast_to(reference["mean"], (3, n, 4)),
        "neural": selected["means"],
        "replay": selected["reference_means"],
    }
    scale_sources = {"e0": old["sigmas"], "v23": selected["sigmas"]}
    np.testing.assert_allclose(
        old["means"][:, 30:], mean_sources["bplus"][:, 30:], atol=1e-8, rtol=0
    )
    table = score_matrix(mean_sources, scale_sources, observed, h, probability)
    prior = pd.read_csv(RUN / "metrics.csv")
    maximum = 0.0
    for key, model in ORIGINALS.items():
        a = table[(table.mean_source == key[0]) & (table.scale_source == key[1])]
        b = prior[prior.model == model]
        joined = a.merge(
            b,
            on=["phase", "station", "metric"],
            validate="one_to_one",
            suffixes=("_new", "_old"),
        )
        assert len(a) == len(b) == len(joined) == 122
        maximum = max(maximum, float(np.abs(joined.value_new - joined.value_old).max()))
    assert maximum < 1e-8
    delta_table = contrasts(table)
    difference_table = state_differences(reference, selected, h)
    assert (
        len(table) == 732 and len(delta_table) == 854 and len(difference_table) == 168
    )
    # Recheck input identity after scoring; no hash is rewritten or accepted anew.
    manifest = json.loads((RUN / "artifact_manifest.json").read_text())["files"]
    for name, entry in manifest.items():
        assert frozen.sha(RUN / name) == entry["sha256"]
    for name, digest in spec["source_sha256"].items():
        source = RUN / "sources" / name
        assert frozen.sha(source if source.exists() else ROOT / name) == digest
    metadata = dict(
        source_commit=SOURCE_COMMIT,
        started_at=started.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        protocol=dict(
            points=POINTS,
            domains=DOMAINS,
            seeds=[0, 1, 2],
            weights=[1 / 3] * 3,
            mean_sources=MEANS,
            scale_sources=SCALES,
            pairing="mean seed k with scale seed k; no averaging or permutation before constructing the mixture",
            phases={
                name: dict(
                    start=dates[mask.start],
                    end=dates[mask.stop - 1],
                    days=mask.stop - mask.start,
                )
                for name, mask in phases(n, h).items()
            },
            interpretation="conditional frozen-distribution comparisons, not additive causal effects",
            m0_probability_metrics="not applicable; bplus/e0 is the original v1.1-e0 distribution",
            selection="none; original PINN remains the neural/v23 combination",
        ),
        original_verification=verification,
        matched_original_score_rows=366,
        original_score_max_difference=maximum,
        score_rows=len(table),
        contrast_rows=len(delta_table),
        state_difference_rows=len(difference_table),
        new_optimizer_updates=0,
        new_native_solver_calls=0,
        final_293day_started=False,
        v23_status="failed and stopped; cross scores do not replace its primary output",
        inputs={
            str(p.relative_to(ROOT)): dict(sha256=frozen.sha(p), bytes=p.stat().st_size)
            for p in (
                RUN / "artifact_manifest.json",
                RUN / "selected_predictions.npz",
                RUN / "reference.npz",
                scorer_path,
                spec_path,
                old_path,
                observation_path,
                Path(frozen.__file__),
            )
        },
        analysis_script_sha256=frozen.sha(__file__),
    )
    output.mkdir(parents=True, exist_ok=False)
    for name, data in (
        ("scores.csv", table),
        ("contrasts.csv", delta_table),
        ("state_differences.csv", difference_table),
    ):
        data.to_csv(output / name, index=False)
    metadata["outputs"] = {
        p.name: dict(sha256=frozen.sha(p), bytes=p.stat().st_size)
        for p in sorted(output.glob("*.csv"))
    }
    with (output / "audit.json").open("x") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "source_commit",
                    "score_rows",
                    "contrast_rows",
                    "state_difference_rows",
                    "original_score_max_difference",
                    "v23_status",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
