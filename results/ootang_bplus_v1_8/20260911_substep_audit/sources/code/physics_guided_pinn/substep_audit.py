"""Audit recorded original states with NumPy update algebra and PINN residuals."""

import numpy as np
import torch

from .equations import Coefficients, observe, residuals
from .trace import inputs_from_saved

STATE_BLOCKS = ("motion", "plastic", "basal", "contact", "bulk", "background")
DOMAIN_NAMES = ("O3", "O2", "O1_up", "O1_down")


def audit_trace(recorded, saved, length, observation, y0, tolerance):
    n = len(saved["mean"])
    steps = (n - 1) * 64
    shapes = {
        "previous": (steps, 24),
        "current": (steps, 24),
        "loads": (steps, 12),
        "lcp": (steps, 16),
        "masks": (steps, 2),
        "bad": (),
        **{
            name: (n, 4)
            for name in (
                "coordinates",
                "plastic",
                "contact",
                "bulk_reaction",
                "basal_reaction",
            )
        },
    }
    for name, shape in shapes.items():
        if recorded[name].shape != shape or not np.isfinite(recorded[name]).all():
            raise ValueError(f"Invalid recorded shape or value: {name}")
    if recorded["masks"].dtype != np.int32:
        raise ValueError("Integer original activity masks required")
    c = inputs_from_saved(saved, length)
    dt = 1 / 64
    beta = dt / (c["tau_motion"] + dt)
    ar = 1 / (1 + dt / c["tau_rest"])
    ac, ae = 1 / (1 + dt / c["tau_contact"]), 1 / (1 + dt / c["tau_bulk"])
    previous, current, loads, lcp = [
        recorded[k] for k in ("previous", "current", "loads", "lcp")
    ]
    s0, p0, rb0, rc0, re0, b0 = np.split(previous, 6, axis=1)
    s, p, rb, rc, re, b = np.split(current, 6, axis=1)
    force, elastic, db = np.split(loads, 3, axis=1)
    dx_native, gap_native, rhs_native, du_native = np.split(lcp, 4, axis=1)
    checks = {}

    def boolean(name, value):
        checks[name] = {"passed": bool(value)}

    def compare(name, difference, allowed, unit):
        error = np.abs(difference)
        bound = np.broadcast_to(allowed, error.shape)
        if (
            not np.isfinite(error).all()
            or not np.isfinite(bound).all()
            or (bound <= 0).any()
        ):
            raise ValueError(f"Nonfinite audit arithmetic or invalid bound: {name}")
        ratio = error / bound
        checks[name] = dict(
            passed=bool((ratio <= 1).all()),
            max_absolute_error=float(error.max()),
            max_allowed=float(bound.max()),
            max_error_to_bound=float(ratio.max()),
            unit=unit,
            values=int(error.size),
        )

    mm = tolerance["displacement_absolute"]
    reaction = tolerance["reaction_absolute"]
    rounding = tolerance["roundoff_factor"] * np.finfo(np.float64).eps
    force_unit = "original generalized reaction units"
    boolean("all_activity_steps_valid", (recorded["masks"][:, 1] == 1).all())
    boolean(
        "activity_masks_in_range",
        ((recorded["masks"][:, 0] >= 0) & (recorded["masks"][:, 0] <= 15)).all(),
    )
    boolean("no_native_failures", recorded["bad"] == 0)
    boolean("initial_state_zero", np.array_equal(previous[0], np.zeros(24)))
    boolean("substep_continuity_exact", np.array_equal(previous[1:], current[:-1]))
    for name in (
        "coordinates",
        "plastic",
        "contact",
        "bulk_reaction",
        "basal_reaction",
    ):
        boolean(f"initial_{name}_zero", np.array_equal(recorded[name][0], np.zeros(4)))

    day = np.repeat(np.arange(1, n), 64)
    fraction = np.tile(np.arange(1, 65) / 64, n - 1)[:, None]
    interpolated = saved["background"][day - 1] + fraction * (
        saved["background"][day] - saved["background"][day - 1]
    )
    boolean("force_schedule_exact", np.array_equal(force, c["force"][day]))
    boolean("elastic_schedule_exact", np.array_equal(elastic, c["elastic"][day]))
    compare("background_interpolation", b - interpolated, mm, "mm")
    compare("background_load", db - (interpolated - b0), mm, "mm")

    # Rebuild original A, rhs and gap independently of the residual interface.
    stiffness = ac * c["kc"] + ae * c["ke"]
    a = stiffness + np.diag((c["eta"] / dt + ar * c["hardening"]) / beta)
    k0 = beta * (p0 + elastic - s0) + db
    rhs = force - ar * rb0 - ac * rc0 - ae * re0
    for i in range(4):
        for j in range(4):
            rhs[:, i] -= stiffness[i, j] * k0[:, j]
    gap = -rhs.copy()
    for i in range(4):
        for j in range(4):
            gap[:, i] += a[i, j] * dx_native[:, j]
    rhs_scale = (
        abs(force)
        + ar * abs(rb0)
        + ac * abs(rc0)
        + ae * abs(re0)
        + abs(k0) @ abs(stiffness).T
    )
    compare(
        "native_rhs_numpy_identity",
        rhs_native - rhs,
        reaction + rounding * rhs_scale,
        force_unit,
    )
    compare(
        "native_gap_numpy_identity",
        gap_native - gap,
        reaction + rounding * (abs(rhs) + abs(dx_native) @ abs(a).T),
        force_unit,
    )
    compare("native_du_numpy_identity", du_native - k0 - dx_native, mm, "mm")

    # Reconstruct the update from recorded original dx/du, not from state differences.
    p_expected = p0 + dx_native / beta
    s_expected = s0 + beta * (p_expected + elastic - s0)
    rb_expected = ar * (rb0 + c["hardening"] * dx_native / beta)
    rc_expected = ac * (rc0 + du_native @ c["kc"].T)
    re_expected = ae * (re0 + du_native @ c["ke"].T)
    for name, actual, expected, floor, unit in (
        ("motion", s, s_expected, mm, "mm"),
        ("plastic", p, p_expected, mm, "mm"),
        ("basal", rb, rb_expected, reaction, force_unit),
        ("contact", rc, rc_expected, reaction, force_unit),
        ("bulk", re, re_expected, reaction, force_unit),
    ):
        compare(
            f"native_update_{name}",
            actual - expected,
            floor + rounding * (abs(actual) + abs(expected)),
            unit,
        )

    coefficient = Coefficients.from_reference(
        saved["theta"], length, saved["kc"], saved["ke"]
    )
    with torch.no_grad():
        values = residuals(
            *[torch.from_numpy(x) for x in (previous, current, force, elastic, db)],
            coefficient,
        )
        values = {key: value.numpy() for key, value in values.items()}
    q_scale = abs(s) + abs(b) + abs(s0) + abs(b0)
    scales = {
        "motion": abs(s) + abs(s0) + beta * (abs(p) + abs(elastic) + abs(s0)),
        "basal_memory": abs(rb) + ar * (abs(rb0) + c["hardening"] * (abs(p) + abs(p0))),
        "contact_memory": abs(rc) + ac * (abs(rc0) + q_scale @ abs(c["kc"]).T),
        "bulk_memory": abs(re) + ae * (abs(re0) + q_scale @ abs(c["ke"]).T),
        "background": abs(b) + abs(b0) + abs(db),
    }
    for name, scale in scales.items():
        displacement = name in ("motion", "background")
        compare(
            f"pinn_{name}",
            values[name],
            (mm if displacement else reaction) + rounding * scale,
            "mm" if displacement else force_unit,
        )

    bound_dx = mm + rounding * beta * (abs(p) + abs(p0))
    gap_scale = (
        c["eta"] / dt * (abs(p) + abs(p0)) + abs(rb) + abs(rc) + abs(re) + abs(force)
    )
    bound_gap = reaction + rounding * gap_scale
    compare("pinn_dx_identity", values["slip_step"] - dx_native, bound_dx, "mm")
    compare(
        "pinn_gap_identity", values["yield_gap"] - gap_native, bound_gap, force_unit
    )
    compare(
        "native_negative_dx",
        np.maximum(-dx_native, 0),
        -tolerance["native_min_dx"],
        "mm",
    )
    compare(
        "native_negative_gap",
        np.maximum(-gap_native, 0),
        -tolerance["native_min_gap"],
        force_unit,
    )
    compare(
        "pinn_negative_dx",
        values["negative_slip"],
        -tolerance["native_min_dx"] + bound_dx,
        "mm",
    )
    compare(
        "pinn_negative_gap",
        values["negative_gap"],
        -tolerance["native_min_gap"] + bound_gap,
        force_unit,
    )
    product = dx_native * gap_native
    normalizer = 1 + abs(dx_native).max(axis=1) * abs(gap_native).max(axis=1)
    normalized = abs(product).max(axis=1) / normalizer
    compare(
        "native_normalized_complementarity",
        normalized,
        tolerance["normalized_complementarity"],
        "normalized source convention",
    )
    compare(
        "pinn_complementarity_identity",
        values["complementarity"] - product,
        abs(dx_native) * bound_gap + abs(gap_native) * bound_dx + bound_dx * bound_gap,
        "mm times original generalized reaction units",
    )
    active = (recorded["masks"][:, :1] & (1 << np.arange(4))) != 0
    boolean("native_active_dx_zero", (dx_native[active] == 0).all())

    daily_state = np.vstack([np.zeros((1, 24)), current[63::64]])
    extracted = {
        "coordinates": daily_state[:, :4] + daily_state[:, 20:],
        "plastic": daily_state[:, 4:8],
        "basal_reaction": daily_state[:, 8:12],
        "contact": daily_state[:, 12:16],
        "bulk_reaction": daily_state[:, 16:20],
        "background": daily_state[:, 20:],
    }
    for name, value in extracted.items():
        displacement = name in ("coordinates", "plastic", "background")
        floor, unit = (mm, "mm") if displacement else (reaction, force_unit)
        compare(f"daily_saved_{name}", value - saved[name], floor, unit)
        if name in recorded:
            compare(f"daily_recorded_{name}", value - recorded[name], floor, unit)
    with torch.no_grad():
        mean = observe(
            torch.from_numpy(daily_state),
            torch.as_tensor(observation),
            torch.as_tensor(y0),
        ).numpy()
    compare("daily_observation", mean - saved["mean"], mm, "mm")
    return dict(
        passed=all(item["passed"] for item in checks.values()),
        days=n,
        substeps=steps,
        checks=checks,
        native=dict(
            min_dx=float(dx_native.min()),
            min_gap=float(gap_native.min()),
            max_normalized_complementarity=float(normalized.max()),
            active_steps_by_domain=dict(zip(DOMAIN_NAMES, active.sum(axis=0).tolist())),
            min_dx_by_domain=dx_native.min(axis=0).tolist(),
            min_gap_by_domain=gap_native.min(axis=0).tolist(),
        ),
        pinn=dict(
            min_dx=float(values["slip_step"].min()),
            min_gap=float(values["yield_gap"].min()),
            max_absolute_complementarity=float(abs(values["complementarity"]).max()),
            max_roundoff_dx=float((bound_dx - mm).max()),
            max_roundoff_gap=float((bound_gap - reaction).max()),
        ),
    )
