"""Read frozen arrays, verify scale inheritance and preselected time boundaries.

No model imports, fitting, forecasting, physical calls or metric recomputation.
"""

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtri

from audit_ootang_frozen_sources import ROOT, BASE, OUT, START, DEADLINE, MODELS, sha


POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
STAGES = {"development": (792, 1168), "later_exploratory": (1168, 1461)}


def array_sha(value):
    value = np.ascontiguousarray(value)
    return hashlib.sha256(
        str((value.shape, str(value.dtype))).encode() + value.tobytes()
    ).hexdigest()


def day(index):
    return (datetime(2016, 7, 1) + timedelta(days=int(index))).date().isoformat()


def main():
    if datetime.now(timezone.utc) >= datetime.fromisoformat(DEADLINE):
        raise RuntimeError("Frozen validation budget expired")
    destinations = [
        OUT / n
        for n in (
            "array_evidence.json",
            "origin_ledger.csv",
            "boundary_checks.csv",
        )
    ]
    if any(p.exists() for p in destinations):
        raise FileExistsError("Preserve previous audit evidence")
    source_receipt = json.loads((OUT / "sources_audit.json").read_text())
    if not source_receipt["passed"]:
        raise ValueError("Source audit did not pass")
    known = {r["path"]: r["expected"] for r in source_receipt["checks"]}
    used, loaded = {}, set()

    def checked(path):
        rel = str(path.relative_to(ROOT))
        value = sha(path)
        if rel not in known or value != known[rel]:
            raise ValueError(f"Source absent from audit or changed: {rel}")
        used[rel] = value
        return path

    def arrays(path):
        checked(path)
        loaded.add(str(path.relative_to(ROOT)))
        with np.load(path, allow_pickle=False) as data:
            return {k: data[k].copy() for k in data.files}

    def read_json(path):
        return json.loads(checked(path).read_text())

    maxima = {}

    def close(name, got, expected):
        np.testing.assert_allclose(got, expected, atol=1e-8, rtol=5e-11, equal_nan=True)
        error = np.abs(np.asarray(got) - expected)
        value = float(np.nanmax(error)) if np.size(error) else 0.0
        maxima[name] = max(maxima.get(name, 0.0), value)

    labels_path = checked(ROOT / "data/monitoring_data.csv")
    with labels_path.open(encoding="utf-8-sig") as handle:
        frame = list(csv.DictReader(handle))
    y = pd.read_csv(labels_path, usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)
    assert len(y) == 1461 and np.isfinite(y).all()
    assert all(row["Date"] == day(i) for i, row in enumerate(frame))
    histories = {}
    for run in ("c8_online", "c16_fast_feedback_run3", "c18_information"):
        events = [
            json.loads(s)
            for s in checked(BASE / run / "events.jsonl").read_text().splitlines()
        ]
        histories[run] = events

    ledger, boundary, phase_results = [], [], {}
    for phase, (start, end) in STAGES.items():
        nrows = end - start
        origin = np.arange(start, end)
        valid_mask = np.arange(nrows)[:, None] + np.arange(30)[None] < nrows
        c8root, c16root, c18root = [
            BASE / run / phase
            for run in (
                "c8_online",
                "c16_fast_feedback_run3",
                "c18_information",
            )
        ]
        core = arrays(c8root / "C8_ONLINE_DATA.npz")
        c8state = arrays(c8root / "online_state_DATA.npz")
        features = arrays(c8root / "issued_features.npz")
        normal = read_json(c16root / "normalization.json")
        units = np.asarray(normal["response_scales"])
        np.testing.assert_array_equal(units, core["sigma"][0])
        np.testing.assert_array_equal(features["origins"], origin)
        records = {name: arrays(c18root / f"{name}.npz") for name in MODELS}
        original, learned, info = {}, {}, {}
        for arm in ("PHYS", "CORE"):
            original[arm] = arrays(c16root / f"C16_FAST_{arm}.npz")
            learned[arm] = arrays(c16root / f"C16_FAST_{arm}_learning.npz")
            info[arm] = arrays(c18root / f"C18_{arm}_information.npz")["matrix"]
            old, new = original[arm], records[f"C18_{arm}"]
            for key in ("mean", "raw_sigma", "origins", "teacher_prefixes"):
                np.testing.assert_array_equal(new[key], old[key])
            np.testing.assert_array_equal(old["sigma"], core["sigma"])
            np.testing.assert_array_equal(old["raw_sigma"], core["raw_sigma"])
            np.testing.assert_array_equal(old["core_mean"], core["mean"])
            np.testing.assert_array_equal(
                old["core_feedback_log_scale"], core["feedback_log_scale"]
            )
            np.testing.assert_array_equal(new["core_sigma"], core["sigma"])
            close(
                "sigma_formula_mm",
                new["sigma"][valid_mask],
                np.hypot(
                    core["sigma"][valid_mask],
                    np.sqrt(new["information_variance"][valid_mask]),
                ),
            )
            np.testing.assert_array_equal(new["sigma"][0], core["sigma"][0])
        np.testing.assert_array_equal(
            records["C16_FAST_PHYS"]["mean"], original["PHYS"]["mean"]
        )
        for name in ("B_ANCHOR", "DRIFT1"):
            prior = arrays(c8root / f"{name}.npz")
            for key in prior:
                np.testing.assert_array_equal(records[name][key], prior[key])
        for pred in records.values():
            np.testing.assert_array_equal(pred["origins"], origin)
            np.testing.assert_array_equal(
                pred["teacher_prefixes"], np.full(nrows, start)
            )
            assert np.isfinite(pred["mean"][valid_mask]).all()
            assert np.isfinite(pred["sigma"][valid_mask]).all()
            assert (pred["sigma"][valid_mask] > 0).all()

        # The history-only four-feature core is checked against observed trends.
        for i, n in enumerate(origin):
            H = min(30, end - n)
            h = np.arange(1, H + 1)[:, None]
            d = {
                w: y[n - 1] + h * (y[n - 1] - y[n - 1 - w]) / w
                for w in (1, 3, 7, 14, 30)
            }
            truth = np.stack(
                [d[1] - d[3], d[1] - d[7], d[7] - d[14], d[14] - d[30]], axis=-1
            )
            close(
                "data_only_trend_features_mm", features["features"][i, :H, :, :4], truth
            )
            close("drift1_mean_mm", records["DRIFT1"]["mean"][i, :H], d[1])

        maps = {}
        for run, events in histories.items():
            locks = {
                int(e["origin"]): (j, e)
                for j, e in enumerate(events)
                if e["kind"] == "forecast_locked" and e["phase"] == phase
            }
            releases = {
                int(e["index"]): (j, e)
                for j, e in enumerate(events)
                if e["kind"] == "observation_released" and e["phase"] == phase
            }
            assert set(locks) == set(origin)
            maps[run] = locks, releases

        teacher_dir = BASE / (
            "physics_development" if phase == "development" else "physics_transfer"
        )
        provenance = read_json(teacher_dir / "provenance.json")
        teacher = provenance["teachers"][str(start)]
        assert teacher["fit_label_last_index"] == start - 1
        for i, n in enumerate(origin):
            H = min(30, end - n)
            last = int(n - 1)
            for run in ("c8_online", "c16_fast_feedback_run3"):
                locks, releases = maps[run]
                order, lock = locks[int(n)]
                release_order, release = releases[int(n)]
                assert order < release_order
                assert lock["history_sha256"] == array_sha(y[:n])
                assert release["value_sha256"] == array_sha(y[n])
                if i:
                    assert releases[int(n - 1)][0] < order
            c8lock = maps["c8_online"][0][int(n)][1]
            c16lock = maps["c16_fast_feedback_run3"][0][int(n)][1]
            c18lock = maps["c18_information"][0][int(n)][1]
            assert c8lock["beta_sha256"]["DATA"] == array_sha(c8state["beta_issued"][i])
            predicted = {}
            for arm in ("PHYS", "CORE"):
                old_name, new_name = f"C16_FAST_{arm}", f"C18_{arm}"
                state = learned[arm]
                fields = c16lock["inputs"][old_name]
                for key in ("features", "available"):
                    assert fields[key] == array_sha(state[key][i, :H])
                assert fields["beta"] == array_sha(state["beta"][i])
                assert c16lock["predictions"][old_name] == array_sha(
                    np.stack(
                        [
                            original[arm][key][i, :H]
                            for key in ("mean", "sigma", "raw_sigma")
                        ]
                    )
                )
                assert c18lock["predictions"][new_name] == array_sha(
                    np.stack(
                        [records[new_name][key][i, :H] for key in ("mean", "sigma")]
                    )
                )
                predicted[new_name] = c18lock["predictions"][new_name]
            for name in ("DRIFT1", "B_ANCHOR"):
                assert c8lock["predictions"][name] == array_sha(
                    np.stack(
                        [
                            records[name][key][i, :H]
                            for key in ("mean", "sigma", "raw_sigma")
                        ]
                    )
                )
                predicted[name] = c8lock["predictions"][name]
            predicted["C16_FAST_PHYS"] = c16lock["predictions"]["C16_FAST_PHYS"]
            ledger.append(
                dict(
                    phase=phase,
                    origin=int(n),
                    origin_date=day(n),
                    last_visible_index=last,
                    last_visible_date=day(last),
                    observation_available_at="unknown; assumed before origin",
                    valid_horizons=H,
                    first_target_date=day(n),
                    last_target_date=day(n + H - 1),
                    teacher_prefix=start,
                    teacher_parameter_sha256=teacher["theta_sha256"],
                    data_core_or_drift1_forcing_last_index="not used",
                    phys_feedback_forcing_last_index=last if i else "no feedback yet",
                    b_anchor_forcing_last_index=int(n + H - 1),
                    new_mature_error_head_counts_by_h=json.dumps(
                        [max(0, i - h) for h in range(1, H + 1)]
                    ),
                    mature_error_head_first_origin=start + 1 if i >= 2 else "none",
                    mature_target_upper_bound=last,
                    core_beta_sha256=c8lock["beta_sha256"]["DATA"],
                    phys_beta_sha256=c16lock["inputs"]["C16_FAST_PHYS"]["beta"],
                    core_feedback_beta_sha256=c16lock["inputs"]["C16_FAST_CORE"][
                        "beta"
                    ],
                    phys_information_sha256=array_sha(info["PHYS"][i, :H]),
                    core_information_sha256=array_sha(info["CORE"][i, :H]),
                    calibration_source="C8_ONLINE_DATA actual issued state; not C16 residuals",
                    raw_sigma_sha256=array_sha(core["raw_sigma"][i, :H]),
                    calibration_factor_sha256=array_sha(
                        core["calibration_factor"][i, :H]
                    ),
                    calibration_feedback_sha256=array_sha(
                        core["feedback_log_scale"][i, :H]
                    ),
                    normalization_sha256=sha(c16root / "normalization.json"),
                    locked_prediction_hashes=json.dumps(predicted, sort_keys=True),
                    c16_lock_event=maps["c16_fast_feedback_run3"][0][int(n)][0],
                    c16_release_event=maps["c16_fast_feedback_run3"][1][int(n)][0],
                    causal_csv_order_checked=True,
                )
            )
        # Fixed boundary selection is independent of forecast errors.
        selected = [start, start + 1, start + 31, end - 30]
        for n in selected:
            i = n - start
            for h in (1, 7, 30):
                k = h - 1
                pool = np.arange(max(0, i - h + 1))
                log_scale = np.zeros(4)
                for j in pool:
                    error = y[start + j + k] - core["mean"][j, k]
                    miss = abs(error) > ndtri(0.95) * core["sigma"][j, k]
                    log_scale = np.clip(
                        log_scale + 0.02 * (miss - 0.1), -np.log(10), np.log(10)
                    )
                recent = pool[-90:]
                z2 = (
                    (y[start + recent + k] - core["mean"][recent, k])
                    / core["raw_sigma"][recent, k]
                ) ** 2
                factor = np.sqrt((10 + z2.sum(axis=0)) / (10 + len(recent))) * np.exp(
                    log_scale
                )
                close(
                    "calibration_log_scale", core["feedback_log_scale"][i, k], log_scale
                )
                close("calibration_factor", core["calibration_factor"][i, k], factor)
                close(
                    "calibration_sigma_mm",
                    core["sigma"][i, k],
                    np.maximum(0.01, core["raw_sigma"][i, k] * factor),
                )
                for arm in ("PHYS", "CORE"):
                    state = learned[arm]
                    refs = [core, records["B_ANCHOR"]] if arm == "PHYS" else [core]
                    names = (
                        ["C8_ONLINE_DATA", "B_ANCHOR"]
                        if arm == "PHYS"
                        else ["C8_ONLINE_DATA"]
                    )
                    x = (
                        np.stack(
                            [
                                (y[n - 1] - ref["mean"][i - 1, 0])
                                / np.asarray(normal["input_scales"][name])[k]
                                for ref, name in zip(refs, names)
                            ],
                            axis=-1,
                        )
                        if i
                        else np.zeros((4, len(refs)))
                    )
                    close("feedback_input_dimensionless", state["features"][i, k], x)
                    assert bool(state["available"][i, k]) == (i > 0)
                    mature = np.flatnonzero(state["available"][: max(0, i - k), k])
                    assert all(start + int(j) + k < n for j in mature)
                    for p, point in enumerate(POINTS):
                        design = state["features"][mature, k, p]
                        matrix = np.eye(len(refs)) + np.einsum(
                            "ni,nj->ij", design, design, optimize=False
                        )
                        close(
                            "information_matrix_dimensionless",
                            info[arm][i, k, p],
                            matrix,
                        )
                        whitened = np.linalg.solve(np.linalg.cholesky(matrix), x[p])
                        v = units[k, p] ** 2 * np.dot(whitened, whitened)
                        pred = records[f"C18_{arm}"]
                        close(
                            "information_variance_mm2",
                            pred["information_variance"][i, k, p],
                            v,
                        )
                        close(
                            "mean_reconstruction_mm",
                            original[arm]["mean"][i, k, p],
                            core["mean"][i, k, p]
                            + units[k, p] * np.dot(x[p], state["beta"][i, k, p]),
                        )
                        boundary.append(
                            dict(
                                phase=phase,
                                origin=n,
                                horizon=h,
                                point=point,
                                arm=arm,
                                target_index=n + k,
                                last_visible_index=n - 1,
                                feedback_target_index=n - 1 if i else "none",
                                mature_count=len(mature),
                                mature_first_origin=start + int(mature[0])
                                if len(mature)
                                else "none",
                                mature_last_target=start + int(mature[-1]) + k
                                if len(mature)
                                else "none",
                                response_unit_mm=float(units[k, p]),
                                input_units_mm=json.dumps(
                                    [
                                        normal["input_scales"][name][k][p]
                                        for name in names
                                    ]
                                ),
                                mean_mm=float(pred["mean"][i, k, p]),
                                raw_sigma_mm=float(core["raw_sigma"][i, k, p]),
                                inherited_sigma_mm=float(core["sigma"][i, k, p]),
                                variance_added_mm2=float(v),
                                sigma_c18_mm=float(pred["sigma"][i, k, p]),
                                matrix_sha256=array_sha(info[arm][i, k, p]),
                                passed=True,
                            )
                        )
        phase_results[phase] = dict(
            all_origins=nrows,
            h30_origins=nrows - 29,
            boundary_origins=selected,
            boundary_dates=[day(n) for n in selected],
            exact_mean_and_scale_inheritance=True,
            input_units_mm={k: v["unit"] for k, v in normal["input_entries"].items()},
            response_h30_units_mm=normal["response_scales"][29],
        )
    for path, rows in zip(destinations[1:], (ledger, boundary)):
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    result = dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        start_utc=START,
        deadline_utc=DEADLINE,
        auditor_sha256=sha(Path(__file__)),
        source_receipt_sha256=sha(OUT / "sources_audit.json"),
        inputs=used,
        arrays_loaded=len(loaded),
        origin_rows=len(ledger),
        boundary_rows=len(boundary),
        phase_results=phase_results,
        maximum_differences=maxima,
        original_atol=1e-8,
        original_rtol=5e-11,
        new_training=0,
        new_physics=0,
        new_scores=0,
        coefficient_fits=0,
        verification_matrix_solves=len(boundary),
        files={p.name: sha(p) for p in destinations[1:]},
        limitations=[
            "as-of is not verified",
            "unit-noise variance interpretation is not validated by algebra",
            "No complete model verifier, new bootstrap, or new forecast run",
        ],
    )
    destinations[0].write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "passed",
                    "checked_utc",
                    "arrays_loaded",
                    "origin_rows",
                    "boundary_rows",
                    "new_training",
                    "new_physics",
                    "new_scores",
                    "maximum_differences",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
