"""Inspect issued scale conversion without loading observation labels."""

import hashlib
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/ootang_rolling_v3/20260913/c13_innovation"
OUT = ROOT / "results/ootang_rolling_v3/20260913/c13_normalization_audit.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


sources = {}
rows = []
for phase in ("development", "later_exploratory"):
    paths = [
        RUN / phase / (name + ".npz") for name in ("C8_ONLINE_DATA", "C13_INNOV_CORE")
    ]
    sources.update({str(p.relative_to(ROOT)): sha(p) for p in paths})
    with np.load(paths[0]) as c, np.load(paths[1]) as m:
        count = len(c["origins"]) - 29
        i = np.arange(30, count)
        ratios = c["sigma"][i, 29] / c["sigma"][i - 30, 29]
        delta = m["mean"][:count, 29] - c["mean"][:count, 29]
        for p, point in enumerate(("ATU1", "ATU5", "MJ3", "MJ1")):
            rows.append(
                dict(
                    phase=phase,
                    point=point,
                    horizon=30,
                    n_ratios=len(i),
                    maximum_scale_ratio=float(ratios[:, p].max()),
                    median_scale_ratio=float(np.median(ratios[:, p])),
                    maximum_absolute_core_correction_mm=float(abs(delta[:, p]).max()),
                )
            )
result = dict(
    sources=sources,
    script_sha256=sha(Path(__file__)),
    rows=rows,
    core_correction_identity="beta * error_core(n-h,h) * sigma_core(n,h)/sigma_core(n-h,h)",
    labels_loaded=False,
    new_fits=0,
    physical_calls=0,
    limitation="algebraic and descriptive evidence only; no proof that fixed units improve forecasting",
)
OUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
