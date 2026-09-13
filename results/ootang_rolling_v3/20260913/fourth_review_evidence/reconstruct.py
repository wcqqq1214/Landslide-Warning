"""Describe frozen h30 errors; this does not fit or choose a new model."""

import hashlib
import json
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
POINTS = ["ATU1", "ATU5", "MJ3", "MJ1"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


data = ROOT / "data/monitoring_data.csv"
run = ROOT / "results/ootang_rolling_v3/20260913/c8_online"
verified = ROOT / "results/ootang_rolling_v3/20260913/c8_verification/verification.json"
y = pd.read_csv(data)[[p + "/mm" for p in POINTS]].to_numpy()
sources = {
    str(p.relative_to(ROOT)): sha(p)
    for p in [data, verified, run / "artifact_manifest.json"]
}
rows = []
for phase in ["development", "later_exploratory"]:
    for name in ["C8_ONLINE_FULL", "C8_ONLINE_DATA"]:
        p = run / phase / (name + ".npz")
        sources[str(p.relative_to(ROOT))] = sha(p)
        with np.load(p) as z:
            start, n = int(z["origins"][0]), len(z["origins"]) - 29
            e = y[start + 29 : start + 29 + n] - z["mean"][:n, 29]
            radius = NormalDist().inv_cdf(0.95) * z["sigma"][:n, 29]
            for j, point in enumerate(POINTS):
                a = e[:, j]
                rows.append(
                    dict(
                        phase=phase,
                        model=name,
                        point=point,
                        n=n,
                        bias_mm=float(a.mean()),
                        median_error_mm=float(np.median(a)),
                        rmse_mm=float(np.sqrt(np.mean(a * a))),
                        below90=int(np.sum(a < -radius[:, j])),
                        above90=int(np.sum(a > radius[:, j])),
                        negative_error_count=int(np.sum(a < 0)),
                        correlation_lag1=float(np.corrcoef(a[1:], a[:-1])[0, 1]),
                        correlation_lag30=float(np.corrcoef(a[30:], a[:-30])[0, 1]),
                    )
                )
result = dict(
    sources=sources,
    script_sha256=sha(Path(__file__)),
    horizon=30,
    error_definition="observed minus frozen predicted mean",
    latest_same_horizon_available_error="at origin n, issued at n-30, target n-1",
    interpretation="descriptive post-exposure evidence; overlap and serial dependence retained; not a causal or predictive-skill proof",
    new_training_updates=0,
    physical_calls=0,
    rows=rows,
)
(OUT / "signed_errors.json").write_text(json.dumps(result, indent=2) + "\n")
print(f"Saved {len(rows)} frozen point/phase/model error descriptions")
