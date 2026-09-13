"""Independently reconstruct frozen-mean calibration, scores and issue order."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from verify_ootang_online import arrays, sha, array_sha, verify_calibration
from verify_ootang_empirical import decision_checks
from verify_ootang_rolling import independent_metrics

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DEPENDENCIES = (
    "verify_ootang_online.py",
    "verify_ootang_empirical.py",
    "verify_ootang_rolling.py",
)


def verify(run):
    files = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, expected in files.items():
        if sha(run / name) != expected:
            raise ValueError("Changed artifact " + name)
    sources = json.loads((run / "sources.json").read_text())["files"]
    for name, expected in sources.items():
        source = run / "sources" / name
        if not source.exists():
            source = ROOT / name
        if sha(source) != expected:
            raise ValueError("Changed source " + name)
    configs = list((run / "sources/config").glob("ootang_rolling_probability.*.json"))
    if len(configs) != 1:
        raise ValueError("Ambiguous frozen config")
    spec = json.loads(configs[0].read_text())
    status = json.loads((run / "status.json").read_text())
    deadline = min(
        datetime.fromisoformat(spec["deadline_utc"]),
        datetime.fromisoformat(spec["candidate_deadline_utc"]),
    )
    if (
        status["state"] != "completed"
        or status["exit_code"] != 0
        or datetime.fromisoformat(status["finished_utc"]) > deadline
    ):
        raise ValueError("Incomplete or late run")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Unverified frozen means")
    if (
        not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
        or spec["uncertainty"] != "own_issued_error_calibration"
    ):
        raise ValueError("Changed exposure or calibration task")
    labels = pd.read_csv(ROOT / spec["data"])[[p + "/mm" for p in POINTS]].to_numpy(
        float
    )
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Changed observations")
    decisions = json.loads((run / "decision.json").read_text())
    all_records = {}
    calibration_max = score_max = 0.0
    score_cells = calibration_updates = 0
    for phase, (start, end) in spec["stages"].items():
        H, N = spec["horizons"], end - start
        ref = spec["sources"][phase]
        source = ROOT / ref["current_run"] / ref["current_phase"]
        if (
            sha(source.parent / "artifact_manifest.json")
            != ref["current_manifest_sha256"]
        ):
            raise ValueError("Changed frozen mean source")
        records = {p.stem: arrays(p) for p in (run / phase).glob("*.npz")}
        all_records[phase] = records
        feedback = json.loads((run / phase / "feedback_state.json").read_text())
        if set(feedback) != set(spec["calibration_sources"]):
            raise ValueError("Changed calibrated model set")
        for name, pred in records.items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            np.testing.assert_array_equal(pred["teacher_prefixes"], np.full(N, start))
            if name not in spec["calibration_sources"]:
                original = arrays(source / (name + ".npz"))
                if pred.keys() != original.keys():
                    raise ValueError("Changed original fields")
                for key in original:
                    np.testing.assert_array_equal(pred[key], original[key])
                continue
            original = arrays(source / (spec["calibration_sources"][name] + ".npz"))
            for key in ("origins", "teacher_prefixes", "mean", "raw_sigma"):
                np.testing.assert_array_equal(pred[key], original[key])
            calibration_max = max(
                calibration_max,
                verify_calibration(
                    pred, labels, start, end, spec["probability"], feedback[name]
                ),
            )
            calibration_updates += 4 * sum(N - k for k in range(H))
            if name == spec["identity_control"]:
                for key in ("sigma", "calibration_factor", "feedback_log_scale"):
                    np.testing.assert_array_equal(pred[key], original[key])
        rows = []
        for name, pred in records.items():
            for k in range(H):
                n = N - k
                y, mean = labels[start + k : end], pred["mean"][:n, k]
                if name == "B_RAW":
                    values = dict(
                        mae=np.mean(abs(y - mean), axis=0),
                        rmse=np.sqrt(np.mean((y - mean) ** 2, axis=0)),
                    )
                else:
                    values = independent_metrics(y, mean, pred["sigma"][:n, k])
                rows.extend(
                    dict(
                        model=name,
                        horizon=k + 1,
                        point=p,
                        n=n,
                        **{key: float(value[j]) for key, value in values.items()},
                    )
                    for j, p in enumerate(POINTS)
                )
        computed = pd.DataFrame(rows)
        summary = []
        for (name, h), group in computed.groupby(["model", "horizon"], sort=False):
            summary.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(group.n.iloc[0]),
                    pooled_rmse=float(np.sqrt(np.mean(group.rmse**2))),
                    **{
                        k: float(group[k].mean())
                        for k in computed
                        if k not in ("model", "horizon", "point", "n")
                    },
                )
            )
        summary = pd.DataFrame(summary)
        for frame, filename, keys in [
            (computed, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ]:
            wanted = frame.set_index(keys).sort_index()
            actual = pd.read_csv(run / phase / filename).set_index(keys).sort_index()
            np.testing.assert_allclose(
                actual[wanted.columns].to_numpy(),
                wanted.to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            score_max = max(
                score_max,
                float(
                    np.nanmax(
                        abs(actual[wanted.columns].to_numpy() - wanted.to_numpy())
                    )
                ),
            )
            score_cells += wanted.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        decision = json.loads((run / phase / "decision.json").read_text())
        expected_updates = (
            4 * len(spec["calibration_sources"]) * sum(N - k for k in range(H))
        )
        if (
            checks != decision["checks"]
            or decision["passed"] != all(checks.values())
            or decision["passed_count"] != sum(checks.values())
            or decision != decisions["phases"][phase]
            or decision["calibration_point_updates"] != expected_updates
        ):
            raise ValueError("Changed effects or phase count")
    chain, pending, locks = "", None, 0
    next_origins = {}
    for raw in (run / "events.jsonl").read_text().splitlines():
        event = json.loads(raw)
        if event["previous_sha256"] != chain:
            raise ValueError("Broken issue chain")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = event["phase"]
        start, end = spec["stages"][phase]
        if event["kind"] == "phase_started":
            next_origins[phase] = start
        elif event["kind"] == "forecast_locked":
            n = event["origin"]
            if (
                pending is not None
                or n != next_origins[phase]
                or event["history_sha256"] != array_sha(labels[:n])
            ):
                raise ValueError("Forecast preceded known history")
            i, valid = n - start, min(spec["horizons"], end - n)
            expected = {
                name: array_sha(
                    np.stack(
                        [pred[k][i, :valid] for k in ("mean", "sigma", "raw_sigma")]
                    )
                )
                for name, pred in all_records[phase].items()
            }
            if event["predictions"] != expected:
                raise ValueError("Changed issued distribution")
            pending = (phase, n)
            locks += 1
        elif event["kind"] == "observation_released":
            if pending != (phase, event["index"]) or event["value_sha256"] != array_sha(
                labels[event["index"]]
            ):
                raise ValueError("Observation released before lock")
            pending = None
            next_origins[phase] += 1
        elif event["kind"] == "phase_completed":
            if pending is not None or next_origins[phase] != end:
                raise ValueError("Incomplete release phase")
        else:
            raise ValueError("Unknown event")
    if (
        locks != sum(end - start for start, end in spec["stages"].values())
        or pending is not None
    ):
        raise ValueError("Incomplete issue locks")
    if (
        calibration_updates != spec["expected_calibration_point_updates"]
        or calibration_updates != decisions["calibration_point_updates"]
    ):
        raise ValueError("Total calibration count differs")
    if any(
        decisions[k] != 0
        for k in (
            "new_mean_fits",
            "new_scale_optimizations",
            "neural_updates",
            "physical_calls",
        )
    ):
        raise ValueError("Unexpected fitting")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(files),
        sources_checked=len(sources),
        forecast_locks=locks,
        calibration_point_updates=calibration_updates,
        score_cells=score_cells,
        maximum_calibration_difference_mm=calibration_max,
        maximum_score_difference_mm=score_max,
        frozen_means_and_raw_scales_exact=True,
        old_distributions_exact=True,
        c8_identity_exact=True,
        independent_transfer=False,
        new_training=0,
        new_physics=0,
        verifier_sha256=sha(__file__),
        dependency_sha256={
            name: sha(Path(__file__).with_name(name)) for name in DEPENDENCIES
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = verify(args.run)
    args.out.mkdir(parents=True)
    (args.out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for name in (Path(__file__).name, *DEPENDENCIES):
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(result, indent=2))
