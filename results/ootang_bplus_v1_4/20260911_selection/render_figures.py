"""Readable axes for the frozen v1.4 curves; preserve the original exports."""

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


def render(base):
    target = base / "figures"
    target.mkdir(exist_ok=False)
    inputs = {
        name: hashlib.sha256((base / name).read_bytes()).hexdigest()
        for name in ("predictions.csv", "selection_locked.json")
    }
    data = pd.read_csv(base / "predictions.csv", parse_dates=["date"])
    selections = json.loads((base / "selection_locked.json").read_text())["selections"]
    for n in (432, 612):
        cutoff = pd.Timestamp("2016-07-01") + pd.Timedelta(days=n)
        fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), sharex=True)
        choice = selections[str(n)]
        for ax, station in zip(axes.flat, ("ATU1", "ATU5", "MJ3", "MJ1")):
            frame = data[
                (data.fit_days == n)
                & (data.station == station)
                & (data.date >= cutoff - pd.Timedelta(days=60))
            ]
            observed = (
                frame[["date", "observed_mm"]].drop_duplicates().sort_values("date")
            )
            ax.plot(
                observed.date,
                observed.observed_mm,
                color="#222222",
                lw=1.5,
                label="Observed",
            )
            for recipe, color in (("A", "#1b9e77"), ("B", "#d95f02")):
                tags = [
                    arm
                    for arm, value in (
                        ("T", choice["T"]),
                        ("V", choice["V"]["selected_recipe"]),
                    )
                    if recipe == value
                ]
                curve = frame[frame.recipe == recipe].sort_values("date")
                ax.plot(
                    curve.date,
                    curve.mean_mm,
                    color=color,
                    lw=1.7,
                    label=f"Recipe {recipe} ({', '.join(tags) or 'unselected'})",
                )
            ax.axvline(cutoff, color="#666666", ls="--", lw=1)
            ax.set(title=station, ylabel="Displacement (mm)")
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
        fig.suptitle(
            f"B+ v1.4 | {n}-day training | 180-day historical forecast\n"
            "T: training objective; V: internal temporal selection; dashed line: forecast start"
        )
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        for suffix in ("png", "pdf"):
            fig.savefig(
                target / f"selection_{n}.{suffix}", dpi=160, bbox_inches="tight"
            )
        plt.close(fig)
    for name, digest in inputs.items():
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == digest
    (target / "provenance.json").write_text(
        json.dumps(
            {
                "inputs_sha256": inputs,
                "change": "Two-month date labels and margins; identical saved curves and 60+180 day display.",
                "original_exports_preserved": True,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    render(Path(__file__).resolve().parent)
