"""Read-only reconstruction of mature error inputs, ridge heads and scores."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from verify_ootang_empirical import decision_checks
from verify_ootang_rolling import independent_metrics

ROOT = Path(__file__).resolve().parents[1]
POINTS = ["ATU1", "ATU5", "MJ3", "MJ1"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def arrays(p):
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k].copy() for k in z.files}


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, wanted in manifest.items():
        if sha(run / name) != wanted:
            raise ValueError("Changed artifact " + name)
    sources = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in sources.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Changed source " + name)
    configs = list((run / "sources/config").glob("ootang_rolling_probability.*.json"))
    if len(configs) != 1:
        raise ValueError("Ambiguous source config")
    spec = json.loads(configs[0].read_text())
    status = json.loads((run / "status.json").read_text())
    if (
        status["state"] != "completed"
        or status["exit_code"] != 0
        or datetime.fromisoformat(status["finished_utc"])
        > datetime.fromisoformat(spec["deadline_utc"])
    ):
        raise ValueError("Incomplete or late run")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Unverified core")
    if (
        not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
        or spec["uncertainty"] != "frozen_core_sigma"
    ):
        raise ValueError("Protocol changed")
    labels = pd.read_csv(ROOT / spec["data"])[[p + "/mm" for p in POINTS]].to_numpy(
        float
    )
    decisions = json.loads((run / "decision.json").read_text())
    maximum = dict(features=0.0, beta=0.0, svd=0.0, mean_mm=0.0, scores_mm=0.0)
    totals = dict(point_updates=0, score_cells=0, svd_checks=0)
    records, learning = {}, {}
    for phase, (start, end) in spec["stages"].items():
        N, H = end - start, spec["horizons"]
        ref = spec["sources"][phase]
        source = ROOT / ref["current_run"] / ref["current_phase"]
        if (
            sha(source.parent / "artifact_manifest.json")
            != ref["current_manifest_sha256"]
        ):
            raise ValueError("Core source manifest changed")
        current = {}
        for p in (run / phase).glob("*.npz"):
            v = arrays(p)
            if {"origins", "mean", "sigma", "raw_sigma"} <= v.keys():
                current[p.stem] = v
        records[phase] = current
        learning[phase] = {}
        states = json.loads((run / phase / "learning_state.json").read_text())
        core = arrays(source / (spec["core_model"] + ".npz"))
        for name, pred in current.items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            np.testing.assert_array_equal(pred["teacher_prefixes"], np.full(N, start))
            if name not in spec["innovation_references"]:
                old = arrays(source / (name + ".npz"))
                if old.keys() != pred.keys():
                    raise ValueError("Changed original fields")
                for key in old:
                    np.testing.assert_array_equal(old[key], pred[key])
                continue
            for key in ("sigma", "raw_sigma"):
                np.testing.assert_array_equal(pred[key], core[key])
            for new, old in [
                ("core_mean", "mean"),
                ("core_calibration_factor", "calibration_factor"),
                ("core_feedback_log_scale", "feedback_log_scale"),
            ]:
                np.testing.assert_array_equal(pred[new], core[old])
            log = arrays(run / phase / (name + "_learning.npz"))
            learning[phase][name] = log
            refs = [
                arrays(source / (s + ".npz"))
                for s in spec["innovation_references"][name]
            ]
            D = len(refs)
            state = states[name]
            expected_features = np.full_like(log["features"], np.nan)
            expected_available = np.zeros_like(log["available"])
            reconstructed_beta = np.zeros_like(log["beta"])
            for k in range(H):
                h = k + 1
                count = N - k
                x = np.zeros((count, 4, D))
                valid_origins = np.arange(h, count)
                for d, reference in enumerate(refs):
                    past = valid_origins - h
                    x[valid_origins, :, d] = (
                        labels[start + valid_origins - 1] - reference["mean"][past, k]
                    ) / reference["sigma"][past, k]
                expected_features[:count, k] = x
                expected_available[h:count, k] = True
                response = (labels[start + k : end] - core["mean"][:count, k]) / core[
                    "sigma"
                ][:count, k]
                counts = np.maximum(h, np.r_[np.arange(N) - k, count])
                for i, stop in enumerate(counts):
                    xx = x[h:stop]
                    yy = response[h:stop]
                    gram = spec["innovation_alpha"] * np.eye(D) + np.einsum(
                        "npi,npj->pij", xx, xx, optimize=False
                    )
                    rhs = np.einsum("npi,np->pi", xx, yy, optimize=False)
                    beta = np.linalg.solve(gram, rhs[..., None])[..., 0]
                    if i < N:
                        reconstructed_beta[i, k] = beta
                    else:
                        for key, wanted in [
                            ("gram", gram),
                            ("rhs", rhs),
                            ("beta", beta),
                        ]:
                            np.testing.assert_allclose(
                                state[key][k], wanted, rtol=5e-11, atol=1e-8
                            )
                    if i in (0, N // 2, N - 1):
                        for p in range(4):
                            aug = np.vstack(
                                [
                                    xx[:, p],
                                    np.sqrt(spec["innovation_alpha"]) * np.eye(D),
                                ]
                            )
                            target = np.r_[yy[:, p], np.zeros(D)]
                            svd = np.linalg.lstsq(aug, target, rcond=None)[0]
                            np.testing.assert_allclose(
                                svd, beta[p], rtol=5e-11, atol=1e-8
                            )
                            maximum["svd"] = max(
                                maximum["svd"], float(abs(svd - beta[p]).max())
                            )
                            totals["svd_checks"] += 1
                if (
                    state["updates"][k] != count - h
                    or state["last_target"][k] != end - 1
                ):
                    raise ValueError("Mature target counts changed")
                totals["point_updates"] += (count - h) * 4
            np.testing.assert_allclose(
                log["features"], expected_features, rtol=0, atol=0, equal_nan=True
            )
            np.testing.assert_array_equal(log["available"], expected_available)
            np.testing.assert_allclose(
                log["beta"], reconstructed_beta, rtol=5e-11, atol=1e-8
            )
            maximum["beta"] = max(
                maximum["beta"], float(abs(log["beta"] - reconstructed_beta).max())
            )
            mean = core["mean"] + core["sigma"] * np.einsum(
                "nhpd,nhpd->nhp", expected_features, reconstructed_beta, optimize=False
            )
            np.testing.assert_allclose(
                pred["mean"], mean, rtol=5e-11, atol=1e-8, equal_nan=True
            )
            maximum["mean_mm"] = max(
                maximum["mean_mm"], float(np.nanmax(abs(mean - pred["mean"])))
            )
        rows = []
        for name, pred in current.items():
            for k in range(H):
                count = N - k
                y = labels[start + k : end]
                mu = pred["mean"][:count, k]
                if name == "B_RAW":
                    values = dict(
                        mae=np.mean(abs(y - mu), axis=0),
                        rmse=np.sqrt(np.mean((y - mu) ** 2, axis=0)),
                    )
                else:
                    values = independent_metrics(y, mu, pred["sigma"][:count, k])
                rows.extend(
                    dict(
                        model=name,
                        horizon=k + 1,
                        point=p,
                        n=count,
                        **{key: float(v[j]) for key, v in values.items()},
                    )
                    for j, p in enumerate(POINTS)
                )
        computed = pd.DataFrame(rows)
        summary = []
        for (name, h), g in computed.groupby(["model", "horizon"], sort=False):
            summary.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(g.n.iloc[0]),
                    pooled_rmse=float(np.sqrt(np.mean(g.rmse**2))),
                    **{
                        k: float(g[k].mean())
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
            actual = pd.read_csv(run / phase / filename).set_index(keys).sort_index()
            want = frame.set_index(keys).sort_index()
            np.testing.assert_allclose(
                actual[want.columns].to_numpy(),
                want.to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            maximum["scores_mm"] = max(
                maximum["scores_mm"],
                float(
                    np.nanmax(abs(actual[want.columns].to_numpy() - want.to_numpy()))
                ),
            )
            totals["score_cells"] += want.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        dec = json.loads((run / phase / "decision.json").read_text())
        if (
            checks != dec["checks"]
            or dec["passed"] != all(checks.values())
            or dec["passed_count"] != sum(checks.values())
            or dec != decisions["phases"][phase]
        ):
            raise ValueError("Effect decision differs")
        expected_solves = (
            len(spec["innovation_references"])
            * 4
            * sum(N - 2 * h + 1 for h in range(1, H + 1))
        )
        if dec["point_solves"] != expected_solves:
            raise ValueError("Phase solve count differs")
    chain, pending, locks = "", None, 0
    next_origin = {}
    for raw in (run / "events.jsonl").read_text().splitlines():
        event = json.loads(raw)
        if event["previous_sha256"] != chain:
            raise ValueError("Broken event chain")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = event["phase"]
        start, end = spec["stages"][phase]
        if event["kind"] == "phase_started":
            if event["inherited_core"] != spec["core_model"]:
                raise ValueError("Wrong core identity")
            next_origin[phase] = start
        elif event["kind"] == "forecast_locked":
            n = event["origin"]
            i = n - start
            h = min(spec["horizons"], end - n)
            if (
                pending is not None
                or next_origin[phase] != n
                or event["history_sha256"] != array_sha(labels[:n])
            ):
                raise ValueError("Forecast crossed observed history")
            expected = {
                name: array_sha(
                    np.stack([p[k][i, :h] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, p in records[phase].items()
            }
            inputs = {
                name: dict(
                    features=array_sha(log["features"][i, :h]),
                    available=array_sha(log["available"][i, :h]),
                    beta=array_sha(log["beta"][i]),
                )
                for name, log in learning[phase].items()
            }
            if event["predictions"] != expected or event["inputs"] != inputs:
                raise ValueError("Issued input or prediction changed")
            pending = (phase, n)
            locks += 1
        elif event["kind"] == "observation_released":
            if pending != (phase, event["index"]) or event["value_sha256"] != array_sha(
                labels[event["index"]]
            ):
                raise ValueError("Observation released before lock")
            pending = None
            next_origin[phase] += 1
        elif event["kind"] == "phase_completed":
            if pending is not None or next_origin[phase] != end:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unknown event")
    if locks != sum(e - s for s, e in spec["stages"].values()) or pending is not None:
        raise ValueError("Incomplete locks")
    if (
        totals["point_updates"] != spec["expected_point_solves"]
        or totals["point_updates"] != decisions["point_solves"]
    ):
        raise ValueError("Total solve count differs")
    if any(
        decisions[k] != 0
        for k in (
            "new_initial_fits",
            "new_neural_updates",
            "new_scale_optimizations",
            "physical_calls",
        )
    ):
        raise ValueError("Additional computation occurred")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(sources),
        forecast_locks=locks,
        checks=totals,
        maximum_differences=maximum,
        old_distributions_and_core_sigma_exactly_unchanged=True,
        post_exposure=True,
        independent_transfer=False,
        new_training_updates=0,
        physical_calls=0,
        verifier_sha256=sha(__file__),
        dependency_sha256={
            name: sha(Path(__file__).with_name(name))
            for name in ("verify_ootang_empirical.py", "verify_ootang_rolling.py")
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = verify(args.run)
    args.out.mkdir(parents=True)
    (args.out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for name in (
        Path(__file__).name,
        "verify_ootang_empirical.py",
        "verify_ootang_rolling.py",
    ):
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
