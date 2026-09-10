"""Read-only values, full-history derivative evidence and strict call accounting."""

import argparse
import json
import re

import numpy as np
import pandas as pd
import torch

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
    read_labels,
    specification as state_specification,
)
from .run import CONFIG, CONFIG_SHA, PLAN_SHA, limits, specification


def expected_evaluations(spec):
    result = []
    for h in spec["prefixes"]:
        base = dict(prefix=h)
        result.append(
            dict(
                id=f"zero_{h}",
                days=h + 180,
                neural=True,
                **base,
                seed=0,
                epoch=0,
                kind="zero",
            )
        )
        for seed in spec["seeds"]:
            result.append(
                dict(
                    id=f"full_{h}_{seed}",
                    days=h + 180,
                    neural=True,
                    **base,
                    seed=seed,
                    epoch=200,
                    kind="full",
                )
            )
        if h not in spec["direction_prefixes"]:
            continue
        base.update(days=h, seed=0, epoch=200)
        result.append(dict(id=f"train_{h}", neural=True, **base, kind="train"))
        for key in ("gradient", "random"):
            for step in spec["direction_steps"]:
                for sign in ("plus", "minus"):
                    result.append(
                        dict(
                            id=f"param_{h}_{key}_{step:.0e}_{sign}",
                            neural=True,
                            **base,
                            kind="parameter_probe",
                        )
                    )
        for sign in ("plus", "minus"):
            result.append(
                dict(id=f"early_{h}_{sign}", neural=False, **base, kind="early_probe")
            )
        result.append(
            dict(id=f"future_{h}", neural=True, **base, kind="future_isolation")
        )
    return result


def loss(arrays, labels):
    data = np.mean(((arrays["mean"][30:] - labels[30:]) / 100) ** 2)
    prior = np.mean((np.log(arrays["multiplier"][1:]) / np.log(2)) ** 2)
    return data + 0.001 * prior, data, prior


def original_parameters(source, h, seed, epoch):
    checkpoint = torch.load(
        source / f"model_{h}_{seed}/e{epoch}.pt", weights_only=True, map_location="cpu"
    )
    return np.concatenate(
        [
            v.numpy().reshape(-1)
            for k, v in checkpoint["state_dict"].items()
            if k.startswith("rate_net.")
        ]
    )


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
        raise ValueError("Validation design binding changed")
    source = ROOT / spec["source_run"]
    if manifest["source_index_sha256"] != sha(source / "artifact_manifest.json"):
        raise ValueError("Frozen training source changed")
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    for name, digest in manifest["sources"].items():
        if sha(out / "sources" / name) != digest:
            raise ValueError("Implementation snapshot changed")
    if (
        sha(out / "sources" / CONFIG.relative_to(ROOT)) != CONFIG_SHA
        or sha(out / "sources" / spec["protocol"]) != PLAN_SHA
    ):
        raise ValueError("Protocol snapshot changed")
    for library in read_json(out / "native_libraries.json"):
        if sha(library["path"]) != library["sha256"]:
            raise ValueError("Daily native library changed")
    evaluations = read_json(out / "evaluations.json")
    if evaluations != expected_evaluations(spec):
        raise ValueError("Validation cases or chronological order changed")
    execution = read_json(out / "execution.json")
    if (
        any(execution[k] != v for k, v in limits(spec).items())
        or execution["optimizer_updates"] != 0
        or execution["label_prefixes"] != [342, 612]
    ):
        raise ValueError("Scientific calls or label scope changed")
    if (
        sum(r["days"] - 1 for r in evaluations) != execution["day_forwards"]
        or sum(r["neural"] for r in evaluations) != execution["neural_evaluations"]
    ):
        raise ValueError("Call counts do not match the saved trajectories")
    check = NumericalChecks()
    cache = {h: load_bundle(state_specification(), h) for h in spec["prefixes"]}
    arrays = {r["id"]: load_npz(out / (r["id"] + ".npz")) for r in evaluations}

    def close(a, b, atol=1e-8, mean=False):
        check.close(a, b, atol=atol, mean=mean)

    def state_close(a, b):
        close(a[:, :8], b[:, :8])
        close(a[:, 20:], b[:, 20:])
        allowance = spec["reaction_atol"] + spec["roundoff_factor"] * np.finfo(
            float
        ).eps * np.max(abs(b[:, 8:20]))
        close(a[:, 8:20], b[:, 8:20], allowance)

    def scalar(a, b):
        close(
            np.asarray(a),
            np.asarray(b),
            1e-10 + 64 * np.finfo(float).eps * max(1, abs(b)),
        )

    min_dx, min_gap, max_comp = 0.0, 0.0, 0.0
    max_state_displacement, max_reaction = 0.0, 0.0
    original_branch_changes = []
    for h, bundle in cache.items():
        saved, trace, _, _ = bundle
        reference = load_npz(out / f"reference_{h}.npz")
        for name, value in reference.items():
            expected = trace["length"] if name == "length" else saved[name]
            allowance = (
                spec["reaction_atol"]
                + spec["roundoff_factor"] * np.finfo(float).eps * np.max(abs(expected))
                if name in ("force", "contact", "bulk_reaction", "basal_reaction")
                else spec["displacement_atol"]
            )
            close(value, expected, allowance, mean=name == "mean")
    for row in evaluations:
        h, n = row["prefix"], row["days"]
        values = arrays[row["id"]]
        saved, trace, _, y0 = cache[h]
        shapes = {
            "state": (n, 24),
            "mean": (n, 4),
            "multiplier": (n, 4),
            "background": (n, 4),
            "delta_background": (n, 4),
            "masks": (n - 1, 64),
            "audit": (n - 1, 5),
            "parameters": (548,),
        }
        if set(values) != set(shapes) or any(
            values[k].shape != shape or not np.isfinite(values[k]).all()
            for k, shape in shapes.items()
        ):
            raise ValueError("Incomplete or invalid trajectory arrays")
        if values["masks"].dtype.kind not in "iu" or np.any(
            (values["masks"] < 0) | (values["masks"] > 15)
        ):
            raise ValueError("Invalid active branches")
        gamma = values["multiplier"]
        if np.any((gamma < 0.5) | (gamma > 2)):
            raise ValueError("Rate bounds violated")
        close(values["state"][0], np.zeros(24), 0)
        delta = np.vstack(
            [
                np.zeros(4),
                np.cumsum(saved["background_rate"][1:n] * (gamma[1:] - 1), axis=0),
            ]
        )
        close(values["delta_background"], delta)
        close(values["background"], saved["background"][:n] + delta)
        close(values["state"][:, 20:], values["background"])
        close(
            values["mean"],
            (values["state"][:, :4] + values["state"][:, 20:])
            @ saved["observation_matrix"].T
            + y0,
            mean=True,
        )
        audit = values["audit"]
        if (
            np.min(audit[:, 0]) < -1e-8
            or np.min(audit[:, 1]) < -1e-7
            or np.max(audit[:, 2]) > 1e-8
            or np.min(audit[:, 3]) < 0
        ):
            raise ArithmeticError("Original physical tolerances failed")
        min_dx, min_gap, max_comp = (
            min(min_dx, float(audit[:, 0].min())),
            min(min_gap, float(audit[:, 1].min())),
            max(max_comp, float(audit[:, 2].max())),
        )
        if row["kind"] in ("zero", "full"):
            close(
                values["parameters"],
                original_parameters(source, h, row["seed"], row["epoch"]),
                0,
            )
            if row["kind"] == "zero":
                expected_state = np.vstack(
                    [np.zeros((1, 24)), trace["current"][63::64]]
                )
                expected_mean = saved["mean"]
                close(gamma, np.ones_like(gamma), 0)
            else:
                prior = load_npz(source / f"model_{h}_{row['seed']}/R.npz")
                p = load_npz(source / f"model_{h}_{row['seed']}/P.npz")
                expected_state = np.vstack(
                    [np.zeros((1, 24)), prior["current"][63::64]]
                )
                expected_mean = prior["mean"]
                close(gamma, p["multiplier"], spec["multiplier_atol"])
                close(values["background"], prior["background"])
                original_branch_changes.append(
                    dict(
                        prefix=h,
                        seed=row["seed"],
                        changed_substeps=int(
                            np.count_nonzero(
                                values["masks"].ravel() != prior["masks"][:, 0]
                            )
                        ),
                    )
                )
            state_close(values["state"], expected_state)
            close(values["mean"], expected_mean, mean=True)
            max_state_displacement = max(
                max_state_displacement,
                float(abs(values["state"][:, :8] - expected_state[:, :8]).max()),
            )
            max_reaction = max(
                max_reaction,
                float(abs(values["state"][:, 8:20] - expected_state[:, 8:20]).max()),
            )
    difference = pd.read_csv(out / "directional_checks.csv")
    expected_rows = []
    max_direction_error = 0.0
    for h in spec["direction_prefixes"]:
        base, full, future = (
            arrays[f"train_{h}"],
            arrays[f"full_{h}_0"],
            arrays[f"future_{h}"],
        )
        state_close(base["state"], full["state"][:h])
        close(base["mean"], full["mean"][:h], mean=True)
        for name in base:
            close(base[name], future[name], 0)
        deriv = load_npz(out / f"derivatives_{h}.npz")
        close(deriv["parameters"], original_parameters(source, h, 0, 200), 0)
        close(base["parameters"], deriv["parameters"], 0)
        if (
            deriv["gradient"].shape != (548,)
            or deriv["multiplier_gradient"].shape != (h, 4)
            or any(not np.isfinite(v).all() for v in deriv.values())
        ):
            raise ValueError("Incomplete saved full-history gradients")
        labels = read_labels(source / "input_prefix_792.csv", h)
        total, data, prior = loss(base, labels)
        for name, value in (
            ("objective", total),
            ("data", data),
            ("prior", prior),
            ("final_scalar", base["mean"][-1].sum() / 100),
        ):
            scalar(float(deriv[name]), float(value))
        if np.linalg.norm(deriv["gradient"]) <= spec["nonzero_gradient_threshold"]:
            raise ArithmeticError("Missing nonzero full-history parameter gradient")
        random = np.random.default_rng(spec["random_direction_seed"]).normal(size=548)
        for key, want in (
            ("gradient", deriv["gradient"] / np.linalg.norm(deriv["gradient"])),
            ("random", random / np.linalg.norm(random)),
        ):
            direction = deriv[key + "_direction"]
            close(direction, want, 1e-12)
            ad = float(deriv["gradient"] @ direction)
            for step in spec["direction_steps"]:
                probes = [
                    arrays[f"param_{h}_{key}_{step:.0e}_{tag}"]
                    for tag in ("plus", "minus")
                ]
                for probe, sign in zip(probes, (1, -1)):
                    close(
                        probe["parameters"],
                        deriv["parameters"] + sign * step * direction,
                        1e-15,
                    )
                expected_rows.append(
                    (
                        h,
                        "parameter",
                        key,
                        step,
                        ad,
                        [loss(p, labels)[0] for p in probes],
                        probes,
                        base,
                    )
                )
        early = deriv["early_direction"]
        close(early, np.full(4, 0.5), 0)
        ad = float(deriv["multiplier_gradient"][1] @ early)
        if abs(ad) <= spec["nonzero_gradient_threshold"]:
            raise ArithmeticError("Early-day derivative is zero")
        probes = [arrays[f"early_{h}_{tag}"] for tag in ("plus", "minus")]
        for probe, sign in zip(probes, (1, -1)):
            gamma = base["multiplier"].copy()
            gamma[1] += sign * spec["early_step"] * early
            close(probe["multiplier"], gamma, 0)
            close(probe["parameters"], deriv["parameters"], 0)
        expected_rows.append(
            (
                h,
                "early",
                "day1",
                spec["early_step"],
                ad,
                [p["mean"][-1].sum() / 100 for p in probes],
                probes,
                base,
            )
        )
    if len(difference) != len(expected_rows):
        raise ValueError("Finite difference rows missing")
    required = 0
    for row, expected in zip(difference.itertuples(index=False), expected_rows):
        h, kind, key, step, ad, values, probes, base = expected
        if (row.prefix, row.kind, row.direction, row.step) != (h, kind, key, step):
            raise ValueError("Finite difference order changed")
        scalar(row.ad, ad)
        scalar(row.plus, values[0])
        scalar(row.minus, values[1])
        fd = (values[0] - values[1]) / (2 * step)
        close(
            np.asarray(row.fd),
            np.asarray(fd),
            1e-9 + 128 * np.finfo(float).eps * max(abs(v) for v in values) / (2 * step),
        )
        allowed = spec["direction_atol"] + spec["direction_rtol"] * max(
            abs(ad), abs(fd)
        )
        scalar(row.allowed, allowed)
        for sign, probe in zip(("plus", "minus"), probes):
            if getattr(row, sign + "_branch_changes") != np.count_nonzero(
                probe["masks"] != base["masks"]
            ):
                raise ValueError("Active branch changes were not preserved")
        if row.passed != (abs(row.ad - row.fd) <= row.allowed):
            raise ValueError("Derivative status changed")
        if step == min(spec["direction_steps"]):
            required += 1
            if abs(ad - fd) > allowed:
                raise ArithmeticError(
                    "Registered small-step derivative failed; no new training"
                )
        max_direction_error = max(max_direction_error, abs(ad - fd))
    return dict(
        passed=True,
        full_trajectories_matched=12,
        all_saved_trajectories=36,
        required_small_step_directions_passed=required,
        future_isolation_prefixes=spec["direction_prefixes"],
        max_mean_difference_mm=check.max_mean_difference_mm,
        max_state_displacement_difference_mm=max_state_displacement,
        max_reaction_difference_original_units=max_reaction,
        max_direction_absolute_error=max_direction_error,
        native_min_dx=min_dx,
        native_min_gap=min_gap,
        native_max_normalized_complementarity=max_comp,
        original_branch_changes=original_branch_changes,
        protected_files=len(protected),
        new_neural_evaluations=0,
        new_native_calls=0,
        new_optimizer_updates=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_11" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
