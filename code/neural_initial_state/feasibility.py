"""Fixed nonzero initial-state checks, independent C replay and gradient gate."""

import argparse
import itertools
import json
import traceback

import numpy as np
import torch

from short_horizon.common import ROOT, CALLS, Recorder, save_json, sha, now
from short_horizon.data import training, query
from short_horizon.models import Scaling

from .core import read_spec, cache_for, guard, InitialStateNet, corrected_initial, advance, initial_checks
from .reference import OriginalResume


def mean_from_state(states, initial, data, i=0, add_anchor=True):
    delta = states[:, :4] + states[:, 20:] - initial[:4] - initial[20:]
    mean = delta @ data["obs"].T
    return mean + data["last_y"][i] if add_anchor else mean


def check_audit(audit, spec):
    p = spec["physics"]
    if not (
        audit[0] >= p["x_min"] and audit[1] >= p["gap_min"]
        and audit[2] <= p["normalized_complementarity_max"]
        and audit[3] >= p["plastic_substep_min_mm"]
    ):
        raise ArithmeticError("Original C substep physical check failed: " + repr(audit))


def independent_one(reference, data, initial, i=0):
    return reference.replay(initial, data["state"][i, 1:, 20:], data["force"][i], data["elastic"][i], data["teacher"][i])


def audit_prediction(reference, data, details, unit, spec):
    records = []
    for i, n in enumerate(data["origins"]):
        guard(spec)
        initial = details["initial"][i]
        checks = initial_checks(initial, data["state"][i, 0], unit)
        exact, audit, _ = independent_one(reference, data, initial, i)
        check_audit(audit, spec)
        daily = float(np.min(np.diff(np.vstack([initial[4:8], exact[:, 4:8]]), axis=0)))
        if daily < spec["physics"]["plastic_daily_min_mm"]:
            raise ArithmeticError("Negative daily plastic increment")
        difference = float(np.max(abs(exact - details["states"][i])))
        expected = mean_from_state(exact, initial, data, i)
        actual = mean_from_state(details["states"][i], initial, data, i)
        mean_diff = float(np.max(abs(actual - expected)))
        if difference > spec["physics"]["replay_state_tolerance_original_units"] or mean_diff > spec["physics"]["replay_mean_tolerance_mm"]:
            raise ArithmeticError("Independent original C trajectory disagrees")
        records.append(dict(origin=int(n), state_difference=difference, mean_difference_mm=mean_diff, min_plastic_daily_mm=daily, audit=audit.tolist(), **checks))
    return records


def execute(spec, out, recorder):
    guard(spec, "feasibility")
    torch.set_num_threads(1)
    cache = cache_for(spec)
    tr = training(cache, spec, 612)
    scaling = Scaling(tr)
    save_json(out / "scaling.json", scaling.state)
    old_scale = json.loads((ROOT / spec["cache_root"] / "inner/training/PINN_EQ/scaling.json").read_text())
    if scaling.state != old_scale:
        raise ValueError("Training normalization differs from frozen v4.0")
    data = query(cache, spec["feasibility"]["origins"])
    tensors = scaling.tensors(data)
    reference = OriginalResume(spec)
    matrix_checks = []
    for n, c in zip(data["origins"], data["coeff"]):
        a = c[46:62].reshape(4, 4)
        eig = float(np.linalg.eigvalsh(a).min())
        symmetry = float(abs(a - a.T).max())
        if eig <= 0 or not np.allclose(a, a.T, rtol=1e-10, atol=1e-8):
            raise ArithmeticError("Complementarity matrix is not symmetric positive definite")
        if not np.all((c[:8] > 0) & (c[:8] < 1)) or not np.all((c[12:14] > 0) & (c[12:14] < 1)):
            raise ArithmeticError("Invalid physical relaxation coefficient")
        matrix_checks.append(dict(origin=int(n), minimum_eigenvalue=eig, symmetry_error=symmetry))
    vertex_checks = 0
    for vertex in itertools.product([-1., 1.], repeat=4):
        initial, _, _ = corrected_initial(tensors, scaling.state_unit, torch.tensor(vertex).double()[None].expand(len(data["origins"]), -1))
        initial_checks(initial.numpy(), data["state"][:, 0], scaling.state_unit)
        vertex_checks += len(initial)
    all_means, all_initial, all_states, checks = [], [], [], []
    for vector in [[0., 0., 0., 0.]] + spec["feasibility"]["normalized_corrections"]:
        guard(spec, "feasibility")
        initial, _, _ = corrected_initial(tensors, scaling.state_unit, torch.tensor(vector).double()[None].expand(len(data["origins"]), -1))
        with torch.no_grad():
            mean, states = advance(tensors, initial)
        details = dict(initial=initial.numpy(), states=states.numpy())
        checks += audit_prediction(reference, data, details, scaling.state_unit, spec)
        all_means.append(mean.numpy())
        all_initial.append(initial.numpy())
        all_states.append(states.numpy())
    zero_error = float(np.max(abs(all_means[0] - data["anchor"])))
    zero_state_error = float(np.max(abs(all_states[0] - data["state"][:, 1:])))
    if zero_error > spec["physics"]["zero_mean_tolerance_mm"] or zero_state_error > spec["physics"]["replay_state_tolerance_original_units"]:
        raise ArithmeticError("Zero correction does not return B+")
    impact = np.max(abs(np.stack(all_means[1:]) - all_means[0]), axis=(0, 1, 2))
    if not np.all(impact > spec["feasibility"]["impact_each_point_min_mm"]):
        raise ArithmeticError("Initial correction has no usable output influence")
    np.savez_compressed(out / "physical_examples.npz", origins=data["origins"], mean=all_means, initial=all_initial, states=all_states)
    recorder.event("physical_feasibility_passed", zero_mean_difference_mm=zero_error, zero_state_difference=zero_state_error, impact_by_point_mm=impact.tolist())

    gradients = []
    for n in spec["feasibility"]["gradient_origins"]:
        d = query(cache, [n])
        t = scaling.tensors(d)
        radius = np.minimum(scaling.state_unit[:4], np.maximum(d["state"][0, 0, :4], 0))
        for vector in spec["feasibility"]["normalized_corrections"]:
            guard(spec, "feasibility")
            def forward(v):
                initial, _, _ = corrected_initial(t, scaling.state_unit, v[None])
                return advance(t, initial)[0].reshape(-1)
            v = torch.tensor(vector, dtype=torch.float64, requires_grad=True)
            analytic = torch.autograd.functional.jacobian(forward, v).numpy()
            for epsilon in spec["feasibility"]["finite_difference_steps"]:
                fd = np.empty_like(analytic)
                stable = True
                for j in range(4):
                    values, tapes = [], []
                    for sign in [-1, 1]:
                        q = np.asarray(vector).copy()
                        q[j] += sign * epsilon
                        initial = d["state"][0, 0].copy()
                        initial[:4] += radius * q
                        states, audit, tape = independent_one(reference, d, initial)
                        check_audit(audit, spec)
                        values.append(mean_from_state(states, initial, d, add_anchor=False).ravel())
                        tapes.append(tape)
                    fd[:, j] = (values[1] - values[0]) / (2 * epsilon)
                    stable = stable and np.array_equal(tapes[0], tapes[1])
                allowed = spec["feasibility"]["gradient_atol_mm"] + spec["feasibility"]["gradient_rtol"] * abs(fd)
                error = abs(analytic - fd)
                passed = bool(np.isfinite(analytic).all() and np.all(error <= allowed) and stable)
                gradients.append(dict(origin=n, normalized=vector, epsilon=epsilon, same_active_set=stable, max_error_mm=float(error.max()), max_allowed_fraction=float(np.max(error / allowed)), passed=passed))
                if epsilon == spec["feasibility"]["finite_difference_steps"][-1] and not passed:
                    save_json(out / "gradient_checks.json", gradients)
                    raise ArithmeticError("Nonzero-state finite difference gate failed")
    save_json(out / "gradient_checks.json", gradients)

    torch.manual_seed(0)
    model = InitialStateNet()
    with torch.no_grad():
        model.initial_head.bias.copy_(torch.tensor([.2, -.15, .1, -.2]))
        model.initial_head.weight.copy_(.01 * torch.sin(torch.arange(128).reshape(4, 32)))
    d = query(cache, [432])
    t = scaling.tensors(d)
    weights = torch.linspace(-1, 1, 28, dtype=torch.float64).reshape(1, 7, 4)
    mean, _ = model(t, scaling)
    value = ((mean - t["last_y"][:, None]) * weights).mean()
    value.backward()
    parameters = list(model.parameters())
    gradient = torch.cat([p.grad.reshape(-1) for p in parameters])
    norm = float(gradient.norm())
    direction = torch.cos(torch.arange(len(gradient), dtype=torch.float64))
    direction /= direction.norm()
    analytic = float(gradient @ direction)
    saved = [p.detach().clone() for p in parameters]
    epsilon = spec["feasibility"]["finite_difference_steps"][-1]
    values, tapes = [], []
    for sign in [-1, 1]:
        offset = 0
        with torch.no_grad():
            for p, original in zip(parameters, saved):
                p.copy_(original + sign * epsilon * direction[offset:offset + p.numel()].reshape(p.shape))
                offset += p.numel()
            _, detail = model(t, scaling)
        initial = detail["initial"][0].numpy()
        states, audit, tape = independent_one(reference, d, initial)
        check_audit(audit, spec)
        values.append(float(np.mean(mean_from_state(states, initial, d, add_anchor=False) * weights.numpy()[0])))
        tapes.append(tape)
    fd = (values[1] - values[0]) / (2 * epsilon)
    difference = abs(analytic - fd)
    allowed = spec["feasibility"]["gradient_atol_mm"] + spec["feasibility"]["gradient_rtol"] * abs(fd)
    if norm <= spec["feasibility"]["network_gradient_norm_min"] or difference > allowed or not np.array_equal(*tapes):
        raise ArithmeticError("Nonzero network parameter gradient gate failed")
    return dict(passed=True, zero_mean_difference_mm=zero_error, zero_state_difference=zero_state_error, impact_by_point_mm=impact.tolist(), initial_vertex_checks=vertex_checks, trajectories=checks, matrices=matrix_checks, gradient_checks=gradients, network_gradient=dict(norm=norm, analytic=analytic, finite_difference=fd, absolute_error=difference, allowed=allowed, same_active_set=True), new_training=0, calls=CALLS.copy())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    spec = read_spec(args.config)
    root = ROOT / spec["output_root"]
    out = root / "feasibility"
    out.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(root, spec, "feasibility")
    try:
        result = execute(spec, out, recorder)
    except Exception as error:
        result = dict(passed=False, error=repr(error), traceback=traceback.format_exc(), new_training=0, calls=CALLS.copy())
        save_json(out / "receipt.json", result)
        recorder.event("feasibility_stopped", error=repr(error), calls=CALLS.copy())
        raise
    result.update(completed_utc=now(), config_sha256=sha(args.config))
    save_json(out / "receipt.json", result)
    recorder.event("feasibility_completed", passed=True, calls=CALLS.copy())
    print(json.dumps({k: v for k, v in result.items() if k not in ("trajectories", "matrices", "gradient_checks")}, indent=2))


if __name__ == "__main__":
    main()
