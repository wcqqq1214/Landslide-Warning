"""Pinned specification and saved B+ teachers; no physical solver calls."""

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_sample_learning.core import POINTS, teacher_arrays
from .core import ORIGINS

CONFIG = ROOT / "config/ootang_bplus_origin_learning.v1_19.json"
CONFIG_SHA = "481ec70a85ca2d1b43bc3892b3b2f7bbe034fee4452fefd09cebc621ea6a0136"
PLAN_SHA = "968108f0631d1367a92eb9b5f992218ab6d6c6dccf9124690de4bac01a53bffa"
RECIPES = {252: "B", 342: "A", 432: "B", 612: "B"}


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered origin learning design changed")
    return spec


def source_guard(spec):
    source = ROOT / spec["diagnostic_source"]
    if sha(source / "artifact_manifest.json") != spec["diagnostic_index_sha256"]:
        raise ValueError("Frozen history diagnosis index differs")
    check_index(source)
    if (
        not read_json(source / "verification.json")["passed"]
        or read_json(source / "launcher.json")["exitcode"] != 0
    ):
        raise ValueError("History diagnosis did not pass")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    name = "docs/ootang_bplus_history_diagnostics_results.v1.18.md"
    protected[name] = sha(ROOT / name)
    check_hashes(protected)
    for key in ("history_learning_source", "physical_source", "teacher_source"):
        check_index(ROOT / spec[key])
    teachers = read_json(ROOT / spec["teacher_source"] / "teachers.json")
    if {int(k): v["recipe"] for k, v in teachers.items()} != RECIPES:
        raise ValueError("Frozen own-J0 teacher choices differ")
    return protected


def read_teachers(path, source, h):
    if h not in ORIGINS:
        raise ValueError("Unregistered observation prefix")
    frame = pd.read_csv(path, nrows=h + 180, usecols=["Date", "Rainfall/mm", "RWL/m"])
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    if not dates.equals(pd.date_range("2016-07-01", periods=h + 180)):
        raise ValueError("Driver dates differ")
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    y0 = pd.read_csv(path, nrows=1, usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)[0]
    if (
        not np.isfinite(forcing).all()
        or not np.isfinite(y0).all()
        or (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Invalid forcing or initial displacement")
    return {
        o: teacher_arrays(source, o, RECIPES[o], forcing[: o + 180], y0)
        for o in (*ORIGINS[h], h)
    }
