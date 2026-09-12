"""Prefix-only physical teachers and origin-local observation features."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, prepare, save_json, sha

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
EARLY = ROOT / "results/ootang_bplus_v1_4/20260911_selection"
SOURCES = {
    252: EARLY / "inner_432_252_B.npz",
    342: EARLY / "inner_612_342_A.npz",
    432: EARLY / "outer_432_B.npz",
    612: EARLY / "outer_612_B.npz",
    792: ROOT
    / "results/ootang_probability_pinn_v2_3/20260912_development/reference.npz",
}
BASELINES = ("B_RAW", "B_ANCHOR", "B_TREND14", "PERSIST", "DRIFT14")


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def read_prefix(path, n):
    frame = pd.read_csv(path, nrows=n, usecols=["Date"] + [p + "/mm" for p in POINTS])
    dates = pd.to_datetime(frame.Date)
    if len(frame) != n or not dates.equals(
        pd.Series(pd.date_range("2016-07-01", periods=n))
    ):
        raise ValueError("Unexpected observation prefix dates")
    values = frame[[p + "/mm" for p in POINTS]].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite observations; no filling permitted")
    return values


class ObservationStream:
    """Never hand a forecaster an observation beyond its current origin."""

    def __init__(self, path, start, end):
        self.path, self.end = Path(path), end
        self.history = read_prefix(path, start)
        self.reader = pd.read_csv(
            path,
            skiprows=range(1, start + 1),
            nrows=end - start,
            usecols=["Date"] + [p + "/mm" for p in POINTS],
            chunksize=1,
        )
        self.next_index = start

    def release(self):
        if self.next_index >= self.end:
            raise StopIteration
        frame = next(self.reader)
        date = str(frame.Date.iloc[0])
        expected = (
            pd.Timestamp("2016-07-01") + pd.Timedelta(days=self.next_index)
        ).strftime("%Y-%m-%d")
        if date != expected:
            raise ValueError("Observation release out of order")
        value = frame[[p + "/mm" for p in POINTS]].to_numpy(float)
        if not np.isfinite(value).all():
            raise ValueError("Nonfinite released observation")
        self.history = np.concatenate([self.history, value])
        self.next_index += 1
        if self.next_index == self.end:
            self.reader.close()
        return value[0].copy()


@dataclass
class Teacher:
    prefix: int
    mean: np.ndarray
    forcing: np.ndarray
    moisture: np.ndarray
    rain_head: np.ndarray
    reservoir_head: np.ndarray


def load_teachers(directory):
    provenance = json.loads((Path(directory) / "provenance.json").read_text())
    for name, expected in provenance["files"].items():
        if sha(Path(directory) / name) != expected:
            raise ValueError("Prepared physical teacher changed: " + name)
    teachers = {}
    for path in sorted(Path(directory).glob("teacher_*.npz")):
        with np.load(path) as a:
            q = int(a["prefix"])
            teachers[q] = Teacher(
                q,
                *[
                    a[k].copy()
                    for k in (
                        "mean",
                        "forcing",
                        "moisture",
                        "rain_head",
                        "reservoir_head",
                    )
                ],
            )
    if not teachers:
        raise ValueError("No prepared physical teachers")
    return teachers


def prepare_teachers(spec, out, end=1168):
    """Replay frozen prefix parameters; no optimization and no later displacement."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    data = ROOT / spec["data"]
    if sha(data) != spec["data_sha256"]:
        raise ValueError("Frozen observations changed")
    frame = pd.read_csv(data, nrows=end, usecols=["Date", "Rainfall/mm", "RWL/m"])
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    if len(forcing) != end or not np.isfinite(forcing).all():
        raise ValueError("Invalid forcing")
    y0 = read_prefix(data, 1)[0]
    ref = prepare(ROOT / "runtime/ootang_rolling_v3/reference")
    ctx = ref.Context(forcing)
    provenance, calls = {}, 0
    sources = dict(SOURCES)
    prior_audit_path = (
        ROOT / "results/ootang_residual_training_audit_20260913/audit.json"
    )
    prior_audit = json.loads(prior_audit_path.read_text())
    for q in (252, 342, 432, 612):
        source = sources[q]
        name = str(source.relative_to(ROOT))
        if sha(source) != prior_audit["source_sha256"][name]:
            raise ValueError("Earlier verified physical teacher changed: " + name)
    if end == 1461:
        sources[1168] = ref.ROOT / "results/calibrated.json"
    for q, source in sources.items():
        if source.suffix == ".npz":
            with np.load(source) as a:
                theta = a["theta"].copy()
                old = a["mean"].copy()
        else:
            cfg = json.loads(source.read_text())
            theta = np.array(cfg["theta"], dtype=float)
            with np.load(
                ROOT
                / "results/ootang_bplus_v1_1/20260910_implementation/frozen_reference.npz"
            ) as a:
                old = a["mu"].copy()
        if theta.shape != (54,) or not np.isfinite(theta).all():
            raise ValueError("Invalid frozen physical parameters")
        mu, states = ref.forward(theta, ctx, states=True)
        calls += 1
        mu = mu + y0
        m = min(len(old), len(mu))
        difference = float(np.max(np.abs(mu[:m] - old[:m])))
        if difference > 1e-8:
            raise ValueError(f"Physical replay changed for prefix {q}: {difference}")
        # An independent shorter history must exactly retain the earlier trajectory.
        short = ref.forward(theta, ref.Context(forcing[:q])) + y0
        calls += 1
        prefix_difference = float(np.max(abs(short - mu[:q])))
        if prefix_difference > 1e-8:
            raise ValueError("Physical preprocessing depends on future forcing")
        np.savez_compressed(
            out / f"teacher_{q}.npz",
            prefix=q,
            mean=mu,
            theta=theta,
            forcing=forcing,
            y0=y0,
            **{k: states[k] for k in ("moisture", "rain_head", "reservoir_head")},
        )
        provenance[str(q)] = dict(
            source=str(source.relative_to(ROOT)),
            source_sha256=sha(source),
            theta_sha256=array_sha(theta),
            source_rows=len(old),
            replay_rows=end,
            max_replay_difference_mm=difference,
            max_prefix_difference_mm=prefix_difference,
            fit_label_last_index=q - 1,
        )
        if q < 792:
            provenance[str(q)]["prior_parameter_audit"] = next(
                row for row in prior_audit["teacher_rows"] if row["prefix"] == q
            )
    receipt = dict(
        teachers=provenance,
        physical_forward_calls=calls,
        new_parameter_fits=0,
        displacement_prefix_parsed=1,
        forcing_rows=end,
        data_sha256=sha(data),
        earlier_teacher_audit_sha256=sha(prior_audit_path),
        files={p.name: sha(p) for p in out.glob("*.npz")},
    )
    save_json(out / "provenance.json", receipt)
    return receipt


def select_teacher(pool, origin):
    eligible = [q for q in pool if q <= origin]
    if not eligible:
        raise ValueError("No teacher calibrated before this origin")
    return pool[max(eligible)]


def averages(x, width):
    # Difference of cumulative levels over a backward-looking window.
    y = np.zeros_like(x)
    y[width:] = (x[width:] - x[:-width]) / width
    return y


def example(history, teacher, horizon=30, window=30):
    n = len(history)
    if n < 2 * window or teacher.prefix > n or n >= len(teacher.mean):
        raise ValueError("Invalid causal origin")
    H = min(horizon, len(teacher.mean) - n)
    b, f = teacher.mean, teacher.forcing
    y = np.asarray(history, dtype=float)
    if y.shape != (n, 4):
        raise ValueError("Four point observation history required")
    dy = np.diff(y, axis=0, prepend=y[:1])
    db = np.diff(b[:n], axis=0, prepend=b[:1])
    r = y - b[:n]
    t = np.arange(n - window, n)

    def scalar(v):
        return np.repeat(np.asarray(v)[..., None], 4, axis=-1)

    day = t + pd.Timestamp("2016-07-01").dayofyear - 1
    features = [dy[t], db[t], (dy - db)[t]]
    features += [averages(y, w)[t] for w in (7, 14, 30)]
    features += [averages(r, w)[t] for w in (7, 14, 30)]
    features += [y[t] - y[-1], b[t] - b[n - 1]]
    rain = f[:n, 0]
    csum = np.r_[0.0, np.cumsum(rain)]
    features += [
        scalar(f[t, 0]),
        scalar(f[t, 1]),
        scalar(f[t, 1] - f[t - 1, 1]),
        scalar((csum[t + 1] - csum[t - 6]) / 7),
        scalar((csum[t + 1] - csum[t - 29]) / 30),
        scalar(teacher.moisture[t].mean(axis=1)),
        scalar(teacher.rain_head[t].mean(axis=1)),
        scalar(teacher.reservoir_head[t] - f[t, 1]),
        scalar(np.sin(2 * np.pi * day / 365.25)),
        scalar(np.cos(2 * np.pi * day / 365.25)),
    ]
    features += [np.repeat(np.eye(4)[j][None], window, axis=0) for j in range(4)]
    x = np.stack(features, axis=1)
    h = np.arange(1, H + 1, dtype=float)
    future_ids = np.arange(n, n + H)
    increments = b[future_ids] - b[n - 1]
    z = np.stack(
        [
            increments / h[:, None],
            b[future_ids] - b[future_ids - 1],
            scalar(f[future_ids, 0]),
            scalar(f[future_ids, 1]),
            scalar(teacher.moisture[future_ids].mean(axis=1)),
            scalar(teacher.rain_head[future_ids].mean(axis=1)),
            scalar(teacher.reservoir_head[future_ids] - f[future_ids, 1]),
            scalar(h / horizon),
        ],
        axis=1,
    )
    anchor = y[-1] + increments
    means = dict(
        B_RAW=b[future_ids].copy(),
        B_ANCHOR=anchor,
        B_TREND14=anchor
        + h[:, None] * ((y[-1] - y[-15]) - (b[n - 1] - b[n - 15])) / 14,
        PERSIST=np.broadcast_to(y[-1], (H, 4)).copy(),
        DRIFT14=y[-1] + h[:, None] * (y[-1] - y[-15]) / 14,
    )
    if not all(np.isfinite(v).all() for v in [x, z, *means.values()]):
        raise ArithmeticError("Invalid origin features")
    return x, z, means


def training_examples(labels, pool, horizon=30, window=30):
    xs, zs, ys, anchors, origins, teachers = [], [], [], [], [], []
    bases = {k: [] for k in BASELINES}
    for n in range(min(pool), len(labels) - horizon + 1):
        teacher = select_teacher(pool, n)
        x, z, means = example(labels[:n], teacher, horizon, window)
        if len(z) != horizon:
            raise ValueError("Teacher lacks the required future forcing trajectory")
        xs.append(x)
        zs.append(z)
        ys.append(labels[n : n + horizon])
        anchors.append(means["B_ANCHOR"])
        origins.append(n)
        teachers.append(teacher.prefix)
        for key in bases:
            bases[key].append(means[key])
    return dict(
        x=np.stack(xs),
        z=np.stack(zs),
        y=np.stack(ys),
        anchor=np.stack(anchors),
        origins=np.array(origins),
        teachers=np.array(teachers),
        baselines={k: np.stack(v) for k, v in bases.items()},
    )
