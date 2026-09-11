"""Pinned sources and complete-prefix diagnostic evaluation."""

import math

import numpy as np
import pandas as pd
import torch

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
from physics_guided_history_learning.core import new_model
from physics_guided_origin_learning.core import Samples, make_samples, query_table
from physics_guided_origin_learning.support import read_teachers
from physics_guided_origin_learning.verify import independent_samples
from .core import block_gradients, descent_direction, finite_differences, layout

CONFIG = ROOT / "config/ootang_bplus_origin_gradients.v1_20.json"
CONFIG_SHA = "e27fd7b45d0a329e3215f4091f37683607f50bf2ef53f3515aa95b46c83e4296"
PLAN_SHA = "97a562f96f4e681d8a7687298289c03416c472b7874a4d5b77bf3c7ce1f745fe"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered gradient diagnosis changed")
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
        raise ValueError("Source learning run did not pass verification")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    name = "docs/ootang_bplus_origin_learning_results.v1.19.md"
    protected[name] = sha(ROOT / name)
    check_hashes(protected)
    return protected


def samples_at(source, h, independent=False):
    if h not in (342, 432, 612):
        raise ValueError("Only registered training prefixes may be parsed")
    _, _, labels = load_observations(source / "input.csv", h)
    old = read_json(source / "manifest.json")["specification"]
    teachers = read_teachers(source / "input.csv", ROOT / old["physical_source"], h)
    constants = read_json(source / f"constants_{h}.json")
    if array_sha(labels) != constants["labels_sha256"]:
        raise ValueError("Training observations differ")
    scalers = saved_scalers(source, h)
    result = {}
    for strategy in ("IN", "OOF"):
        if independent:
            table, x, payload = independent_samples(
                h, strategy, labels, teachers, read_json(source / f"scalers_{h}.json")
            )
            samples = Samples(
                torch.as_tensor(x),
                *[torch.as_tensor(payload[k]) for k in ("base", "target", "weights")],
                payload["blocks"],
            )
        else:
            table = query_table(h)
            samples = make_samples(strategy, h, labels, teachers, scalers)
        pd.testing.assert_frame_equal(
            table,
            pd.read_csv(source / f"pairs_{h}.csv"),
            check_exact=False,
            atol=1e-15,
            rtol=0,
        )
        if array_sha(samples.x.numpy()) != constants["sample_hashes"][strategy]:
            raise ValueError("Frozen sample input fingerprint differs")
        with np.load(
            source / f"samples_{h}_{strategy}.npz", allow_pickle=False
        ) as saved:
            for key, value in samples.payload().items():
                np.testing.assert_array_equal(value, saved[key])
        result[strategy] = samples
    anchor = result["IN"].blocks == 0
    for name in ("x", "base", "target", "weights"):
        if not torch.equal(
            getattr(result["IN"], name)[anchor], getattr(result["OOF"], name)[anchor]
        ):
            raise ValueError("Common anchor block differs between sources")
    return result, dict(
        fit_days=h,
        observation_rows=h,
        labels_sha256=array_sha(labels),
        sample_hashes={s: array_sha(v.x.numpy()) for s, v in result.items()},
    )


def frozen_model(source, h, strategy, seed, epoch):
    model = new_model(seed)
    model.load_state_dict(
        torch.load(
            source / f"model_{h}_{strategy}_{seed}/e{epoch}.pt",
            map_location="cpu",
            weights_only=True,
        ),
        strict=True,
    )
    return model.eval().requires_grad_(True)


def check_model_state(model, snapshot):
    state = model.state_dict()
    if state.keys() != snapshot.keys() or any(
        not torch.equal(state[k], v) for k, v in snapshot.items()
    ):
        raise ArithmeticError("Frozen model state changed during diagnosis")


def evaluate_prefix(source, h, spec, budget, independent=False):
    from .verify import replay_gradients, replay_values

    inputs, info = samples_at(source, h, independent)
    batch = spec["replay_batch" if independent else "primary_batch"]
    keys, losses, gradients, finite = [], [], [], []
    names, skipped, expected_difference = None, 0, 0
    for strategy_id, strategy in enumerate(spec["strategies"]):
        samples = inputs[strategy]
        chunks = sum(
            math.ceil(int((samples.blocks == b).sum()) / batch) for b in (0, 1)
        )
        for seed in spec["seeds"]:
            for epoch in spec["epochs"]:
                model = frozen_model(source, h, strategy, seed, epoch)
                snapshot = {k: v.clone() for k, v in model.state_dict().items()}
                current_layout = layout(model)
                if current_layout[-1]["stop"] != spec["parameters"] or (
                    names is not None and names != current_layout
                ):
                    raise ValueError("Frozen parameter layout differs")
                names = current_layout
                calculate = replay_gradients if independent else block_gradients
                value, gradient = calculate(model, samples, batch, budget)
                diff = np.full((len(spec["finite_steps"]), 2, 2), np.nan)
                if epoch == 100:
                    direction = descent_direction(gradient, spec["zero_norm_tolerance"])
                    options = dict(evaluate=replay_values) if independent else {}
                    diff = finite_differences(
                        model,
                        samples,
                        direction,
                        spec["finite_steps"],
                        batch,
                        budget,
                        **options,
                    )
                    skipped += int(direction is None)
                    if direction is not None:
                        expected_difference += chunks * 2 * len(spec["finite_steps"])
                check_model_state(model, snapshot)
                if any(p.grad is not None for p in model.parameters()):
                    raise ArithmeticError(
                        "Diagnostic left parameter gradients attached"
                    )
                keys.append((strategy_id, seed, epoch))
                losses.append(value)
                gradients.append(gradient)
                finite.append(diff)
    data = dict(
        keys=np.array(keys),
        losses=np.array(losses),
        gradients=np.array(gradients),
        finite=np.array(finite),
    )
    info.update(
        finite_skipped_models=skipped, expected_difference_calls=expected_difference
    )
    return data, info, names


def plot(out, table):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = table[table.parameter_group == "all"]
    fig, axes = plt.subplots(3, 3, figsize=(14, 10), layout="constrained")
    labels = (
        "Paired / anchor gradient norm",
        "Gradient cosine",
        "Anchor derivative along unit -gT",
    )
    for col, h in enumerate((342, 432, 612)):
        selected = data[data.fit_days == h]
        for row, metric in enumerate(("norm_ratio", "cosine", "anchor_directional")):
            ax = axes[row, col]
            for strategy, color in (("IN", "#d27b17"), ("OOF", "#16846a")):
                for seed, style in enumerate(("-", "--", ":")):
                    points = selected[
                        (selected.strategy == strategy) & (selected.seed == seed)
                    ]
                    ax.plot(
                        points.epoch,
                        points[metric],
                        color=color,
                        linestyle=style,
                        marker=".",
                        label=f"{strategy} seed {seed}",
                    )
            if metric == "norm_ratio":
                ax.set_yscale("log")
                ax.axhline(1, color="0.5", linewidth=0.7)
            else:
                ax.axhline(0, color="0.5", linewidth=0.7)
            if metric == "cosine":
                ax.set_ylim(-1.05, 1.05)
            if metric == "anchor_directional":
                ax.set_yscale("symlog", linthresh=1e-3)
            ax.set(
                title=f"Training prefix {h}",
                xlabel="Saved epoch",
                ylabel=labels[row],
                xticks=(0, 25, 50, 75, 100),
            )
            ax.grid(alpha=0.15)
    handles, names = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, names, loc="outside upper center", ncol=6)
    fig.savefig(out / "objective_gradients.png", dpi=150)
    plt.close(fig)
