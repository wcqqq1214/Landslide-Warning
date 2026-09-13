"""Paired uncertainty summaries and figures for frozen rolling forecasts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import signal

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def losses(y, mean, sigma):
    error = mean - y
    z = -error / sigma
    q = ndtri(0.95)
    lo, hi = mean - q * sigma, mean + q * sigma
    return dict(
        absolute=np.abs(error),
        squared=error**2,
        crps=sigma
        * (
            z * (2 * ndtr(z) - 1)
            + np.sqrt(2 / np.pi) * np.exp(-(z**2) / 2)
            - 1 / np.sqrt(np.pi)
        ),
        coverage90=((y >= lo) & (y <= hi)).astype(float),
        width90=hi - lo,
        interval_score90=hi - lo + 20 * (np.maximum(lo - y, 0) + np.maximum(y - hi, 0)),
    )


def statistics(values, indices=None):
    # Shapes are [origin, point] or [replicate, origin, point].
    selected = values if indices is None else {k: v[indices] for k, v in values.items()}
    by_point = {k: v.mean(axis=-2) for k, v in selected.items()}
    output = {
        k: v.mean(axis=-1)
        for k, v in by_point.items()
        if k not in ("absolute", "squared")
    }
    output["mae"] = by_point["absolute"].mean(axis=-1)
    output["rmse"] = np.sqrt(by_point["squared"]).mean(axis=-1)
    output["pooled_rmse"] = np.sqrt(by_point["squared"].mean(axis=-1))
    return output


def circular_blocks(n, length, replicates, seed):
    rng = np.random.default_rng(seed)
    starts = rng.integers(n, size=(replicates, int(np.ceil(n / length))))
    return ((starts[..., None] + np.arange(length)) % n).reshape(replicates, -1)[:, :n]


def independently_check_decision(run, phase_rows, point_rows, spec, simple):
    h = spec["primary_horizon"]
    candidate = spec["candidate"]
    rows = {r["model"]: r for r in phase_rows if r["horizon"] == h}
    a, b = rows[candidate], rows["B_ANCHOR"]
    rule = spec["effect"]
    checks = {
        f"average_{k}_improves_5pct": bool(
            a[k] <= (1 - rule["relative_improvement"]) * b[k]
        )
        for k in ("mae", "rmse", "crps", "interval_score90")
    }
    lower, upper = rule["coverage90_average"]
    checks["average_coverage90"] = bool(lower <= a["coverage90"] <= upper)
    rows_by_point = {
        (r["model"], r["point"]): r
        for r in point_rows
        if r["phase"] == phase_rows[0]["phase"]
    }
    for point in POINTS:
        pc, pb = rows_by_point[(candidate, point)], rows_by_point[("B_ANCHOR", point)]
        for k in ("mae", "rmse"):
            checks[f"{point}_{k}_nonregression"] = bool(
                pc[k] <= pb[k] + rule["point_mean_atol_mm"]
            )
        for k in ("crps", "interval_score90"):
            checks[f"{point}_{k}_guard"] = bool(
                pc[k] <= pb[k] * (1 + rule["point_probability_max_regression"])
            )
        lower, upper = rule["coverage90_point"]
        checks[f"{point}_coverage90"] = bool(lower <= pc["coverage90"] <= upper)
    for k in ("rmse", "crps"):
        checks[f"beats_simple_{k}"] = bool(a[k] <= rows[simple][k])
    saved = json.loads((run / "decision.json").read_text())
    if saved["checks"] != checks or saved["passed"] != all(checks.values()):
        raise ValueError("Independent effect decision differs from the saved decision")


def plot_stage(phase, forecasts, dates, y, primary, simple, h, summary, out):
    ablation = primary.replace("FULL", "DATA")
    names = [primary, "B_ANCHOR", simple, ablation]
    labels = ["Ridge + B+", "Anchored B+", "Current-velocity drift", "Ridge data-only"]
    origin = forecasts[primary]["origins"]
    targets = origin + h - 1
    mask = targets < len(y)
    when, actual = dates[targets[mask]], y[targets[mask]]
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "savefig.dpi": 160,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    for j, ax in enumerate(axes.flat):
        mu = forecasts[primary]["mean"][mask, h - 1, j]
        sd = forecasts[primary]["sigma"][mask, h - 1, j]
        ax.fill_between(
            when,
            mu - ndtri(0.95) * sd,
            mu + ndtri(0.95) * sd,
            color=COLORS[0],
            alpha=0.18,
            label="Ridge + B+: 90% interval",
        )
        ax.plot(when, actual[:, j], color="#202020", lw=1.6, label="Observation")
        for name, label, color in zip(names[:3], labels[:3], COLORS[:3]):
            ax.plot(
                when,
                forecasts[name]["mean"][mask, h - 1, j],
                color=color,
                lw=1.2,
                linestyle="-" if name == primary else "--",
                label=label,
            )
        ax.set(title=POINTS[j], ylabel="Displacement (mm)")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.15)
    handles, labels_ = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="outside lower center", ncol=3, frameon=False)
    fig.suptitle(
        f"{phase.title()}: daily issued {h}-day forecasts (target dates)\n"
        "Known future rainfall and reservoir level; exploratory historical replay"
    )
    fig.savefig(out / f"{phase}_forecast_h{h}.png")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(12, 6), constrained_layout=True)
    for j, ax in enumerate(axes.flat):
        for name, label, color in zip(names, labels, COLORS):
            error = forecasts[name]["mean"][mask, h - 1, j] - actual[:, j]
            ax.plot(when, error, color=color, lw=1.1, label=label)
        ax.axhline(0, color="black", lw=0.5)
        ax.set(title=POINTS[j], ylabel="Signed error (mm)")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.15)
    fig.legend(
        *axes[0, 0].get_legend_handles_labels(),
        loc="outside lower center",
        ncol=4,
        frameon=False,
    )
    fig.suptitle(f"{phase.title()}: {h}-day errors, complete target dates")
    fig.savefig(out / f"{phase}_errors_h{h}.png")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.7), constrained_layout=True)
    for ax, metric, title in zip(
        axes,
        ("rmse", "crps", "coverage90"),
        ("Mean point RMSE (mm)", "CRPS (mm)", "90% coverage"),
    ):
        for name, label, color in zip(names, labels, COLORS):
            rows = summary[summary.model.eq(name)].sort_values("horizon")
            ax.plot(rows.horizon, rows[metric], label=label, color=color)
        if metric == "coverage90":
            ax.axhline(0.9, color="black", lw=0.6, ls=":")
            ax.set_ylim(0, 1.02)
        ax.set(xlabel="Forecast lead (days)", title=title)
        ax.grid(alpha=0.15)
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        loc="outside lower center",
        ncol=4,
        frameon=False,
    )
    fig.savefig(out / f"{phase}_horizons.png")
    plt.close(fig)


def analyze(development, transfer, out):
    if out.exists():
        raise FileExistsError(out)
    config_files = list(
        (development / "sources/config").glob("ootang_rolling_probability*.json")
    )
    if len(config_files) != 1:
        raise ValueError("Ambiguous frozen configuration")
    spec = json.loads(config_files[0].read_text())
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Frozen observation data changed")
    remaining = (
        datetime.fromisoformat(spec["deadline_utc"]) - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining <= 0:
        raise TimeoutError("Authorized overall deadline expired")

    def stop(_signum, _frame):
        raise TimeoutError("Analysis time budget expired")

    signal.signal(signal.SIGALRM, stop)
    signal.alarm(int(min(600, remaining)))
    out.mkdir(parents=True)
    source_records = {}
    all_summary, point_rows, nonoverlap, seed_rows, comparisons, phase_info = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for phase, run in (("development", development), ("transfer", transfer)):
        status = json.loads((run / "status.json").read_text())
        if status["state"] != "completed":
            raise ValueError("Incomplete run")
        manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
        for name, expected in manifest.items():
            if sha(run / name) != expected:
                raise ValueError("Frozen artifact changed: " + name)
        source_records[str(run.relative_to(ROOT))] = sha(run / "artifact_manifest.json")
        start, end = spec["stages"][phase]
        table = pd.read_csv(
            ROOT / spec["data"],
            nrows=end,
            usecols=["Date"] + [p + "/mm" for p in POINTS],
        )
        dates = pd.to_datetime(table.Date).to_numpy()
        y = table[[p + "/mm" for p in POINTS]].to_numpy(float)
        forecasts = {}
        phase_summary = []
        horizon = spec["primary_horizon"]
        primary = spec["candidate"]
        simple = json.loads((development / "decision.json").read_text())[
            "simple_baseline"
        ]
        primary_losses = {}
        for file in sorted((run / phase).glob("*.npz")):
            with np.load(file) as a:
                saved = {k: a[k].copy() for k in a.files}
            name = file.stem
            forecasts[name] = saved
            for h in range(1, spec["horizons"] + 1):
                ids = saved["origins"] + h - 1
                mask = ids < end
                value = losses(
                    y[ids[mask]],
                    saved["mean"][mask, h - 1],
                    saved["sigma"][mask, h - 1],
                )
                row = dict(
                    phase=phase,
                    model=name,
                    horizon=h,
                    n=int(mask.sum()),
                    **statistics(value),
                )
                if name == "B_RAW":
                    for key in ("crps", "coverage90", "width90", "interval_score90"):
                        row[key] = np.nan
                if "_seed" in name:
                    seed_rows.append(row)
                else:
                    all_summary.append(row)
                    phase_summary.append(row)
                if h == horizon and "_seed" not in name:
                    primary_losses[name] = value
                    nr = dict(
                        phase=phase,
                        model=name,
                        horizon=h,
                        n=len(value["absolute"][::h]),
                        **statistics(value, np.arange(0, mask.sum(), h)),
                    )
                    if name == "B_RAW":
                        for key in (
                            "crps",
                            "coverage90",
                            "width90",
                            "interval_score90",
                        ):
                            nr[key] = np.nan
                    nonoverlap.append(nr)
                    for j, point in enumerate(POINTS):
                        v = {k: a[:, j : j + 1] for k, a in value.items()}
                        point_row = dict(
                            phase=phase,
                            model=name,
                            point=point,
                            horizon=h,
                            n=int(mask.sum()),
                            **statistics(v),
                        )
                        if name == "B_RAW":
                            for key in (
                                "crps",
                                "coverage90",
                                "width90",
                                "interval_score90",
                            ):
                                point_row[key] = np.nan
                        point_rows.append(point_row)
        # Pair the same origin blocks across every model and every point.
        boot = spec["bootstrap"]
        n = end - start - horizon + 1
        indices = circular_blocks(
            n, boot["block_length"], boot["replicates"], boot["seed"]
        )
        ablation = primary.replace("FULL", "DATA")
        pairs = [
            (primary, "B_ANCHOR"),
            (primary, simple),
            (primary, ablation),
            (ablation, "B_ANCHOR"),
            (ablation, simple),
        ]
        for candidate, reference in pairs:
            a, b = primary_losses[candidate], primary_losses[reference]
            observed_a, observed_b = statistics(a), statistics(b)
            sampled_a, sampled_b = statistics(a, indices), statistics(b, indices)
            for metric in observed_a:
                delta = sampled_a[metric] - sampled_b[metric]
                lower, upper = np.quantile(
                    delta, [(1 - boot["confidence"]) / 2, (1 + boot["confidence"]) / 2]
                )
                comparisons.append(
                    dict(
                        phase=phase,
                        candidate=candidate,
                        reference=reference,
                        metric=metric,
                        difference=observed_a[metric] - observed_b[metric],
                        lower=lower,
                        upper=upper,
                        n=n,
                        replicates=boot["replicates"],
                        block_length=boot["block_length"],
                        seed=boot["seed"],
                    )
                )
        saved_summary = pd.read_csv(run / phase / "summary.csv").set_index(
            ["model", "horizon"]
        )
        for row in phase_summary:
            for key in (
                "mae",
                "rmse",
                "pooled_rmse",
                "crps",
                "coverage90",
                "width90",
                "interval_score90",
            ):
                np.testing.assert_allclose(
                    row[key],
                    saved_summary.loc[(row["model"], row["horizon"]), key],
                    atol=1e-8,
                    rtol=5e-11,
                )
        independently_check_decision(run, phase_summary, point_rows, spec, simple)
        plot_stage(
            phase,
            forecasts,
            dates,
            y,
            primary,
            simple,
            horizon,
            pd.DataFrame(phase_summary),
            out,
        )
        phase_info.append(
            dict(
                phase=phase,
                train_rows=start,
                released_rows=end - start,
                h30_n_per_point=n,
                nonoverlap_origins=len(np.arange(0, n, horizon)),
                first_h30_target=str(dates[start + horizon - 1])[:10],
                last_target=str(dates[end - 1])[:10],
            )
        )
    for name, rows in (
        ("horizon_summary", all_summary),
        ("primary_points", point_rows),
        ("nonoverlap_summary", nonoverlap),
        ("seed_summary", seed_rows),
        ("paired_bootstrap", comparisons),
    ):
        pd.DataFrame(rows).to_csv(
            out / f"{name}.csv", index=False, float_format="%.12g"
        )
    metadata = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        phases=phase_info,
        input_manifest_sha256=source_records,
        data_sha256=sha(ROOT / spec["data"]),
        script_sha256=sha(__file__),
        protocol="circular moving block; paired across models/points",
        bootstrap=spec["bootstrap"],
        seed_interpretation="deterministic ridge; seed0 is an interface label",
        original_fixed_origin_task_unchanged=True,
        new_fits=0,
        physical_forward_calls=0,
        statistical_intervals_do_not_change_effect_gate=True,
        effect_decisions_independently_checked=True,
    )
    (out / "analysis.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copyfile(__file__, out / "analyze_ootang_rolling.py")
    files = {p.name: sha(p) for p in out.iterdir() if p.is_file()}
    (out / "artifact_manifest.json").write_text(
        json.dumps(dict(files=files), indent=2) + "\n"
    )
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", required=True)
    parser.add_argument("--transfer", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    analyze(
        Path(args.development).resolve(),
        Path(args.transfer).resolve(),
        Path(args.out).resolve(),
    )
