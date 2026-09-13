"""Prefix-local features; physical caches contain no forecast target labels."""

import json
import numpy as np
import pandas as pd

from rolling_probability.data import Teacher, example
from .common import ROOT, CALLS, save_json, sha, array_sha
from .physics import PhysicalBank


def observations(spec, end=None):
    df = pd.read_csv(ROOT / spec["data"], nrows=end, encoding="utf-8-sig")
    dates = pd.to_datetime(df.Date)
    if not np.array_equal(
        dates.to_numpy(), pd.date_range("2016-07-01", periods=len(df)).to_numpy()
    ):
        raise ValueError("Unexpected dates")
    y = df[[p + "/mm" for p in spec["points"]]].to_numpy(float)
    forcing = df[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    if not np.isfinite(y).all() or not np.isfinite(forcing).all():
        raise ValueError("Nonfinite published data")
    return y, forcing, df.Date.to_numpy(str)


def prepare(spec, recorder):
    out = ROOT / spec["output_root"] / "prepared"
    if out.exists():
        raise FileExistsError("Prepared inputs already exist; do not overwrite")
    y, forcing, dates = observations(spec)
    bank = PhysicalBank(forcing, y[0], spec["teacher_prefixes"])
    keys = [
        "x",
        "z",
        "anchor",
        "last_y",
        "drift",
        "b_raw",
        "oracle",
        "state",
        "force",
        "elastic",
        "coeff",
        "teacher",
        "origins",
        "history_sha",
    ]
    records = {k: [] for k in keys}
    max_prefix = 0.0
    for n in range(252, len(y)):
        recorder.guard()
        q, f, b, state, packed, err = bank.forecast(n)
        teacher = Teacher(
            q, b, f, state["moisture"], state["rain_head"], state["reservoir_head"]
        )
        x, z, means = example(y[:n], teacher, horizon=7, window=30)
        drift = y[n - 1] + np.arange(1, 8)[:, None] * (y[n - 1] - y[n - 2])
        oracle = np.full((7, 4), np.nan)
        valid = min(7, len(y) - n)
        actual = bank.actual[q][0]
        oracle[:valid] = y[n - 1] + actual[n : n + valid] - actual[n - 1]
        row = dict(
            x=x,
            z=z,
            anchor=means["B_ANCHOR"],
            last_y=y[n - 1],
            drift=drift,
            b_raw=means["B_RAW"],
            oracle=oracle,
            state=packed[n - 1 : n + 7],
            force=state["force"][n : n + 7],
            elastic=state["rain_head"][n : n + 7] * bank.theta[q][[44, 45, 46, 46]],
            coeff=bank.coeff[q],
            teacher=q,
            origins=n,
            history_sha=array_sha(y[:n]),
        )
        for k, v in row.items():
            records[k].append(v)
        max_prefix = max(max_prefix, err)
        if n % 100 == 0:
            recorder.event("physical_cache_progress", origin=n, calls=CALLS.copy())
    out.mkdir(parents=True)
    np.savez_compressed(
        out / "inputs.npz",
        **{k: np.asarray(v) for k, v in records.items()},
        obs=bank.obs,
        dates=dates,
    )
    save_json(
        out / "receipt.json",
        dict(
            rows=len(records["origins"]),
            min_origin=252,
            max_origin=len(y) - 1,
            target_labels_saved=False,
            calls=CALLS.copy(),
            max_historical_state_difference=max_prefix,
            input_sha256=sha(out / "inputs.npz"),
            semantics="each x uses y[:origin]; future physical inputs use fixed past-only scenario",
        ),
    )
    recorder.event(
        "physical_cache_locked", rows=len(records["origins"]), calls=CALLS.copy()
    )


def load_cache(spec):
    path = ROOT / spec["output_root"] / "prepared"
    receipt = json.loads((path / "receipt.json").read_text())
    if sha(path / "inputs.npz") != receipt["input_sha256"]:
        raise ValueError("Prepared cache changed")
    with np.load(path / "inputs.npz") as a:
        return {k: a[k].copy() for k in a.files}


def query(cache, origins):
    origins = np.asarray(origins, int)
    ids = origins - 252
    if (ids < 0).any() or (ids >= len(cache["origins"])).any():
        raise ValueError("Origin outside cache")
    if not np.array_equal(cache["origins"][ids], origins):
        raise ValueError("Cache origins mismatched")
    return {k: v[ids] if k not in ("obs", "dates") else v for k, v in cache.items()}


def training(cache, spec, start):
    labels, _, _ = observations(spec, start)
    origins = np.arange(252, start - 7 + 1)
    data = query(cache, origins)
    data["target"] = np.stack([labels[n : n + 7] for n in origins])
    if (data["teacher"] > origins).any():
        raise ValueError("Future physical teacher in training")
    return data


def ridge_features(data):
    # Reuse the exact origin-local history rows, including back-referenced y values.
    x = data["x"]
    v1 = x[:, -1, 0]
    # x channel 9 is y[t]-y[last], so n-4 to n-1 gives a three-day velocity.
    v3 = -x[:, -4, 9] / 3
    v7, v14, v30 = [x[:, -1, i] for i in (3, 4, 5)]
    H = np.arange(1, 8)[None, :, None]

    def repeat(a):
        return np.broadcast_to(a[:, None, :], (len(x), 7, 4))

    vals = [
        repeat(v) for v in (v1, v3, v7, v14, v30, v1 - v3, v1 - v7, v7 - v14, v14 - v30)
    ]
    vals += [
        data["anchor"] - data["last_y"][:, None, :],
        repeat(x[:, -1, 1]),
        repeat(v14 - x[:, -1, 7]),
        repeat(x[:, -1, 14]),
        repeat(x[:, -1, 12]),
        repeat(x[:, -1, 13]),
        np.broadcast_to(H / 7, (len(x), 7, 4)),
    ]
    return np.stack(vals, axis=-1)
