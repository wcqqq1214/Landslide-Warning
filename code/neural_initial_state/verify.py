"""Read-only closure of the stopped run: all saved checkpoints, no scoring."""

import argparse
import json
import traceback

import numpy as np
import pandas as pd
import torch

from short_horizon.common import ROOT, CALLS, Recorder, save_json, sha, now
from short_horizon.data import training, query
from short_horizon.models import Scaling

from .core import read_spec, cache_for, guard, InitialStateNet, corrected_initial, initial_checks
from .feasibility import independent_one, mean_from_state
from .reference import OriginalResume


def trace_substeps(initial, data, i, native_tape):
    """Independent reduced-system solves locate the negative increment exactly."""
    c = data["coeff"][i]
    beta, ar, kr = c[:4], c[4:8], c[8:12]
    ac, ae = c[12:14]
    kc, ke, A = c[14:30].reshape(4, 4), c[30:46].reshape(4, 4), c[46:62].reshape(4, 4)
    state = initial.copy()
    rows, daily = [], []
    for h in range(7):
        begin = state.copy()
        for step in range(64):
            bg = begin[20:] + (step + 1) / 64 * (data["state"][i, h + 1, 20:] - begin[20:])
            k0 = beta * (state[4:8] + data["elastic"][i, h] - state[:4]) + bg - state[20:]
            rhs = data["force"][i, h] - ar * state[8:12] - ac * state[12:16] - ae * state[16:20] - np.sum((ac * kc + ae * ke) * k0[None, :], axis=1)
            mask = int(native_tape[h, step])
            free = [j for j in range(4) if not mask & (1 << j)]
            x = np.zeros(4)
            if free:
                x[free] = np.linalg.solve(A[np.ix_(free, free)], rhs[free])
            gap = np.sum(A * x[None, :], axis=1) - rhs
            dp = x / beta
            for j in range(4):
                rows.append(dict(horizon=h + 1, substep=step + 1, coordinate=j, mask=mask, beta=float(beta[j]), x=float(x[j]), gap=float(gap[j]), delta_plastic_mm=float(dp[j]), original_x_tolerance_allows=bool(x[j] >= -1e-8), frozen_plastic_gate_pass=bool(dp[j] >= -1e-8)))
            du = k0 + x
            state[4:8] += dp
            state[:4] += beta * (state[4:8] + data["elastic"][i, h] - state[:4])
            state[8:12] = ar * (state[8:12] + kr * dp)
            state[12:16] = ac * (state[12:16] + np.sum(kc * du[None, :], axis=1))
            state[16:20] = ae * (state[16:20] + np.sum(ke * du[None, :], axis=1))
            state[20:] = bg
        daily.append(state.copy())
    return np.asarray(daily), rows


def execute(spec, out, recorder):
    root = ROOT / spec["output_root"]
    lock = json.loads((root / "implementation_lock.json").read_text())
    for name, digest in lock.items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen training implementation changed: " + name)
    registry = json.loads((root / "fit_registry.json").read_text())
    if len(registry) != 3 or any(r["phase"] != "inner" or r["updates"] != 400 or r["status"] != "completed" for r in registry):
        raise ValueError("Unexpected training history for the stopped experiment")
    if (root / "internal_selection.json").exists() or (root / "development").exists() or (root / "later_exploratory").exists():
        raise ValueError("Run continued past the stopped physical gate")
    cache = cache_for(spec)
    tr = training(cache, spec, 612)
    scaling = Scaling(tr)
    saved_scale = json.loads((root / "inner/training/scaling.json").read_text())
    if scaling.state != saved_scale:
        raise ValueError("Saved normalization changed")
    with np.load(root / "inner/training_queries.npz") as q:
        assert np.array_equal(q["origins"], np.arange(252, 606))
        assert np.array_equal(q["teacher"], tr["teacher"])
        assert np.array_equal(q["target_last"], q["origins"] + 6)
        assert int(q["target_last"].max()) < 612
    data = query(cache, np.arange(612, 792))
    reference = OriginalResume(spec)
    rows, artifact_hashes, by_checkpoint = [], {}, []
    first = None
    for step in spec["neural"]["checkpoints"]:
        for seed in spec["neural"]["seeds"]:
            guard(spec)
            path = root / "inner/training" / f"seed_{seed}" / f"step_{step}.pt"
            artifact_hashes[str(path.relative_to(ROOT))] = sha(path)
            model = InitialStateNet()
            model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
            model.eval()
            saved_initial, saved_native_states, saved_day_states, saved_mean = [], [], [], []
            these = []
            for i, origin in enumerate(data["origins"]):
                guard(spec)
                t = scaling.tensors(data, slice(i, i + 1))
                with torch.no_grad():
                    x = t["x"]
                    summary = torch.cat([x[:, -1].flatten(1), x[:, -7:].mean(1).flatten(1), x.mean(1).flatten(1)], dim=1)
                    normalized = torch.tanh(model.initial_head(model.encoder(summary)))
                    init, _, _ = corrected_initial(t, scaling.state_unit, normalized)
                initial = init[0].numpy()
                initial_checks(initial, data["state"][i, 0], scaling.state_unit)
                exact, audit, tape = independent_one(reference, data, initial, i)
                row = dict(step=step, seed=seed, origin=int(origin), min_x=float(audit[0]), min_gap=float(audit[1]), max_complementarity=float(audit[2]), min_substep_plastic_mm=float(audit[3]), min_daily_plastic_mm=float(np.diff(np.vstack([initial[4:8], exact[:, 4:8]]), axis=0).min()))
                row['original_solver_gate'] = bool(audit[0] >= -1e-8 and audit[1] >= -1e-7 and audit[2] <= 1e-7)
                row['frozen_substep_gate'] = bool(audit[3] >= -1e-8)
                row['frozen_daily_gate'] = bool(row['min_daily_plastic_mm'] >= -1e-8)
                try:
                    with torch.no_grad():
                        mean, detail = model(t, scaling)
                    state = detail["states"][0].numpy()
                    row['forward_ok'] = True
                    row['independent_state_difference'] = float(abs(state - exact).max())
                    row['independent_mean_difference_mm'] = float(abs(mean[0].numpy() - mean_from_state(exact, initial, data, i)).max())
                    if row['independent_state_difference'] > 1e-8 or row['independent_mean_difference_mm'] > 1e-8:
                        raise ArithmeticError("Saved model and original C continuation disagree")
                    mu = mean[0].numpy()
                except ArithmeticError as exc:
                    if 'disagree' in str(exc):
                        raise
                    row['forward_ok'] = False
                    row['forward_error'] = repr(exc)
                    row['independent_state_difference'] = np.nan
                    row['independent_mean_difference_mm'] = np.nan
                    state, mu = np.full((7, 24), np.nan), np.full((7, 4), np.nan)
                if first is None and not row['frozen_substep_gate']:
                    traced, events = trace_substeps(initial, data, i, tape)
                    worst = min(events, key=lambda e: e['delta_plastic_mm'])
                    first = dict(**row, worst_substep=worst, independent_trace_max_state_difference=float(abs(traced - exact).max()), latest_observed_date=str(cache['dates'][origin - 1]), first_target_date=str(cache['dates'][origin]))
                    if first['independent_trace_max_state_difference'] > 1e-8:
                        raise ArithmeticError("Independent first-counterexample trace disagrees")
                    pd.DataFrame(events).to_csv(out / 'first_counterexample_substeps.csv', index=False, float_format='%.17g')
                    np.savez_compressed(out / 'first_counterexample.npz', initial=initial, native=exact, independent_trace=traced, masks=tape, coeff=data['coeff'][i], force=data['force'][i], elastic=data['elastic'][i], background=data['state'][i, 1:, 20:], origin=origin)
                rows.append(row)
                these.append(row)
                saved_initial.append(initial)
                saved_native_states.append(exact)
                saved_day_states.append(state)
                saved_mean.append(mu)
            checkpoint = dict(step=step, seed=seed, trajectories=len(these), original_solver_pass=sum(r['original_solver_gate'] for r in these), substep_pass=sum(r['frozen_substep_gate'] for r in these), daily_pass=sum(r['frozen_daily_gate'] for r in these), forward_pass=sum(r['forward_ok'] for r in these), minimum_substep_plastic_mm=min(r['min_substep_plastic_mm'] for r in these))
            by_checkpoint.append(checkpoint)
            # These are labelled diagnostic reconstructions, never selected forecasts.
            np.savez_compressed(out / f'diagnostic_step_{step}_seed_{seed}.npz', origins=data['origins'], initial=saved_initial, native_states=saved_native_states, day_states=saved_day_states, day_mean=saved_mean)
            recorder.event('checkpoint_physically_reloaded', **checkpoint)
            print(json.dumps(checkpoint), flush=True)
    pd.DataFrame(rows).to_csv(out / 'trajectory_audit.csv', index=False, float_format='%.17g')
    pd.DataFrame(by_checkpoint).to_csv(out / 'checkpoint_audit.csv', index=False, float_format='%.17g')
    save_json(out / 'first_counterexample.json', first)
    return dict(verification_pass=True, physical_contract_pass=False, stopped_as_frozen=True, normalization_reconstructed=True, training_queries=354, frozen_sources=len(spec['source_sha256']), implementation_files=len(lock), reloaded_checkpoints=len(artifact_hashes), checkpoint_sha256=artifact_hashes, trajectories=len(rows), original_solver_pass=sum(r['original_solver_gate'] for r in rows), substep_pass=sum(r['frozen_substep_gate'] for r in rows), forward_pass=sum(r['forward_ok'] for r in rows), minimum_substep_plastic_mm=min(r['min_substep_plastic_mm'] for r in rows), maximum_independent_state_difference=float(np.nanmax([r['independent_state_difference'] for r in rows])), maximum_independent_mean_difference_mm=float(np.nanmax([r['independent_mean_difference_mm'] for r in rows])), first_counterexample=first, checkpoints=by_checkpoint, new_training=0, new_model_selection=0, new_scoring=0, probability_comparison_available=False, development_or_later_run=False, calls=CALLS.copy())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    spec = read_spec(args.config)
    torch.set_num_threads(1)
    out = ROOT / spec['output_root'] / 'verification'
    out.mkdir(exist_ok=False)
    recorder = Recorder(ROOT / spec['output_root'], spec, 'verification')
    try:
        result = execute(spec, out, recorder)
    except Exception as error:
        save_json(out / 'failure.json', dict(error=repr(error), traceback=traceback.format_exc(), calls=CALLS.copy()))
        raise
    result['completed_utc'] = now()
    save_json(out / 'receipt.json', result)
    recorder.event('verification_completed', physical_pass=False, new_training=0, calls=CALLS.copy())
    print(json.dumps({k:v for k,v in result.items() if k not in ['checkpoint_sha256','first_counterexample','checkpoints']},indent=2))


if __name__ == '__main__':
    main()
