"""Load only sealed arrays and derive the registered descriptive tables."""

import sys

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
    trajectory_name,
)
from .core import (
    feature_statistics,
    multiplier_statistics,
    point_statistics,
    scale_statistics,
)

CONFIG = ROOT / "config/ootang_bplus_rate_diagnostics.v1_13.json"
CONFIG_SHA = "5ed0138ec863b3c8de4d2f110bbd964a7585b7dfa935ece66c38480cadbc2ba5"
PLAN_SHA = "64423cb89194cd8c90fe1989419cc8ed6c2408393a2150e8ea7bcdd3205578e1"
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DOMAINS = ("O3", "O2", "O1_up", "O1_down")
FEATURES = (
    "rain_mm",
    "rwl_m",
    "rwl_daily_change_m",
    *["rain_head_" + d for d in DOMAINS],
    *["moisture_" + d for d in DOMAINS],
    "reservoir_head",
)
TABLES = ("point", "multiplier", "feature", "joint_feature", "calibration")


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered diagnostic design changed")
    return spec


def no_model_runtime():
    prohibited = (
        "torch",
        "physics_guided_state_pinn",
        "physics_guided_shared_mechanics",
    )
    if any(name.startswith(prohibited) for name in sys.modules):
        raise RuntimeError("A neural or mechanics runtime was imported")


def load_npz(path):
    with np.load(path, allow_pickle=False) as data:
        return {k: data[k].copy() for k in data.files}


def source_guard(spec):
    source = ROOT / spec["source_run"]
    if sha(source / "artifact_manifest.json") != spec["source_index_sha256"]:
        raise ValueError("Frozen learning run index changed")
    check_index(source)
    if not read_json(source / "completed.json")["numerical_verification"]:
        raise ValueError("Source learning experiment was not verified")
    if not read_json(source / "verification.json")["passed"]:
        raise ValueError("Source verification failed")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    result_doc = "docs/ootang_bplus_rate_learning_results.v1.12.md"
    protected[result_doc] = sha(ROOT / result_doc)
    check_hashes(protected)
    return protected


def project(values, observation):
    return np.einsum("...d,pd->...p", values, observation)


def load_daily(spec):
    source = ROOT / spec["source_run"]
    frame = pd.read_csv(source / "input_prefix_792.csv", nrows=792)
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date))
    if not dates.equals(pd.date_range("2016-07-01", periods=792)):
        raise ValueError("Input calendar changed")
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(float)
    drivers = load_npz(ROOT / spec["driver_source"])
    daily = {}
    for h, end in zip(spec["prefixes"], spec["ends"]):
        base = load_npz(
            ROOT
            / spec["physical_source"]
            / trajectory_name(h, "A" if h == 342 else "B")
        )
        if base["mean"].shape != (h + 180, 4):
            raise ValueError("Physical teacher horizon changed")
        saved = [load_npz(source / f"model_{h}_{seed}/S.npz") for seed in spec["seeds"]]
        if any(s["mean"].shape != (h + 180, 4) for s in saved):
            raise ValueError("Frozen S horizon changed")
        if not np.array_equal(base["dates"][:end], drivers["dates"][:end]):
            raise ValueError("Physical and forcing calendars differ")
        forcing = drivers["forcing"][:end]
        raw = np.column_stack(
            [
                forcing[:, 0],
                forcing[:, 1],
                np.r_[0.0, np.diff(forcing[:, 1])],
                base["rain_head"][:end],
                base["moisture"][:end],
                base["reservoir_head"][:end],
            ]
        )
        constants = read_json(source / f"constants_{h}.json")
        mean, std = [np.array(constants[k]) for k in ("feature_mean", "feature_std")]
        value = dict(
            dates=base["dates"][:end],
            labels=labels[:end],
            forcing=forcing,
            raw_features=raw,
            z_features=(raw - mean) / std,
            feature_mean=mean,
            feature_std=std,
            base_mean=base["mean"][:end],
            base_background=base["background"][:end],
            base_s=(base["coordinates"] - base["background"])[:end],
            base_rate=base["background_rate"][:end],
            observation=base["observation_matrix"],
            y0=drivers["y0"],
            prediction=np.stack([s["mean"][:end] for s in saved]),
            multiplier=np.stack([s["multiplier"][:end] for s in saved]),
            background=np.stack([s["background"][:end] for s in saved]),
            state_s=np.stack([s["state"][:end, :4] for s in saved]),
        )
        value["correction_background"] = project(
            value["background"] - value["base_background"], value["observation"]
        )
        value["correction_state"] = project(
            value["state_s"] - value["base_s"], value["observation"]
        )
        if raw.shape != (end, 12) or any(
            not np.isfinite(v).all() for k, v in value.items() if k != "dates"
        ):
            raise ValueError("Nonfinite or malformed diagnostic input")
        daily[h] = value
    no_model_runtime()
    return daily


def periods(h, end):
    return (("train", 30, h), ("forward", h, end))


def build_tables(spec, daily):
    rows = {key: [] for key in TABLES}
    for h, end in zip(spec["prefixes"], spec["ends"]):
        values = daily[h]
        for part, start, stop in periods(h, end):
            info = dict(
                prefix=h, part=part, start_index=start, end_index_exclusive=stop
            )
            section = slice(start, stop)
            for seed in ["0", "1", "2", "mean"]:
                selected = {}
                for key in ("prediction", "correction_background", "correction_state"):
                    selected[key] = (
                        values[key].mean(axis=0)
                        if seed == "mean"
                        else values[key][int(seed)]
                    )[section]
                for j, point in enumerate(POINTS):
                    stats = point_statistics(
                        values["base_mean"][section, j],
                        selected["prediction"][:, j],
                        values["labels"][section, j],
                        selected["correction_background"][:, j],
                        selected["correction_state"][:, j],
                        spec["direction_tolerance_mm"],
                    )
                    rows["point"].append(
                        dict(**info, seed=seed, station=point, **stats)
                    )
            for seed in spec["seeds"]:
                for j, domain in enumerate(DOMAINS):
                    rows["multiplier"].append(
                        dict(
                            **info,
                            seed=seed,
                            domain=domain,
                            **multiplier_statistics(
                                values["multiplier"][seed, section, j],
                                values["base_rate"][section, j],
                                spec["log_multiplier_edge"],
                            ),
                        )
                    )
            features, joint = feature_statistics(
                values["z_features"], h, start, stop, spec["feature_range_tolerance_z"]
            )
            rows["feature"].extend(
                dict(**info, feature=name, **stats)
                for name, stats in zip(FEATURES, features)
            )
            rows["joint_feature"].append(dict(**info, **joint))
    source = ROOT / spec["source_run"]
    metrics = pd.read_csv(source / "seed_metrics.csv")
    for n in (432, 612):
        calibration = load_npz(source / f"calibration_{n}_S.npz")
        future = daily[n]["prediction"][:, n:] - daily[n]["labels"][None, n:]
        for seed in spec["seeds"]:
            for j, point in enumerate(POINTS):
                selected = metrics[
                    (metrics.outer_days == n)
                    & (metrics.strategy == "S")
                    & (metrics.part == "prediction")
                    & (metrics.seed == seed)
                    & (metrics.station == point)
                ]
                if len(selected) != 1:
                    raise ValueError("Ambiguous original probability metric")
                old = selected.iloc[0]
                rows["calibration"].append(
                    dict(
                        prefix=n,
                        seed=seed,
                        station=point,
                        **scale_statistics(
                            calibration["errors"][seed, :, j],
                            future[seed, :, j],
                            calibration["sigma"][seed, j],
                        ),
                        coverage_90=float(old.coverage_90),
                        width_90_mm=float(old.width_90_mm),
                        interval_score_90_mm=float(old.interval_score_90_mm),
                    )
                )
    result = {key: pd.DataFrame(value) for key, value in rows.items()}
    for key, frame in result.items():
        if len(frame) != spec[key + "_rows"]:
            raise ValueError("Registered diagnostic table size differs")
    return result


def plot(out, daily):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(12, 12), layout="constrained")
    for col, h in enumerate((432, 612)):
        data = daily[h]
        x = np.arange(30, len(data["labels"])) - h
        demand = data["labels"] - data["base_mean"]
        correction = data["prediction"] - data["base_mean"]
        for j, point in enumerate(POINTS):
            ax = axes[j, col]
            ax.plot(
                x,
                demand[30:, j],
                color="black",
                label="Observed need (diagnostic only)",
            )
            ax.plot(
                x,
                correction[:, 30:, j].mean(axis=0),
                color="#315f9c",
                label="S correction, mean of 3 seeds",
            )
            ax.fill_between(
                x,
                correction[:, 30:, j].min(axis=0),
                correction[:, 30:, j].max(axis=0),
                color="#315f9c",
                alpha=0.2,
                label="Seed range",
            )
            ax.axvline(0, color="0.5", linestyle="--")
            ax.axhline(0, color="0.5", linewidth=0.7)
            ax.set(
                title=f"{point} | prefix {h}",
                xlabel="Days relative to forecast start",
                ylabel="Correction (mm)",
            )
            ax.grid(alpha=0.15)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncol=2)
    fig.savefig(out / "correction_demand.png", dpi=150)
    plt.close(fig)
