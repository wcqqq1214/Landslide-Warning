"""Independently check paired statistics from exported daily forecast rows."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from statistics import NormalDist

import numpy as np
import pandas as pd
from scipy.special import erf

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sample_weights(n, boot):
    """Count each sampled day's multiplicity without gathering loss tensors."""
    rng = np.random.default_rng(boot["seed"])
    length, replicates = boot["block_length"], boot["replicates"]
    weights = np.zeros((replicates + 1, n))
    weights[0] = 1 / n
    for b in range(replicates):
        starts = rng.integers(0, n, size=(n + length - 1) // length)
        remaining = n
        for start in starts:
            count = min(length, remaining)
            np.add.at(weights[b + 1], (start + np.arange(count)) % n, 1 / n)
            remaining -= count
    np.testing.assert_allclose(weights.sum(axis=1), 1, atol=1e-13, rtol=0)
    return weights


def calculate(y, mu, sd, weights):
    z = (y - mu) / sd
    q = NormalDist().inv_cdf(0.95)
    lower, upper = mu - q * sd, mu + q * sd
    cells = {
        "mae": np.abs(mu - y),
        "squared": (mu - y) ** 2,
        "crps": sd
        * (
            z * erf(z / np.sqrt(2))
            + np.sqrt(2 / np.pi) * np.exp(-(z**2) / 2)
            - 1 / np.sqrt(np.pi)
        ),
        "coverage90": ((lower <= y) & (y <= upper)).astype(float),
        "width90": 2 * q * sd,
        "interval_score90": 2 * q * sd
        + 20 * (np.maximum(lower - y, 0) + np.maximum(y - upper, 0)),
    }
    by_point = {
        key: np.einsum("bn,np->bp", weights, value, optimize=False)
        for key, value in cells.items()
    }
    result = {
        key: value.mean(axis=1) for key, value in by_point.items() if key != "squared"
    }
    result["rmse"] = np.sqrt(by_point["squared"]).mean(axis=1)
    result["pooled_rmse"] = np.sqrt(by_point["squared"].mean(axis=1))
    return result


def main(args):
    cfg_path, run = Path(args.config), Path(args.analysis)
    cfg = json.loads(cfg_path.read_text())
    if datetime.now(timezone.utc) >= datetime.fromisoformat(cfg["deadline_utc"]):
        raise TimeoutError("Original analysis budget exhausted")
    meta = json.loads((run / "analysis.json").read_text())
    if meta["config_sha256"] != sha(cfg_path):
        raise ValueError("Changed analysis configuration")
    artifacts = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, expected in artifacts.items():
        if sha(run / name) != expected:
            raise ValueError("Changed analysis artifact: " + name)
    if sha(ROOT / cfg["data"]) != cfg["data_sha256"]:
        raise ValueError("Changed observations")
    prefix = cfg["focus_prefix"]
    spec = json.loads((ROOT / cfg["focus_config"]).read_text())
    if sha(ROOT / cfg["focus_config"]) != cfg["focus_config_sha256"]:
        raise ValueError("Changed model configuration")
    daily = pd.read_csv(run / (prefix + "_daily_h30.csv"))
    paired = pd.read_csv(run / (prefix + "_paired_bootstrap.csv"))
    sparse = pd.read_csv(run / (prefix + "_nonoverlap.csv"))
    boot, maximum, checked = cfg["bootstrap"], 0.0, 0

    def compare(actual, expected):
        nonlocal maximum
        np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=5e-11)
        maximum = max(maximum, float(np.max(np.abs(np.asarray(actual) - expected))))

    for phase, (start, end) in spec["stages"].items():
        n = end - start - 29
        weights = sample_weights(n, boot)
        sparse_weight = np.zeros(n)
        sparse_weight[::30] = 1 / len(sparse_weight[::30])
        weights = np.vstack((weights, sparse_weight))
        raw = pd.read_csv(ROOT / cfg["data"], nrows=end)
        origins = np.arange(start, end - 29)
        y = raw[[p + "/mm" for p in POINTS]].to_numpy()[origins + 29]
        dates = pd.to_datetime(raw.Date).dt.strftime("%Y-%m-%d").to_numpy()
        scores = {}
        for name in cfg["focus_models"]:
            df = daily[daily.phase.eq(phase) & daily.model.eq(name)]
            if len(df) != 4 * n or df.duplicated(["origin", "point"]).any():
                raise ValueError("Missing or duplicate daily observations")
            arrays = {}
            for column in ("observed", "mean", "sigma", "lower90", "upper90"):
                arrays[column] = (
                    df.pivot(index="origin", columns="point", values=column)
                    .loc[origins, list(POINTS)]
                    .to_numpy()
                )
            compare(arrays["observed"], y)
            for point in POINTS:
                point_rows = df[df.point.eq(point)].sort_values("origin")
                np.testing.assert_array_equal(
                    point_rows.target_date, dates[origins + 29]
                )
            with np.load(ROOT / cfg["focus_run"] / phase / (name + ".npz")) as source:
                compare(arrays["mean"], source["mean"][:n, 29])
                compare(arrays["sigma"], source["sigma"][:n, 29])
            q = NormalDist().inv_cdf(0.95)
            compare(arrays["lower90"], arrays["mean"] - q * arrays["sigma"])
            compare(arrays["upper90"], arrays["mean"] + q * arrays["sigma"])
            scores[name] = calculate(y, arrays["mean"], arrays["sigma"], weights)
            row = sparse[sparse.phase.eq(phase) & sparse.model.eq(name)].iloc[0]
            for metric, values in scores[name].items():
                compare(values[-1], row[metric])
        phase_pairs = paired[paired.phase.eq(phase)]
        expected_keys = {
            (a, b, metric)
            for a, b in cfg["comparison_pairs"]
            for metric in scores[cfg["focus_models"][0]]
        }
        actual_keys = set(
            zip(phase_pairs.candidate, phase_pairs.reference, phase_pairs.metric)
        )
        if actual_keys != expected_keys or len(phase_pairs) != len(expected_keys):
            raise ValueError("Comparison registry changed")
        for row in phase_pairs.itertuples(index=False):
            for key in boot:
                if getattr(row, key) != boot[key]:
                    raise ValueError("Changed bootstrap protocol")
            delta = (
                scores[row.candidate][row.metric] - scores[row.reference][row.metric]
            )
            tail = (1 - boot["confidence"]) / 2
            limits = np.quantile(delta[1:-1], [tail, 1 - tail])
            compare([delta[0], *limits], [row.difference, row.lower, row.upper])
            checked += 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, out / Path(__file__).name)
    result = dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        artifact_count=len(artifacts),
        paired_intervals=checked,
        daily_rows=len(daily),
        maximum_difference=maximum,
        analysis_manifest_sha256=sha(run / "artifact_manifest.json"),
        verifier_sha256=sha(__file__),
        new_training=0,
        new_physics=0,
        method="exported rows, counted block weights, erf and NormalDist",
    )
    (out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--out", required=True)
    main(parser.parse_args())
