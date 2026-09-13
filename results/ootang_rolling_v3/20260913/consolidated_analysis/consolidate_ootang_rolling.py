"""Read-only fifteen-candidate registry and paired C8 analysis with figures."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import signal
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import ndtri

from analyze_ootang_rolling import (
    sha,
    losses,
    statistics,
    circular_blocks,
    independently_check_decision,
)

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
METRICS = (
    "mae",
    "rmse",
    "pooled_rmse",
    "crps",
    "coverage90",
    "width90",
    "interval_score90",
)
MODELS = (
    "C8_ONLINE_FULL",
    "C8_ONLINE_DATA",
    "B_ANCHOR",
    "DRIFT1",
    "C8_STATIC_FULL",
    "C8_STATIC_DATA",
)
DISPLAY = (
    "Online Ridge + B+",
    "Online Ridge data only",
    "Anchored B+",
    "Current velocity",
)
COLORS = ("#0072B2", "#D55E00", "#999999", "#009E73")
STYLES = ("-", "--", ":", "-.")


def read_npz(path):
    with np.load(path, allow_pickle=False) as saved:
        return {k: saved[k].copy() for k in saved.files}


def check_manifest(path, expected):
    manifest = path / "artifact_manifest.json"
    if sha(manifest) != expected:
        raise ValueError("Changed frozen manifest: " + str(path))
    files = json.loads(manifest.read_text())["files"]
    for file, wanted in files.items():
        if sha(path / file) != wanted:
            raise ValueError("Changed frozen artifact: " + file)
    if json.loads((path / "status.json").read_text())["state"] != "completed":
        raise ValueError("Incomplete source run")
    return len(files)


def collect_registry(cfg, out):
    checked, sources, main, every = {}, [], [], []
    for entry in cfg["entries"]:
        run = ROOT / entry["run"]
        if entry["run"] not in checked:
            checked[entry["run"]] = check_manifest(run, entry["manifest_sha256"])
        verify = ROOT / entry["verification"]
        if (
            sha(verify) != entry["verification_sha256"]
            or not json.loads(verify.read_text())["passed"]
        ):
            raise ValueError("Changed or failed independent verification")
        table = pd.read_csv(run / entry["phase"] / "summary.csv")
        decision = json.loads((ROOT / entry["decision"]).read_text())
        if decision["candidate"] != entry["model"] or len(decision["checks"]) != 27:
            raise ValueError("Wrong candidate identity or effect criteria")
        if sum(decision["checks"].values()) != decision["passed_count"]:
            raise ValueError("Saved criterion count differs")
        row = table[table.model.eq(entry["model"]) & table.horizon.eq(30)]
        if len(row) != 1 or row.iloc[0].n_per_point != (
            347 if entry["display_phase"] == "development" else 264
        ):
            raise ValueError("Incomplete primary dates")
        summary = row.iloc[0].to_dict()
        summary.update(
            candidate_number=entry["candidate_number"],
            phase=entry["display_phase"],
            passed=decision["passed"],
            passed_count=decision["passed_count"],
            simple_baseline=decision["simple_baseline"],
            failed_checks=";".join(k for k, v in decision["checks"].items() if not v),
            source=entry["run"] + "/" + entry["phase"],
        )
        for ref, prefix in [
            ("B_ANCHOR", "b_anchor"),
            (decision["simple_baseline"], "simple"),
        ]:
            reference = table[table.model.eq(ref) & table.horizon.eq(30)].iloc[0]
            summary.update({prefix + "_" + k: reference[k] for k in METRICS})
        main.append(summary)
        table["candidate_number"] = entry["candidate_number"]
        table["phase"] = entry["display_phase"]
        table["source"] = summary["source"]
        every.append(table)
        sources.append(entry)
    pd.DataFrame(main).to_csv(
        out / "all_candidates_h30.csv", index=False, float_format="%.12g"
    )
    pd.concat(every, ignore_index=True).to_csv(
        out / "all_saved_horizons.csv", index=False, float_format="%.12g"
    )
    return dict(
        artifact_counts=checked, candidate_stage_rows=len(main), entries=sources
    )


def export_figure(fig, prefix, require_matplotlib_panel_alignment):
    fig.canvas.draw()
    require_matplotlib_panel_alignment(
        fig,
        json_out=str(prefix) + ".alignment.json",
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        require_panel_labels=True,
        strict=True,
    )
    # Fixed page dimensions preserve the measured physical panel geometry.
    fig.savefig(str(prefix) + ".pdf")
    fig.savefig(str(prefix) + ".svg")
    fig.savefig(str(prefix) + ".png", dpi=600)
    plt.close(fig)


def figures(phase, forecasts, dates, labels, rows, out, align):
    h = 30
    primary = forecasts[MODELS[0]]
    ids = primary["origins"] + h - 1
    mask = ids < len(labels)
    when, actual = dates[ids[mask]], labels[ids[mask]]
    title = "Development" if phase == "development" else "Later exploratory replay"
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "font.size": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "legend.frameon": False,
        }
    )
    for kind in ("forecasts", "errors"):
        fig, axes = plt.subplots(2, 2, figsize=(7.205, 4.961))
        fig.subplots_adjust(
            left=0.09, right=0.98, bottom=0.095, top=0.79, wspace=0.25, hspace=0.43
        )
        for p, ax in enumerate(axes.flat):
            if kind == "forecasts":
                mu, sd = (
                    primary["mean"][mask, h - 1, p],
                    primary["sigma"][mask, h - 1, p],
                )
                ax.fill_between(
                    when,
                    mu - ndtri(0.95) * sd,
                    mu + ndtri(0.95) * sd,
                    color=COLORS[0],
                    alpha=0.18,
                    label="FULL 90% prediction interval",
                )
                ax.plot(
                    when, actual[:, p], color="#202020", lw=0.9, label="Observation"
                )
            else:
                ax.axhline(0, color="#555555", lw=0.4)
            for name, label, color, style in zip(MODELS[:4], DISPLAY, COLORS, STYLES):
                value = forecasts[name]["mean"][mask, h - 1, p]
                if kind == "errors":
                    value = value - actual[:, p]
                ax.plot(when, value, color=color, linestyle=style, lw=0.85, label=label)
            ax.set_title(POINTS[p], fontsize=7, pad=7)
            ax.set_ylabel(
                "Displacement (mm)" if kind == "forecasts" else "Signed error (mm)"
            )
            ax.text(
                0,
                1,
                "abcd"[p],
                transform=ax.transAxes,
                va="bottom",
                ha="right",
                fontweight="bold",
                fontsize=8,
            )
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax.grid(alpha=0.12)
        fig.suptitle(f"{title}: daily issued 30-day {kind}", fontsize=9, y=0.98)
        fig.legend(
            *axes[0, 0].get_legend_handles_labels(),
            loc="upper center",
            bbox_to_anchor=(0.5, 0.945),
            ncol=3 if kind == "forecasts" else 2,
            fontsize=7,
        )
        export_figure(fig, out / f"{phase}_{kind}_h30", align)
    fig, axes = plt.subplots(1, 3, figsize=(7.205, 2.953))
    fig.subplots_adjust(left=0.075, right=0.98, bottom=0.19, top=0.71, wspace=0.32)
    for i, (ax, metric, name) in enumerate(
        zip(
            axes,
            ("rmse", "crps", "coverage90"),
            ("Mean point RMSE (mm)", "CRPS (mm)", "90% coverage"),
        )
    ):
        for model, label, color, style in zip(MODELS[:4], DISPLAY, COLORS, STYLES):
            table = rows[rows.model.eq(model)].sort_values("horizon")
            ax.plot(
                table.horizon,
                table[metric],
                color=color,
                linestyle=style,
                label=label,
                lw=0.9,
            )
        if metric == "coverage90":
            ax.axhline(0.9, color="#555555", lw=0.5)
            ax.set_ylim(0, 1.03)
        ax.set(xlabel="Forecast lead (days)", title=name)
        ax.text(
            0,
            1,
            "abc"[i],
            transform=ax.transAxes,
            va="bottom",
            ha="right",
            fontweight="bold",
            fontsize=8,
        )
        ax.grid(alpha=0.12)
    fig.suptitle(f"{title}: all prescribed forecast leads", fontsize=9, y=0.98)
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.91),
        ncol=2,
        fontsize=7,
    )
    export_figure(fig, out / f"{phase}_horizons", align)


def c8_analysis(cfg, out, align):
    if sha(ROOT / cfg["c8_config"]) != cfg["c8_config_sha256"]:
        raise ValueError("Changed C8 configuration")
    spec = json.loads((ROOT / cfg["c8_config"]).read_text())
    if spec["bootstrap"] != cfg["bootstrap"]:
        raise ValueError("Changed original statistics protocol")
    run = ROOT / cfg["c8_run"]
    all_rows, points, nonoverlap, comparisons, daily, phase_info = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    metric_difference = 0.0
    for phase, (start, end) in spec["stages"].items():
        table = pd.read_csv(ROOT / cfg["data"], nrows=end)
        labels = table[[p + "/mm" for p in POINTS]].to_numpy(float)
        dates = pd.to_datetime(table.Date).to_numpy()
        forecasts = {name: read_npz(run / phase / (name + ".npz")) for name in MODELS}
        scores, phase_rows, point_rows = {}, [], []
        for name, saved in forecasts.items():
            np.testing.assert_array_equal(saved["origins"], np.arange(start, end))
            for h in range(1, 31):
                ids = saved["origins"] + h - 1
                mask = ids < end
                value = losses(
                    labels[ids[mask]],
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
                all_rows.append(row)
                phase_rows.append(row)
                if h != 30:
                    continue
                scores[name] = value
                nonoverlap.append(
                    dict(
                        phase=phase,
                        model=name,
                        horizon=h,
                        n=len(np.arange(0, mask.sum(), h)),
                        **statistics(value, np.arange(0, mask.sum(), h)),
                    )
                )
                for p, point in enumerate(POINTS):
                    pr = dict(
                        phase=phase,
                        model=name,
                        point=point,
                        horizon=h,
                        n=int(mask.sum()),
                        **statistics({k: v[:, p : p + 1] for k, v in value.items()}),
                    )
                    points.append(pr)
                    point_rows.append(pr)
                    for i in np.flatnonzero(mask):
                        mu, sd = saved["mean"][i, h - 1, p], saved["sigma"][i, h - 1, p]
                        daily.append(
                            dict(
                                phase=phase,
                                model=name,
                                point=point,
                                origin=int(saved["origins"][i]),
                                target_date=str(dates[ids[i]])[:10],
                                observed=labels[ids[i], p],
                                mean=mu,
                                sigma=sd,
                                lower90=mu - ndtri(0.95) * sd,
                                upper90=mu + ndtri(0.95) * sd,
                            )
                        )
        stored = pd.read_csv(run / phase / "summary.csv").set_index(
            ["model", "horizon"]
        )
        for row in phase_rows:
            for metric in METRICS:
                expected = stored.loc[(row["model"], row["horizon"]), metric]
                np.testing.assert_allclose(row[metric], expected, atol=1e-8, rtol=5e-11)
                metric_difference = max(metric_difference, abs(row[metric] - expected))
        independently_check_decision(
            run / phase, phase_rows, point_rows, spec, "DRIFT1"
        )
        boot = cfg["bootstrap"]
        n = end - start - 29
        indices = circular_blocks(
            n, boot["block_length"], boot["replicates"], boot["seed"]
        )
        pairs = [
            (MODELS[0], MODELS[2]),
            (MODELS[0], MODELS[3]),
            (MODELS[0], MODELS[1]),
            (MODELS[1], MODELS[2]),
            (MODELS[1], MODELS[3]),
            (MODELS[0], MODELS[4]),
            (MODELS[1], MODELS[5]),
        ]
        observed = {name: statistics(value) for name, value in scores.items()}
        sampled = {name: statistics(value, indices) for name, value in scores.items()}
        for candidate, reference in pairs:
            for metric in METRICS:
                delta = sampled[candidate][metric] - sampled[reference][metric]
                low, high = np.quantile(
                    delta, [(1 - boot["confidence"]) / 2, (1 + boot["confidence"]) / 2]
                )
                comparisons.append(
                    dict(
                        phase=phase,
                        candidate=candidate,
                        reference=reference,
                        metric=metric,
                        difference=observed[candidate][metric]
                        - observed[reference][metric],
                        lower=low,
                        upper=high,
                        n=n,
                        **boot,
                    )
                )
        figures(phase, forecasts, dates, labels, pd.DataFrame(phase_rows), out, align)
        phase_info.append(
            dict(
                phase=phase,
                train=start,
                end=end,
                n_per_point=n,
                first_target=str(dates[start + 29])[:10],
                last_target=str(dates[end - 1])[:10],
            )
        )
    for name, rows in [
        ("c8_horizons", all_rows),
        ("c8_points_h30", points),
        ("c8_nonoverlap", nonoverlap),
        ("c8_paired_bootstrap", comparisons),
        ("c8_daily_h30", daily),
    ]:
        pd.DataFrame(rows).to_csv(
            out / (name + ".csv"), index=False, float_format="%.12g"
        )
    return dict(
        phases=phase_info,
        maximum_saved_metric_difference=metric_difference,
        comparisons=len(comparisons),
        daily_point_rows=len(daily),
        independent_effect_decisions_checked=True,
    )


def main(args):
    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    remaining = (
        datetime.fromisoformat(cfg["deadline_utc"]) - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining <= 0:
        raise TimeoutError("Original budget exhausted")

    def timeout(_sig, _frame):
        raise TimeoutError("Read-only analysis deadline reached")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(int(min(1200, remaining)))
    if sha(ROOT / cfg["data"]) != cfg["data_sha256"]:
        raise ValueError("Changed observations")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(args.figure_tools).resolve()))
    from audit_panel_alignment import require_matplotlib_panel_alignment

    registry = collect_registry(cfg, out)
    summary = c8_analysis(cfg, out, require_matplotlib_panel_alignment)
    for source in [
        Path(__file__),
        Path(__file__).with_name("analyze_ootang_rolling.py"),
        Path(args.figure_tools) / "audit_panel_alignment.py",
        cfg_path,
    ]:
        shutil.copyfile(source, out / source.name)
    metadata = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        registry=registry,
        c8=summary,
        config_sha256=sha(cfg_path),
        data_sha256=cfg["data_sha256"],
        new_training=0,
        new_physics=0,
        original_selection_unchanged=True,
        c8_post_exposure=True,
        independent_transfer=False,
        figure_qa_pending=True,
    )
    (out / "analysis.json").write_text(json.dumps(metadata, indent=2) + "\n")
    files = {p.name: sha(p) for p in out.iterdir() if p.is_file()}
    (out / "artifact_manifest.json").write_text(
        json.dumps(dict(files=files), indent=2) + "\n"
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--figure-tools", required=True)
    main(parser.parse_args())
