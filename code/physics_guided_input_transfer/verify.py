"""Scalar-date inputs, scalar distances, independent selection and score audit."""

from datetime import datetime
import itertools
import math

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_balanced_origin.verify import independent_samples
from physics_guided_history_learning.verify import independent_windows
from physics_guided_origin_learning.support import read_teachers
from .workflow import CONFIG_SHA, PLAN_SHA, source_guard, specification


class Comparison:
    def __init__(self):
        self.count = 0
        self.maximum = 0.0

    def array(self, actual, expected):
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape:
            raise AssertionError("Array shape differs")
        if b.dtype.kind in "biu":
            np.testing.assert_array_equal(a, b)
        else:
            np.testing.assert_allclose(a, b, rtol=1e-10, atol=1e-10, equal_nan=True)
            mask = np.isfinite(a) & np.isfinite(b)
            if mask.any():
                self.maximum = max(self.maximum, float(np.max(abs(a[mask] - b[mask]))))
        self.count += a.size

    def row(self, actual, expected):
        if set(actual) != set(expected):
            raise AssertionError("Table column inventory differs")
        for key, value in expected.items():
            if isinstance(value, str):
                if actual[key] != value:
                    raise AssertionError(f"String field differs: {key}")
            elif isinstance(value, bool):
                if (
                    not isinstance(actual[key], (bool, np.bool_))
                    or actual[key] != value
                ):
                    raise AssertionError(f"Boolean field differs: {key}")
                self.count += 1
            elif isinstance(value, (int, np.integer)):
                if actual[key] != value:
                    raise AssertionError(f"Integer field differs: {key}")
                self.count += 1
            else:
                self.array(actual[key], value)


def scalar_squared(a, b, groups):
    diff = (a - b).tolist()
    values = [float(x) - float(y) for x, y in zip(a.ravel(), b.ravel())]
    full = math.fsum(v * v for v in values) / len(values)
    parts = []
    for lo, hi in groups.values():
        vals = [
            diff[t][c][0][p] for t in range(30) for c in range(lo, hi) for p in range(4)
        ]
        parts.append(math.fsum(v * v for v in vals) / len(vals))
    return full, parts


def reconstruct(h, strategy, spec):
    source = ROOT / spec["source_run"]
    _, _, labels = load_observations(source / "input.csv", h)
    teachers = read_teachers(source / "input.csv", ROOT / spec["physical_source"], h)
    scalers = read_json(source / f"scalers_{h}.json")
    table, x, payload = independent_samples(h, strategy, labels, teachers, scalers)
    mask = table.block.to_numpy() == 1
    origins = table.origin.to_numpy()[mask]
    targets = table.target.to_numpy()[mask]
    base, features = teachers[h]
    n = spec["horizons"][str(h)]
    query_x = independent_windows(
        base,
        features,
        labels,
        np.array([(h, t) for t in range(h, h + n)]),
        scalers["physical"],
        scalers["history"],
        "H",
    )
    original = read_json(source / f"constants_{h}.json")
    if (
        array_sha(x) != original["sample_hashes"][strategy]
        or array_sha(labels) != original["labels_sha256"]
    ):
        raise AssertionError("Independent original input reconstruction differs")
    with np.load(source / f"samples_{h}_{strategy}.npz", allow_pickle=False) as old:
        for key in payload:
            np.testing.assert_allclose(payload[key], old[key], rtol=1e-12, atol=1e-14)
    candidate_x = x[mask]
    edge_ids, distance, groups, chosen, refs = [], [], [], [], []
    reference_pairs = 0
    for lead in range(n):
        candidates = [
            i
            for i, (o, t) in enumerate(zip(origins, targets))
            if t - o == lead and o < h and t < h
        ]
        if not candidates:
            raise AssertionError("Missing past same-lead candidate")
        options = []
        for k in candidates:
            d, parts = scalar_squared(query_x[lead], candidate_x[k], spec["groups"])
            edge_ids.append([lead, k])
            distance.append(d)
            groups.append(parts)
            options.append((d, int(origins[k]), int(targets[k]), k))
        chosen.append(sorted(options)[0][3])
        past = sorted(
            math.sqrt(scalar_squared(candidate_x[a], candidate_x[b], spec["groups"])[0])
            for a, b in itertools.combinations(candidates, 2)
        )
        reference_pairs += len(past)
        if not past:
            refs.append([math.nan] * 3)
        else:
            middle = (past[(len(past) - 1) // 2] + past[len(past) // 2]) / 2
            refs.append([past[0], middle, past[-1]])
    with np.load(source / f"mean_{h}_{strategy}.npz", allow_pickle=False) as old:
        norm = np.array(
            [
                [
                    math.fsum(float(v) for v in old["means"][:, t, j]) / 3
                    for j in range(4)
                ]
                for t in range(h, h + n)
            ]
        )
    data = dict(
        origins=origins,
        targets=targets,
        candidate_correction=(payload["target"] - payload["base"])[mask],
        query_base=base[h : h + n],
        norm_mean=norm,
        edges=np.array(edge_ids, dtype=int),
        squared_distance=np.array(distance),
        group_mse=np.array(groups),
        chosen=np.array(chosen, dtype=int),
        past_reference=np.array(refs),
    )
    info = dict(
        query_sha256=array_sha(query_x),
        sample_sha256=array_sha(x),
        labels_sha256=array_sha(labels),
        input_values=x.size,
        reference_pairs=reference_pairs,
    )
    return data, info


def _sign(a, b):
    nonzero = abs(a) > 1e-6 and abs(b) > 1e-6
    return bool(nonzero), bool(nonzero and ((a > 0) == (b > 0)))


def verify_tables(out, prefixes, labels, spec, compare):
    counts = {}
    for name in ("edges", "queries", "metrics"):
        frame = pd.read_csv(out / f"{name}.csv")
        expected_keys = set()
        for (h, strategy), d in prefixes.items():
            n = len(d["chosen"])
            if name == "edges":
                expected_keys.update(
                    (h, strategy, int(lead), int(d["origins"][k]), st)
                    for lead, k in d["edges"]
                    for st in spec["point_order"]
                )
                keycols = [
                    "fit_days",
                    "strategy",
                    "lead",
                    "candidate_origin",
                    "station",
                ]
            elif name == "queries":
                expected_keys.update(
                    (h, strategy, lead, st)
                    for lead in range(n)
                    for st in spec["point_order"]
                )
                keycols = ["fit_days", "strategy", "lead", "station"]
            else:
                expected_keys.update(
                    (h, strategy, segment, st)
                    for segment, (lo, _) in spec["segments"].items()
                    if lo < n
                    for st in spec["point_order"]
                )
                keycols = ["fit_days", "strategy", "segment", "station"]
        actual_keys = list(frame[keycols].itertuples(index=False, name=None))
        if (
            len(actual_keys) != len(set(actual_keys))
            or set(actual_keys) != expected_keys
        ):
            raise AssertionError(f"{name} key coverage differs")
        for r in frame.to_dict("records"):
            h, strategy, st = r["fit_days"], r["strategy"], r["station"]
            j = spec["point_order"].index(st)
            d = prefixes[(h, strategy)]
            common = dict(fit_days=h, strategy=strategy)
            if name in ("edges", "queries"):
                lead = r["lead"]
                k = (
                    next(
                        k
                        for edge_lead, k in d["edges"]
                        if edge_lead == lead
                        and d["origins"][k] == r["candidate_origin"]
                    )
                    if name == "edges"
                    else d["chosen"][lead]
                )
                ei = next(
                    i for i, pair in enumerate(d["edges"]) if tuple(pair) == (lead, k)
                )
                demand = float(labels[h + lead, j] - d["query_base"][lead, j])
                correction = float(d["candidate_correction"][k, j])
                nz, same = _sign(demand, correction)
                expected = dict(
                    **common,
                    lead=lead,
                    target=h + lead,
                    station=st,
                    candidate_origin=int(d["origins"][k]),
                    candidate_target=int(d["targets"][k]),
                    distance=math.sqrt(d["squared_distance"][ei]),
                )
                if name == "edges":
                    expected.update(
                        {
                            f"mse_{g}": d["group_mse"][ei, gi]
                            for gi, g in enumerate(spec["groups"])
                        }
                    )
                    expected.update(
                        candidate_demand_mm=correction,
                        query_demand_mm=demand,
                        demand_difference_mm=demand - correction,
                    )
                else:
                    expected.update(
                        past_distance_min=d["past_reference"][lead, 0],
                        past_distance_median=d["past_reference"][lead, 1],
                        past_distance_max=d["past_reference"][lead, 2],
                        query_demand_mm=demand,
                        probe_correction_mm=correction,
                        norm_correction_mm=float(
                            d["norm_mean"][lead, j] - d["query_base"][lead, j]
                        ),
                    )
                expected.update(nonzero_pair=nz, same_direction=same)
            else:
                lo, hi = spec["segments"][r["segment"]]
                hi = min(hi, len(d["chosen"]))
                expected = dict(
                    **common, segment=r["segment"], station=st, days=hi - lo
                )
                values = []
                for lead in range(lo, hi):
                    truth = float(labels[h + lead, j] - d["query_base"][lead, j])
                    probe = float(d["candidate_correction"][d["chosen"][lead], j])
                    norm = float(d["norm_mean"][lead, j] - d["query_base"][lead, j])
                    values.append((truth, probe, norm))
                for model, position in (("P0", None), ("NORM", 2), ("PROBE", 1)):
                    errors = [
                        (v[position] if position is not None else 0) - v[0]
                        for v in values
                    ]
                    expected[f"rmse_{model}_mm"] = math.sqrt(
                        math.fsum(e * e for e in errors) / len(errors)
                    )
                    expected[f"mae_{model}_mm"] = math.fsum(
                        abs(e) for e in errors
                    ) / len(errors)
                flags = [_sign(v[0], v[1]) for v in values]
                total, same = sum(n for n, _ in flags), sum(s for _, s in flags)
                expected.update(
                    nonzero_pairs=total,
                    same_direction_days=same,
                    same_direction_fraction=same / total if total else math.nan,
                )
                for model in ("P0", "NORM"):
                    expected[f"forecast_improves_{model}"] = all(
                        expected[f"{m}_{model}_mm"] - expected[f"{m}_PROBE_mm"] > 1e-6
                        for m in ("rmse", "mae")
                    )
            compare.row(r, expected)
        counts[name] = len(frame)
    if counts != dict(
        edges=spec["point_edges"],
        queries=spec["point_queries"],
        metrics=spec["metric_rows"],
    ):
        raise AssertionError("Registered row totals differ")
    saved = pd.read_csv(ROOT / spec["source_run"] / "metrics.csv")
    current = pd.read_csv(out / "metrics.csv")
    for h in (432, 612):
        for strategy in spec["strategies"]:
            for st in spec["point_order"]:
                r = current[
                    (current.fit_days == h)
                    & (current.strategy == strategy)
                    & (current.station == st)
                    & (current.segment == "all")
                ].iloc[0]
                for group, tag in (("P0", "P0"), (strategy, "NORM")):
                    old = saved[
                        (saved.outer_days == h)
                        & (saved.strategy == group)
                        & (saved.station == st)
                        & (saved.part == "prediction")
                    ].iloc[0]
                    for metric in ("rmse", "mae"):
                        compare.array(r[f"{metric}_{tag}_mm"], old[f"{metric}_mm"])
    return counts


def verify(out, sealed=True):
    spec, compare = specification(), Comparison()
    if sealed:
        check_index(out)
        done = read_json(out / "completed.json")
        if (
            not done["execution_complete"]
            or not done["numerical_verification"]
            or not 0 < done["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original diagnostic did not complete")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Frozen diagnostic specification differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected inventory differs")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    lock = read_json(out / "selection_lock.json")
    check_hashes(lock["files"], out)
    expected_locked = {
        f"prefix_{h}_{s}.npz" for h in spec["horizons"] for s in spec["strategies"]
    } | {f"inputs_{h}.json" for h in spec["horizons"]}
    if set(lock["files"]) != expected_locked:
        raise ValueError("Probe lock inventory differs")
    prefixes, input_values, reference_pairs = {}, 0, 0
    previous = manifest["started_utc"]
    for text_h in spec["horizons"]:
        h = int(text_h)
        info = read_json(out / f"inputs_{h}.json")
        if (
            info["label_rows"] != h
            or info["fit_days"] != h
            or not datetime.fromisoformat(previous)
            <= datetime.fromisoformat(info["label_read_utc"])
            <= datetime.fromisoformat(info["locked_utc"])
            <= datetime.fromisoformat(lock["locked_utc"])
        ):
            raise ValueError("Prefix observation/lock ordering differs")
        previous = info["locked_utc"]
        for strategy in spec["strategies"]:
            d, evidence = reconstruct(h, strategy, spec)
            if (
                info["sample_hashes"][strategy] != evidence["sample_sha256"]
                or info["query_sha256"] != evidence["query_sha256"]
                or info["labels_sha256"] != evidence["labels_sha256"]
            ):
                raise ValueError("Reconstructed prefix input fingerprint differs")
            input_values += evidence["input_values"]
            reference_pairs += evidence["reference_pairs"]
            with np.load(
                out / f"prefix_{h}_{strategy}.npz", allow_pickle=False
            ) as stored:
                if set(stored.files) != set(d):
                    raise ValueError("Probe array inventory differs")
                for k in d:
                    compare.array(stored[k], d[k])
            if np.any(d["group_mse"][:, [2, 4]] != 0):
                raise ArithmeticError("Static or same-lead component is nonzero")
            compare.array(
                d["squared_distance"],
                np.sum(d["group_mse"] * np.array([2, 14, 4, 2, 1]), axis=1) / 23,
            )
            prefixes[(h, strategy)] = d
    source = ROOT / spec["source_run"]
    scoring = read_json(out / "scoring.json")
    _, _, labels = load_observations(source / "input.csv", 792)
    if (
        scoring["selection_lock_sha256"] != sha(out / "selection_lock.json")
        or scoring["labels_sha256"] != array_sha(labels)
        or scoring["label_rows"] != 792
        or datetime.fromisoformat(scoring["label_read_utc"])
        < datetime.fromisoformat(lock["locked_utc"])
    ):
        raise ValueError("Diagnostic scoring lock differs")
    counts = verify_tables(out, prefixes, labels, spec, compare)
    expected_execution = {
        k: spec[k]
        for k in (
            "neural_updates",
            "neural_forward_calls",
            "gradient_calls",
            "physical_forward_calls",
            "physical_optimizer_nfev",
            "regression_fits",
            "scaler_fits",
            "scale_fits",
        )
    }
    expected_execution.update(
        candidate_edges=sum(len(d["edges"]) for d in prefixes.values()),
        past_reference_pairs=reference_pairs,
    )
    if (
        expected_execution["candidate_edges"] != 1800
        or read_json(out / "execution.json") != expected_execution
    ):
        raise ValueError("Diagnostic execution budget differs")
    return dict(
        passed=True,
        **counts,
        input_fingerprint_values_checked=input_values,
        candidate_edges=1800,
        past_reference_pairs=reference_pairs,
        numeric_values_checked=compare.count,
        max_absolute_difference=compare.maximum,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        new_neural_updates=0,
        new_neural_forward_calls=0,
        new_physical_forwards=0,
        raw_observation_as_of_verified="unknown",
    )
