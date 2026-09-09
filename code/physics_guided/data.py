"""Strict date/axis contracts; inference receives forcing and first-day origin only."""

from dataclasses import dataclass
import hashlib
import numpy as np
import pandas as pd

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DOMAINS = ("O3", "O2", "O1_up", "O1_down")
COUNTS = {"development": (792, 1168), "final": (1168, 1461)}


@dataclass(frozen=True)
class Drivers:
    dates: pd.DatetimeIndex
    forcing: np.ndarray
    y0: np.ndarray

    def prefix(self, n):
        return Drivers(self.dates[:n], self.forcing[:n].copy(), self.y0.copy())


def read_data(path):
    d = pd.read_csv(path)
    d = d.rename(
        columns={
            **{p + "/mm": p for p in POINTS},
            "Rainfall/mm": "Rainfall",
            "RWL/m": "RWL",
        }
    )
    dates = pd.DatetimeIndex(pd.to_datetime(d["Date"], errors="raise"))
    if not dates.equals(pd.date_range("2016-07-01", "2020-06-30", freq="D")):
        raise ValueError("Expected complete ordered 1461-day Ootang series")
    f = d[["Rainfall", "RWL"]].to_numpy(dtype=np.float64)
    y = d[list(POINTS)].to_numpy(dtype=np.float64)
    if not np.isfinite(f).all() or not np.isfinite(y).all() or (f[:, 0] < 0).any():
        raise ValueError("Nonfinite inputs or negative rainfall; no filling allowed")
    if (f[:, 1] < 130).any() or (f[:, 1] > 190).any():
        raise ValueError("RWL outside frozen lookup range")
    return Drivers(dates, f, y[0].copy()), y


def training_labels(y, stage):
    return y[: COUNTS[stage][0]].copy()


def phase_masks(dates, stage):
    train, end = COUNTS[stage]
    if len(dates) != end:
        raise ValueError("Stage dates must include the full expected prefix")
    t = np.arange(end)
    return {"train": (t >= 30) & (t < train), "prediction": t >= train}


def mask_sha(dates, mask):
    return hashlib.sha256(
        "\n".join(dates[mask].strftime("%Y-%m-%d")).encode()
    ).hexdigest()
