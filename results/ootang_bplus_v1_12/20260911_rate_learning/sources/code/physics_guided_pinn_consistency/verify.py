"""Read-only source, gradient-algebra, finite-difference and motion checks."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_state_pinn.verify import NumericalChecks
from physics_guided_state_pinn.workflow import (
    load_bundle,
    specification as state_specification,
    POINTS,
)
from .core import TERMS, gradient_tables, norm
from .run import CONFIG, CONFIG_SHA, PLAN_SHA, specification


def table_check(check, actual, expected):
    if actual.shape != expected.shape or list(actual.columns) != list(expected.columns):
        raise ValueError("Diagnostic table schema differs")
    for name in expected:
        a, b = actual[name], expected[name]
        if b.dtype.kind in "iuf":
            if not np.array_equal(a.isna(), b.isna()):
                raise ValueError("Undefined gradient angles differ")
            keep = ~b.isna()
            values = b[keep].to_numpy(float)
            check.close(
                a[keep].to_numpy(float),
                values,
                atol=1e-10 + 64 * np.finfo(float).eps * abs(values),
            )
        elif a.tolist() != b.tolist():
            raise ValueError(f"Diagnostic identifiers differ: {name}")


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Diagnostic design changed")
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    for name, digest in manifest["sources"].items():
        if sha(out / "sources" / name) != digest:
            raise ValueError("Diagnostic source snapshot changed")
    if (
        sha(out / "sources" / CONFIG.relative_to(ROOT)) != CONFIG_SHA
        or sha(out / "sources" / spec["protocol"]) != PLAN_SHA
    ):
        raise ValueError("Diagnostic protocol snapshot changed")
    execution = read_json(out / "execution.json")
    if any(
        execution[key] != value
        for key, value in dict(
            neural_evaluations=52,
            gradient_calls=324,
            algebraic_decompositions=9,
            optimizer_updates=0,
            native_calls=0,
            label_prefixes=[342, 432, 612],
        ).items()
    ):
        raise ValueError("Diagnostic calls or label prefixes differ")
    parameters = read_json(out / "parameters.json")
    groups = {
        g: np.array(
            [
                i
                for p in parameters
                if p["group"] == g
                for i in range(p["start"], p["stop"])
            ]
        )
        for g in ("G", "H")
    }
    if (
        len(groups["G"]) != 548
        or len(groups["H"]) != 3028
        or not np.array_equal(np.r_[groups["G"], groups["H"]], np.arange(3576))
    ):
        raise ValueError("Gradient parameter groups changed")
    check = NumericalChecks()
    rows, balances, total_gradients = [], [], {}
    for h in spec["prefixes"]:
        for seed in spec["seeds"]:
            for epoch in spec["checkpoints"]:
                saved = load_npz(out / f"gradients_{h}_{seed}_{epoch}.npz")
                if saved["gradients"].shape != (9, 3576) or saved["values"].shape != (
                    9,
                ):
                    raise ValueError("Gradient vector dimensions changed")
                check.close(saved["values"], saved["values"])
                terms = dict(zip(TERMS, saved["values"]))
                r, balance, total = gradient_tables(saved["gradients"], terms, groups)
                identity = dict(prefix=h, seed=seed, epoch=epoch)
                rows.extend({**identity, **item} for item in r)
                balances.append(
                    dict(
                        **identity,
                        **balance,
                        objective_lambda1=terms["data"]
                        + np.mean(saved["values"][1:8])
                        + 0.001 * terms["rate_prior"],
                        original_next_weight=min(1.0, (epoch + 1) / 20),
                    )
                )
                total_gradients[h, seed, epoch] = total
    table_check(check, pd.read_csv(out / "gradient_terms.csv"), pd.DataFrame(rows))
    table_check(
        check, pd.read_csv(out / "gradient_balance.csv"), pd.DataFrame(balances)
    )
    differences = pd.read_csv(out / "directional_checks.csv")
    expected_order = [
        (h, 0, 200, g, step)
        for h in (342, 612)
        for g in ("G", "H")
        for step in spec["direction_steps"]
    ]
    if (
        list(
            differences[["prefix", "seed", "epoch", "group", "step"]].itertuples(
                index=False, name=None
            )
        )
        != expected_order
    ):
        raise ValueError("Directional probes differ from the fixed set")
    for row in differences.itertuples(index=False):
        direction = load_npz(out / f"direction_{row.prefix}_{row.group}.npz")[
            "direction"
        ]
        if direction.shape != (3576,):
            raise ValueError("Direction shape changed")
        check.close(np.array(norm(direction)), np.array(1.0), atol=1e-12)
        outside = np.setdiff1d(np.arange(3576), groups[row.group])
        check.close(direction[outside], np.zeros(len(outside)), atol=0)
        ad = float(np.sum(total_gradients[row.prefix, row.seed, row.epoch] * direction))
        fd = (row.plus - row.minus) / (2 * row.step)
        check.close(
            np.array(row.ad),
            np.array(ad),
            atol=1e-10 + 64 * np.finfo(float).eps * abs(ad),
        )
        check.close(
            np.array(row.fd),
            np.array(fd),
            atol=1e-9
            + 64
            * np.finfo(float).eps
            * max(abs(row.plus), abs(row.minus))
            / (2 * row.step),
        )
        allowed = spec["direction_atol"] + spec["direction_rtol"] * max(
            abs(row.ad), abs(row.fd)
        )
        check.close(
            np.array(row.allowed),
            np.array(allowed),
            atol=1e-10 + 64 * np.finfo(float).eps * allowed,
        )
        if row.passed != (abs(row.ad - row.fd) <= allowed):
            raise ValueError("Directional status changed")
    if not differences[differences.step == min(spec["direction_steps"])].passed.all():
        raise ArithmeticError(
            "Small-step directional validation failed; no extra probes authorized"
        )
    source = ROOT / spec["source_run"]
    motion_rows = []
    max_closed_form = 0.0
    for h in spec["prefixes"]:
        bundle = load_bundle(state_specification(), h, h)
        beta = (1 / 64) / (np.exp(bundle[0]["theta"][20:24]) + 1 / 64)
        for seed in spec["seeds"]:
            arrays = load_npz(out / f"motion_{h}_{seed}.npz")
            if any(
                v.shape != (h, 4) or not np.isfinite(v).all() for v in arrays.values()
            ):
                raise ValueError("Invalid motion diagnostic arrays")
            directory = source / f"model_{h}_{seed}"
            p = load_npz(directory / "P.npz")
            r = load_npz(directory / "R.npz")
            count = (h - 1) * 64
            ps = p["state"][: count + 1]
            rs = np.vstack([np.zeros((1, 24)), r["current"][:count]])
            delta_p = ps[:, 4:8] - rs[:, 4:8]
            elastic = r["loads"][:count, 4:8]

            def residual(s):
                return (
                    s[1:, :4] - s[:-1, :4] - beta * (s[1:, 4:8] + elastic - s[:-1, :4])
                )

            defect = residual(ps) - residual(rs)
            for day in (1, h // 2, h - 1):
                k = day * 64
                weights = (1 - beta) ** np.arange(k - 1, -1, -1)[:, None]
                for key, values in (
                    ("plastic_driven_s", beta * delta_p[1 : k + 1]),
                    ("motion_defect_s", defect[:k]),
                ):
                    expected = np.sum(weights * values, axis=0)
                    actual = arrays[key][day]
                    check.close(actual, expected)
                    max_closed_form = max(
                        max_closed_form, float(np.max(abs(actual - expected)))
                    )
            check.close(
                arrays["actual_observed"], p["mean"][:h] - r["mean"][:h], mean=True
            )
            check.close(
                arrays["reconstructed_observed"], arrays["actual_observed"], mean=True
            )
            for j, point in enumerate(POINTS):
                row = dict(prefix=h, seed=seed, station=point)
                for key in (
                    "plastic_driven_s_observed",
                    "motion_defect_s_observed",
                    "background_difference_observed",
                    "actual_observed",
                ):
                    values = arrays[key][30:, j]
                    row[key + "_rms_mm"] = float(np.sqrt(np.mean(values**2)))
                    row[key + "_end_mm"] = float(values[-1])
                motion_rows.append(row)
    table_check(
        check, pd.read_csv(out / "motion_components.csv"), pd.DataFrame(motion_rows)
    )
    audits = read_json(out / "motion_checks.json")
    if len(audits) != 9 or any(
        row["max_state_error_mm"] > spec["reconstruction_atol_mm"]
        or row["max_observation_error_mm"] > spec["reconstruction_atol_mm"]
        for row in audits
    ):
        raise ValueError("Recorded motion checks failed")
    m2 = ROOT / spec["m2_source"] / "development/M2"
    stored = read_json(out / "m2_history.json")
    if (
        stored["selection"] != read_json(m2 / "selection.json")
        or stored["new_m2_calls"] != 0
    ):
        raise ValueError("Historical M2 selection changed")
    for row in stored["training"]:
        history = read_json(m2 / f"mean_training_seed{row['seed']}.json")
        if (
            row["first_update_pre_loss"] != history[0]["loss"]
            or row["last_update_pre_loss"] != history[-1]["loss"]
        ):
            raise ValueError("Historical M2 training summary changed")
    return dict(
        passed=True,
        checkpoint_gradient_matrices=36,
        gradient_vectors=324,
        small_step_directions_passed=4,
        motion_groups=9,
        closed_form_knots_checked=27,
        max_closed_form_difference_mm=max_closed_form,
        mean_reconstruction_max_difference_mm=check.max_mean_difference_mm,
        protected_files=len(protected),
        new_neural_evaluations=0,
        new_gradient_calls=0,
        new_native_calls=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or Path(args.run_id).name != args.run_id:
        raise ValueError("Unsafe run id")
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_10" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
