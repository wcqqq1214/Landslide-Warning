"""Frozen specifications, immutable provenance, and diagnostic rendering."""

import numpy as np
import torch

from physics_guided.features import Scaler
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_history_learning.core import new_model
from .core import POINTS

CONFIG = ROOT / "config/ootang_bplus_history_diagnostics.v1_18.json"
CONFIG_SHA = "80ff6e3a2259a72aa5126a646b8139a903771039c026e6a0af0553ec04adbd79"
PLAN_SHA = "190bcab4fd03e33812c148717d00ea06fa45471fbf38dad68091f9c5147f1b8e"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered history diagnostic design changed")
    return spec


def source_guard(spec):
    source = ROOT / spec["source_run"]
    if sha(source / "artifact_manifest.json") != spec["source_index_sha256"]:
        raise ValueError("Frozen learning index differs")
    check_index(source)
    if (
        not read_json(source / "verification.json")["passed"]
        or read_json(source / "launcher.json")["exitcode"] != 0
    ):
        raise ValueError("History learning verification was unsuccessful")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    name = "docs/ootang_bplus_history_learning_results.v1.17.md"
    protected[name] = sha(ROOT / name)
    check_hashes(protected)
    return protected


def saved_scalers(source, h):
    record = read_json(source / f"scalers_{h}.json")
    return tuple(
        Scaler(
            np.array(record[k]["mean"]),
            np.array(record[k]["scale"]),
            np.array(record[k]["floor"]),
        )
        for k in ("physical", "history")
    )


def frozen_model(source, h, seed):
    model = new_model(seed)
    model.load_state_dict(
        torch.load(
            source / f"model_{h}_H_{seed}/e100.pt",
            map_location="cpu",
            weights_only=True,
        )
    )
    return model.eval().requires_grad_(False)


def plot(out, prefixes):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 3, figsize=(13, 12), layout="constrained")
    for col, (h, data) in enumerate(prefixes.items()):
        selected, train = data["kind"] == 2, data["kind"] == 0
        for j, station in enumerate(POINTS):
            ax = axes[j, col]
            need, past = data["need"][selected, j], data["need"][train, j]
            ax.axhspan(
                past.min(),
                past.max(),
                color="0.7",
                alpha=0.22,
                label="Training demand range",
            )
            ax.axhline(0, color="0.5", linewidth=0.5)
            ax.plot(np.arange(1, 181), need, color="black", label="Required correction")
            for variant, color in (("H", "#16846a"), ("H0", "#d27b17")):
                for seed in range(3):
                    ax.plot(
                        np.arange(1, 181),
                        data[variant][seed, selected, j],
                        color=color,
                        alpha=0.18,
                        linewidth=0.7,
                    )
                ax.plot(
                    np.arange(1, 181),
                    data[variant][:, selected, j].mean(axis=0),
                    color=color,
                    label=variant + " mean",
                )
            ax.set(
                title=f"{station} | diagnostic origin {h}",
                xlabel="Forecast day",
                ylabel="Correction (mm)",
            )
            ax.grid(alpha=0.15)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncol=4)
    fig.savefig(out / "history_dependence.png", dpi=150)
    plt.close(fig)
