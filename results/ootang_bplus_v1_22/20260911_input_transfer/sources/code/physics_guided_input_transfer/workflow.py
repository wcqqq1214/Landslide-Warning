"""Read frozen inputs within each prefix, then lock deterministic probe outputs."""

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_history_diagnostics.workflow import saved_scalers
from physics_guided_history_learning.core import windows
from physics_guided_origin_learning.core import make_samples, query_table
from physics_guided_origin_learning.support import read_teachers
from .core import match_inputs, POINTS

CONFIG = ROOT / "config/ootang_bplus_input_transfer.v1_22.json"
CONFIG_SHA = "e8ecc15a83bc505a616cb4046345cea18a6ba358214a589cf0c6765899ff8f76"
PLAN_SHA = "836262d8c17fdc8cfb22e0ead93882ff98a5d5ec5e8b61158796dcdc8ebf4d75"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered input transfer design changed")
    return spec


def source_guard(spec):
    source = ROOT / spec["source_run"]
    if sha(source / "artifact_manifest.json") != spec["source_index_sha256"]:
        raise ValueError("Frozen balanced learning source changed")
    check_index(source)
    if (
        not read_json(source / "verification.json")["passed"]
        or read_json(source / "launcher.json")["exitcode"] != 0
    ):
        raise ValueError("Source verification failed")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    protected[spec["source_results"]] = sha(ROOT / spec["source_results"])
    check_hashes(protected)
    if (
        spec["physical_source"]
        != read_json(source / "manifest.json")["specification"]["physical_source"]
    ):
        raise ValueError("Physical source changed")
    return protected


def prepare(h, spec):
    source = ROOT / spec["source_run"]
    _, _, labels = load_observations(source / "input.csv", h)
    pool = read_teachers(source / "input.csv", ROOT / spec["physical_source"], h)
    scalers = saved_scalers(source, h)
    base, features = pool[h]
    n = spec["horizons"][str(h)]
    queries = np.array([(h, h + lead) for lead in range(n)])
    query_x = windows(base, features, labels, h, queries, *scalers, "H").numpy()
    original = read_json(source / f"constants_{h}.json")
    if (
        array_sha(labels) != original["labels_sha256"]
        or array_sha(features) != original["feature_sha256"]
    ):
        raise ValueError("Source observation/features fingerprint changed")
    table = query_table(h)
    pd.testing.assert_frame_equal(
        table,
        pd.read_csv(source / f"pairs_{h}.csv"),
        check_exact=False,
        rtol=1e-12,
        atol=1e-15,
    )
    mask = table.block.to_numpy() == 1
    pairs = table.loc[mask, ["origin", "target"]].to_numpy()
    results = {}
    info = dict(
        fit_days=h,
        label_rows=h,
        labels_sha256=array_sha(labels),
        query_sha256=array_sha(query_x),
        sample_hashes={},
    )
    for strategy in spec["strategies"]:
        samples = make_samples(strategy, h, labels, pool, scalers)
        x = samples.x.numpy()
        digest = array_sha(x)
        if digest != original["sample_hashes"][strategy]:
            raise ValueError("Original full sample input fingerprint changed")
        with np.load(
            source / f"samples_{h}_{strategy}.npz", allow_pickle=False
        ) as stored:
            for k, value in samples.payload().items():
                np.testing.assert_array_equal(value, stored[k])
        candidate_x = x[mask]
        selection = match_inputs(h, pairs[:, 0], pairs[:, 1], candidate_x, query_x)
        with np.load(source / f"mean_{h}_{strategy}.npz", allow_pickle=False) as stored:
            norm_mean = stored["means"].mean(axis=0)[h : h + n]
        results[strategy] = dict(
            origins=pairs[:, 0],
            targets=pairs[:, 1],
            candidate_correction=(samples.target - samples.base).numpy()[mask],
            query_base=base[h : h + n],
            norm_mean=norm_mean,
            **selection,
        )
        info["sample_hashes"][strategy] = digest
    return results, info


def plot(out, queries):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 3, figsize=(12, 12), layout="constrained")
    for j, station in enumerate(POINTS):
        for ci, h in enumerate((342, 432, 612)):
            ax = axes[j, ci]
            subset = queries[(queries.fit_days == h) & (queries.station == station)]
            q = subset[subset.strategy == "OOF"]
            ax.plot(
                q.lead + 1,
                q.query_demand_mm,
                color="black",
                label="Required correction",
            )
            for strategy, color in (("IN", "#c7781b"), ("OOF", "#278465")):
                d = subset[subset.strategy == strategy]
                ax.plot(
                    d.lead + 1,
                    d.norm_correction_mm,
                    color=color,
                    label=f"NORM {strategy}",
                )
                ax.plot(
                    d.lead + 1,
                    d.probe_correction_mm,
                    "--",
                    color=color,
                    label=f"Input probe {strategy}",
                )
            ax.axhline(0, color="#888888", lw=0.7)
            ax.set_title(
                f"{station} | origin {h}" + (" (auxiliary)" if h == 342 else "")
            )
            ax.set_xlabel("Forecast day")
            ax.set_ylabel("Correction (mm)")
            ax.grid(alpha=0.2)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=3)
    fig.suptitle("Same-lead frozen-input transfer probe | exploratory, no fitting")
    fig.savefig(out / "correction_transfer.png", dpi=150)
    plt.close(fig)
