"""Recompute the state check and single-window selection from frozen artifacts."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

root = Path.cwd()
old = root / "results/ootang_bplus_v1_3/20260910_increment"
prior = root / "results/ootang_bplus_v1_2/20260910_diagnostics"
out = Path(__file__).resolve().parent
arrays = np.load(old / "fit_612/C1_B_final.npz")
plastic = arrays["plastic"][:612, 2]
scores = {}
for n in (432, 612):
    table = pd.read_csv(prior / f"fit_{n}/metrics.csv")
    scores[n] = {
        start: float(
            table[
                (table.candidate == start + "_continued")
                & (table.phase == "prediction")
                & (table.station == "four_point_mean")
            ].iloc[0].rmse_mm
        )
        for start in ("A", "B")
    }
chosen = min(("A", "B"), key=lambda start: scores[432][start])
record = json.loads((out / "state_and_selection_replay.json").read_text())
assert record["state"]["plastic_max_abs_mm"] == float(abs(plastic).max())
assert record["state"]["positive_plastic_increment_days"] == int(
    np.sum(np.diff(plastic) > 1e-12)
)
replay = record["replay"]
assert replay["inner_rmse_mm"] == scores[432]
assert replay["selected_recipe"] == chosen
assert replay["outer_selected_rmse_mm"] == scores[612][chosen]
assert replay["outer_training_selected_rmse_mm"] == scores[612]["B"]
print("state and existing-forecast replay verified; no fitting")
