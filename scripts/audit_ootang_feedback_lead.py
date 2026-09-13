"""Describe known one-day and known thirty-day errors on common origins."""

import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results/ootang_rolling_v3/20260913"
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def correlation(a, b):
    a, b = a - a.mean(0), b - b.mean(0)
    denominator = np.sqrt(np.sum(a * a, axis=0) * np.sum(b * b, axis=0))
    if (denominator <= 0).any():
        raise ValueError("Constant variable in correlation")
    return np.sum(a * b, axis=0) / denominator


def main():
    out = BASE / "fifth_review_evidence"
    out.mkdir(exist_ok=False)
    run = BASE / "c8_online"
    data = ROOT / "data/monitoring_data.csv"
    if sha(data) != "ee63480ad9b8065dea359d49873182b1554013f910bec1c6988c0b152bede118":
        raise ValueError("Changed observations")
    if (
        sha(run / "artifact_manifest.json")
        != "b7ddb09633adf74d858e8ced87253ab2c93f85999423bcc0549876b2b60eb2a4"
    ):
        raise ValueError("Changed C8 manifest")
    for name, expected in json.loads((run / "artifact_manifest.json").read_text())[
        "files"
    ].items():
        if sha(run / name) != expected:
            raise ValueError("Changed C8 artifact")
    labels = pd.read_csv(data)[[p + "/mm" for p in POINTS]].to_numpy(float)
    rows = []
    for phase in ("development", "later_exploratory"):
        with np.load(run / phase / "C8_ONLINE_DATA.npz") as core:
            origins = core["origins"]
            local = np.arange(30, len(origins) - 29)
            n = origins[local]
            response = labels[n + 29] - core["mean"][local, 29]
        for name in ("C8_ONLINE_DATA", "B_ANCHOR", "DRIFT14"):
            with np.load(run / phase / (name + ".npz")) as forecast:
                for lead in (1, 30):
                    previous = local - lead
                    np.testing.assert_array_equal(
                        forecast["origins"][previous] + lead - 1, n - 1
                    )
                    known = labels[n - 1] - forecast["mean"][previous, lead - 1]
                    rho = correlation(known, response)
                    for p, point in enumerate(POINTS):
                        rows.append(
                            dict(
                                phase=phase,
                                point=point,
                                reference=name,
                                known_error_lead=lead,
                                future_core_error_lead=30,
                                n=len(n),
                                correlation=float(rho[p]),
                                known_error_sd_mm=float(known[:, p].std()),
                            )
                        )
    record = dict(
        points=POINTS,
        rows=rows,
        post_exposure=True,
        independent_transfer=False,
        correlation_is_not_incremental_skill=True,
        no_sign_selected=True,
        data_sha256=sha(data),
        c8_manifest_sha256=sha(run / "artifact_manifest.json"),
        new_training=0,
        new_physics=0,
        script_sha256=sha(Path(__file__)),
    )
    (out / "feedback_lead.json").write_text(json.dumps(record, indent=2) + "\n")
    shutil.copyfile(__file__, out / Path(__file__).name)
    print(
        json.dumps(
            dict(
                rows=len(rows),
                core_correlations=[
                    r for r in rows if r["reference"] == "C8_ONLINE_DATA"
                ],
            )
        )
    )


if __name__ == "__main__":
    main()
