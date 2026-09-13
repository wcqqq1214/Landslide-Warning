"""Independent batch information, SVD, scoring and frozen-source checks."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from verify_ootang_online import arrays, sha, array_sha
from verify_ootang_rolling import independent_metrics
from verify_ootang_empirical import decision_checks

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def verify(run, out):
    files = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, expected in files.items():
        if sha(run / name) != expected:
            raise ValueError("Changed artifact")
    sources = json.loads((run / "sources.json").read_text())["files"]
    for name, expected in sources.items():
        path = run / "sources" / name
        if not path.exists():
            path = ROOT / name
        if sha(path) != expected:
            raise ValueError("Changed source: " + name)
    config = next((run / "sources/config").glob("ootang_rolling_probability.*.json"))
    spec = json.loads(config.read_text())
    status = json.loads((run / "status.json").read_text())
    if status["state"] != "completed" or datetime.fromisoformat(
        status["finished_utc"]
    ) > datetime.fromisoformat(spec["candidate_deadline_utc"]):
        raise ValueError("Incomplete or late candidate")
    labels = pd.read_csv(ROOT / spec["data"])[[p + "/mm" for p in POINTS]].to_numpy(
        float
    )
    maximum = 0.0

    def close(actual, wanted):
        nonlocal maximum
        np.testing.assert_allclose(
            actual, wanted, atol=1e-8, rtol=5e-11, equal_nan=True
        )
        maximum = max(maximum, float(np.nanmax(np.abs(np.asarray(actual) - wanted))))

    count = svds = cells = locks = 0
    all_records = {}
    for phase, (start, end) in spec["stages"].items():
        src = ROOT / spec["sources"][phase]["current_run"] / phase
        units = np.asarray(
            json.loads((src / "normalization.json").read_text())["response_scales"]
        )
        records = {}
        for path in (run / phase).glob("*.npz"):
            z = arrays(path)
            if {"mean", "sigma", "origins"} <= z.keys():
                records[path.stem] = z
        for name, old in spec["variance_sources"].items():
            old_pred = arrays(src / (old + ".npz"))
            pred = records[name]
            for key in old_pred:
                if key != "sigma":
                    np.testing.assert_array_equal(pred[key], old_pred[key])
            np.testing.assert_array_equal(pred["core_sigma"], old_pred["sigma"])
            learning = arrays(src / (old + "_learning.npz"))
            x, available = learning["features"], learning["available"]
            n, h, _, dimensions = x.shape
            matrices = arrays(run / phase / (name + "_information.npz"))["matrix"]
            for i in range(n):
                for k in range(h):
                    if i - k - 1 >= 0 and available[i - k - 1, k]:
                        count += 4
                    if i + k >= n:
                        continue
                    past = np.flatnonzero(available[: max(0, i - k), k])
                    for p in range(4):
                        design = np.vstack([np.eye(dimensions), x[past, k, p]])
                        matrix = np.einsum("ni,nj->ij", design, design, optimize=False)
                        close(matrices[i, k, p], matrix)
                        white = np.linalg.solve(np.linalg.cholesky(matrix), x[i, k, p])
                        value = units[k, p] ** 2 * np.sum(white**2)
                        close(pred["information_variance"][i, k, p], value)
                        close(
                            pred["sigma"][i, k, p],
                            np.hypot(old_pred["sigma"][i, k, p], np.sqrt(value)),
                        )
                        if i % 29 == 0 and k in (0, 14, 29):
                            _, s, vt = np.linalg.svd(design, full_matrices=False)
                            close(
                                value,
                                units[k, p] ** 2 * np.sum((vt @ x[i, k, p] / s) ** 2),
                            )
                            svds += 1
            np.testing.assert_array_equal(pred["sigma"][0], old_pred["sigma"][0])
        rows = []
        for name, pred in records.items():
            if name not in spec["variance_sources"]:
                old = arrays(src / (name + ".npz"))
                for key in old:
                    np.testing.assert_array_equal(pred[key], old[key])
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            for k in range(30):
                n = end - start - k
                y = labels[start + k : end]
                value = independent_metrics(
                    y, pred["mean"][:n, k], pred["sigma"][:n, k]
                )
                if name == "B_RAW":
                    value = {
                        key: val for key, val in value.items() if key in ("mae", "rmse")
                    }
                rows.extend(
                    dict(
                        model=name,
                        horizon=k + 1,
                        point=p,
                        n=n,
                        **{key: float(val[j]) for key, val in value.items()},
                    )
                    for j, p in enumerate(POINTS)
                )
        metrics = pd.DataFrame(rows)
        summary = []
        for (name, h), group in metrics.groupby(["model", "horizon"], sort=False):
            summary.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(group.n.iloc[0]),
                    pooled_rmse=float(np.sqrt(np.mean(group.rmse**2))),
                    **{
                        k: float(group[k].mean())
                        for k in metrics
                        if k not in ("model", "horizon", "point", "n")
                    },
                )
            )
        summary = pd.DataFrame(summary)
        for frame, filename, keys in [
            (metrics, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ]:
            wanted = frame.set_index(keys).sort_index()
            actual = pd.read_csv(run / phase / filename).set_index(keys).sort_index()
            close(actual[wanted.columns].to_numpy(), wanted.to_numpy())
            cells += wanted.size
        checks = decision_checks(metrics, summary, spec["candidate"], spec)
        decision = json.loads((run / phase / "decision.json").read_text())
        if decision["checks"] != checks or decision["passed"] != all(checks.values()):
            raise ValueError("Changed decision")
        all_records[phase] = records
    events = [
        json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()
    ]
    for event in events:
        if event["kind"] == "forecast_locked":
            phase, origin = event["phase"], event["origin"]
            start, end = spec["stages"][phase]
            i = origin - start
            valid = min(30, end - origin)
            if event["latest_mature_target"] != origin - 1:
                raise ValueError("Future supervision in lock")
            for name, digest in event["predictions"].items():
                if (
                    array_sha(
                        np.stack(
                            [
                                all_records[phase][name][k][i, :valid]
                                for k in ("mean", "sigma")
                            ]
                        )
                    )
                    != digest
                ):
                    raise ValueError("Changed prediction lock")
            locks += 1
    if (
        locks != 669
        or count != spec["expected_covariance_point_updates"]
        or events[-1]["kind"] != "all_distributions_locked_before_scoring"
    ):
        raise ValueError("Incomplete causal record")
    out.mkdir(parents=True, exist_ok=False)
    for name in [
        Path(__file__).name,
        "verify_ootang_online.py",
        "verify_ootang_rolling.py",
        "verify_ootang_empirical.py",
    ]:
        shutil.copyfile(Path(__file__).with_name(name), out / name)
    result = dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        artifact_count=len(files),
        source_count=len(sources),
        covariance_point_updates=count,
        svd_checks=svds,
        score_cells=cells,
        locks=locks,
        maximum_difference=maximum,
        new_training=0,
        new_physics=0,
        run_manifest_sha256=sha(run / "artifact_manifest.json"),
    )
    (out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    verify(Path(args.run), Path(args.out))
