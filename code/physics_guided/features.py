"""Frozen training-only scalers, distinct point and physical-domain axes."""

from dataclasses import dataclass
import numpy as np
import pandas as pd
from .mechanics import tensor

M1_NAMES = (
    ["u", "du", "P", "R", "dR", "P7", "P30", "H"]
    + [f"h_{j}" for j in range(4)]
    + [f"m_{j}" for j in range(4)]
    + ["x", "O3", "O2", "O1"]
)
M2_NAMES = ["P", "R", "dR", "P7", "P30", "H"] + [
    f"{v}_{j}"
    for v in ["h", "m", "z", "p", "rb_L", "rc_L", "rE_L", "b"]
    for j in range(4)
]


@dataclass
class Scaler:
    mean: np.ndarray
    scale: np.ndarray
    floor: np.ndarray

    @classmethod
    def fit(cls, x, floor, static=0):
        mean, std = x.mean(axis=0), x.std(axis=0, ddof=0)
        scale = np.maximum(std, floor)
        if static:
            mean[-static:] = 0
            scale[-static:] = 1
        return cls(mean, scale, np.broadcast_to(floor, mean.shape).copy())

    def transform(self, x):
        return (x - self.mean) / self.scale

    def torch_transform(self, x):
        return (x - tensor(self.mean)) / tensor(self.scale)

    def record(self):
        return dict(
            mean=self.mean.tolist(),
            scale=self.scale.tolist(),
            floor=self.floor.tolist(),
        )


def public_features(drivers, states):
    p, r = drivers.forcing.T
    return np.column_stack(
        [
            p,
            r,
            np.r_[0, np.diff(r)],
            pd.Series(p).rolling(7, min_periods=1).sum(),
            pd.Series(p).rolling(30, min_periods=1).sum(),
            states["reservoir_head"],
        ]
    )


def point_features(drivers, mechanics, u=None):
    s = mechanics.reference
    u = mechanics.u if u is None else u
    n = len(u)
    x = np.empty((n, 20, 4))
    x[:, 0] = u
    x[:, 1] = np.vstack([np.zeros(4), np.diff(u, axis=0)])
    x[:, 2:8] = public_features(drivers, s)[:, :, None]
    x[:, 8:12] = s["rain_head"][:, :, None]
    x[:, 12:16] = s["moisture"][:, :, None]
    x[:, 16] = (np.array([295, 1000, 1250, 1680]) - 295) / (1680 - 295)
    x[:, 17:] = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 1]])
    return x


def dynamic_reference(drivers, mechanics):
    s, le = mechanics.reference, mechanics.ctx.length
    previous = np.column_stack(
        [
            s["coordinates"] - s["background"],
            s["plastic"],
            s["basal_reaction"] / le,
            s["contact"] / le,
            s["bulk_reaction"] / le,
            s["background"],
        ]
    )
    previous = np.vstack([np.zeros(24), previous[:-1]])
    return np.column_stack(
        [public_features(drivers, s), s["rain_head"], s["moisture"], previous]
    )


def fit_scalers(drivers, mechanics, fit_days):
    m1_floor = np.array([1, 0.01, 1, 0.1, 0.1, 1, 1, 0.1] + [0.01] * 8 + [1] * 4)[
        :, None
    ]
    m2_floor = np.array(
        [1, 0.1, 0.1, 1, 1, 0.1] + [0.01] * 8 + [1] * 8 + [0.01] * 12 + [1] * 4
    )
    x = point_features(drivers, mechanics)
    dynamic = dynamic_reference(drivers, mechanics)
    return Scaler.fit(x[:fit_days], m1_floor, static=4), Scaler.fit(
        dynamic[:fit_days], m2_floor
    )
