"""Reuse frozen internal checkpoints; preserve the original conditional workflow."""

import argparse
import json
import shutil
import traceback

import numpy as np
import torch

from short_horizon.common import ROOT, CALLS, Recorder, save_json, sha, now
from short_horizon.data import training, query
from short_horizon.models import Scaling
from short_horizon.evaluation import calibrate, score_set, quality
from neural_initial_state.core import (
    InitialStateNet, read_spec, cache_for, guard, predict, initial_checks,
)
from neural_initial_state.feasibility import independent_one, mean_from_state
from neural_initial_state.reference import OriginalResume
from neural_initial_state.run import (
    arrays, baselines, train_once, development_gates, copy_selected,
)


def lock_code(spec, config):
    root = ROOT / spec['output_root']
    paths = [ROOT / config] + sorted((ROOT / 'code/neural_initial_state_continuation').glob('*.py'))
    paths = [p for p in paths if p.name != 'verify.py']
    state = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    target = root / 'implementation_lock.json'
    if target.exists() and json.loads(target.read_text()) != state:
        raise ValueError('Continuation implementation changed between phases')
    save_json(target, state)


def physical_record(initial, states, exact, audit, coeff, baseline, unit, spec, origin):
    checks = initial_checks(initial, baseline, unit)
    beta = coeff[:4]
    if not np.isfinite(coeff).all() or not np.all((beta > 0) & (beta < 1)):
        raise ArithmeticError('Invalid beta in converted plasticity threshold')
    p = spec['physics']
    # For every substep and coordinate beta > 0: dp >= x_min/beta iff x >= x_min.
    converted = bool(audit[0] >= p['x_min'])
    strict = bool(audit[3] >= p['strict_substep_diagnostic_min_mm'])
    daily = float(np.diff(np.vstack([initial[4:8], exact[:, 4:8]]), axis=0).min())
    difference = float(abs(states - exact).max())
    passed = bool(
        np.isfinite(exact).all() and np.isfinite(audit).all()
        and converted and audit[1] >= p['gap_min']
        and audit[2] <= p['normalized_complementarity_max']
        and daily >= p['plastic_daily_min_mm']
        and difference <= p['replay_state_tolerance_original_units']
    )
    record = dict(
        origin=int(origin), numerical_physics_pass=passed,
        converted_substep_pass=converted, strict_substep_diagnostic_pass=strict,
        converted_substep_min_mm_by_coordinate=(p['x_min'] / beta).tolist(),
        minimum_x=float(audit[0]), minimum_gap=float(audit[1]),
        maximum_complementarity=float(audit[2]), minimum_substep_plastic_mm=float(audit[3]),
        minimum_daily_plastic_mm=daily, state_difference=difference, **checks,
    )
    if not passed:
        raise ArithmeticError('Continuation numerical physics failed: ' + repr(record))
    return record


def audit_prediction(reference, data, details, unit, spec):
    rows = []
    for i, origin in enumerate(data['origins']):
        guard(spec)
        initial = details['initial'][i]
        exact, audit, _ = independent_one(reference, data, initial, i)
        row = physical_record(initial, details['states'][i], exact, audit,
                              data['coeff'][i], data['state'][i, 0], unit, spec, origin)
        error = float(abs(mean_from_state(exact, initial, data, i)
                          - mean_from_state(details['states'][i], initial, data, i)).max())
        if error > spec['physics']['replay_mean_tolerance_mm']:
            raise ArithmeticError('Independent projected mean differs')
        rows.append(dict(mean_difference_mm=error, **row))
    return rows


def preflight(spec, recorder):
    guard(spec, 'feasibility')
    root = ROOT / spec['output_root']
    out = root / 'preflight'
    out.mkdir()
    cache = cache_for(spec)
    data = query(cache, np.arange(*spec['stages']['inner']))
    tr = training(cache, spec, spec['stages']['inner'][0])
    scaling = Scaling(tr)
    old = ROOT / spec['resume_source_root']
    if scaling.state != json.loads((old / 'inner/training/scaling.json').read_text()):
        raise ValueError('Frozen internal normalization differs')
    legacy = json.loads((old / 'feasibility/receipt.json').read_text())
    if not legacy['passed'] or not all(r['passed'] for r in legacy['gradient_checks']):
        raise ValueError('Original nonzero feasibility was not verified')
    torch.manual_seed(0)
    model = InitialStateNet()
    mean, detail = predict(model, scaling, data, spec)
    reference = OriginalResume(spec)
    rows = audit_prediction(reference, data, detail, scaling.state_unit, spec)
    zero_error = float(abs(mean - data['anchor']).max())
    saved = baselines(spec, 'inner')['B_ANCHOR']
    masked_error = float(np.nanmax(abs(mean - saved['mean'])))
    if max(zero_error, masked_error) > spec['physics']['zero_mean_tolerance_mm']:
        raise ArithmeticError('Zero correction does not return B+')
    np.savez_compressed(out / 'zero_control.npz', origins=data['origins'], mean=mean, **detail)
    receipt = dict(
        passed=True, utc=now(), frozen_sources=len(spec['source_sha256']),
        trajectories=len(rows), strict_diagnostic_failures=[r['origin'] for r in rows if not r['strict_substep_diagnostic_pass']],
        zero_cache_difference_mm=zero_error, zero_saved_B_difference_mm=masked_error,
        previous_nonzero_gradient_checks_reused=True, new_training=0, calls=CALLS.copy(), trajectories_audit=rows,
    )
    save_json(out / 'receipt.json', receipt)
    recorder.event('preflight_passed', trajectories=len(rows), zero_error_mm=zero_error, calls=CALLS.copy())
    return {k: v for k, v in receipt.items() if k != 'trajectories_audit'}


def evaluate(spec, phase, cache, data, scaling, steps, previous, out, reference, recorder):
    phase_root = ROOT / spec['output_root'] / phase
    out.mkdir()
    means, details, physical = [], [], []
    for seed in spec['neural']['seeds']:
        model = InitialStateNet()
        model.load_state_dict(torch.load(phase_root / 'training' / f'seed_{seed}' / f'step_{steps}.pt', map_location='cpu', weights_only=True))
        mu, detail = predict(model, scaling, data, spec)
        rows = audit_prediction(reference, data, detail, scaling.state_unit, spec)
        physical.append(dict(seed=seed, passed=True, trajectories=rows))
        means.append(mu)
        details.append(detail)
    means = np.stack(means)
    mean = means.mean(axis=0)
    np.savez_compressed(out / 'seed_predictions.npz', origins=data['origins'], mean=means,
                        **{k: np.stack([d[k] for d in details]) for k in details[0]})
    save_json(out / 'physical_audit.json', dict(all_seeds_pass=True, tolerance_basis='original x_min/beta; raw strict diagnostic retained', seeds=physical))
    recorder.event('all_seed_trajectories_locked', steps=steps, path=str(out.relative_to(ROOT)), sha256=sha(out / 'seed_predictions.npz'))
    averaged_initial = np.mean([d['initial'] for d in details], axis=0)
    discrepancy = np.zeros(7)
    for i in range(len(data['origins'])):
        guard(spec)
        states, _, _ = independent_one(reference, data, averaged_initial[i], i)
        discrepancy = np.maximum(discrepancy, abs(mean_from_state(states, averaged_initial[i], data, i) - mean[i]).max(axis=1))
    save_json(out / 'ensemble_interpretation.json', dict(statistical_mean_only=True, mean_initial_replay_difference_by_horizon_mm=discrepancy.tolist(), used_as_replacement=False, interval_physical_feasibility_claim=False))
    pred, init = calibrate(spec, phase, spec['model'], mean, cache, previous, recorder, group=out.name)
    np.savez_compressed(out / (spec['model'] + '.npz'), **pred)
    save_json(out / 'calibration.json', dict(initialization=init, distribution='Normal(ensemble_mean,matured_error_RMS^2)', original_rule='v4.0'))
    predictions = {**baselines(spec, phase), spec['model']: pred}
    metrics, summary = score_set(spec, phase, predictions, out)
    _, shared = score_set(spec, phase, predictions, out, common=True)
    return dict(q=quality(shared, spec['model']), physics_pass=True, metrics=metrics, summary=summary, pred=pred)


def phase_run(spec, phase, recorder):
    guard(spec)
    root = ROOT / spec['output_root']
    if not json.loads((root / 'preflight/receipt.json').read_text())['passed']:
        raise RuntimeError('Full zero-control preflight must pass')
    selected = None
    if phase != 'inner':
        selected = json.loads((root / 'internal_selection.json').read_text())
        if not selected['passed']:
            raise RuntimeError('Internal selection did not pass')
    if phase == 'later_exploratory' and not json.loads((root / 'development/decision.json').read_text())['passed']:
        raise RuntimeError('Development failed; later training prohibited')
    out = root / phase
    out.mkdir(exist_ok=False)
    cache = cache_for(spec)
    start, end = spec['stages'][phase]
    tr = training(cache, spec, start)
    scaling = Scaling(tr)
    data = query(cache, np.arange(start, end))
    np.savez_compressed(out / 'training_queries.npz', origins=tr['origins'], teacher=tr['teacher'], target_last=tr['origins'] + 6)
    if phase == 'inner':
        source = ROOT / spec['resume_source_root'] / 'inner/training'
        shutil.copytree(source, out / 'training')
        copied = {}
        for path in sorted(source.rglob('*')):
            if path.is_file():
                dest = out / 'training' / path.relative_to(source)
                if sha(path) != sha(dest):
                    raise ValueError('Copied checkpoint changed')
                copied[str(path.relative_to(ROOT))] = sha(dest)
        if scaling.state != json.loads((out / 'training/scaling.json').read_text()):
            raise ValueError('Internal scaling changed')
        save_json(out / 'checkpoint_reuse.json', dict(new_training=0, source_sha256=copied))
        recorder.event('internal_checkpoints_reused', new_training=0, file_count=len(copied))
    else:
        train_once(tr, scaling, spec, phase, selected['step'], recorder)
    reference = OriginalResume(spec)
    previous_phase = {'inner': None, 'development': 'inner', 'later_exploratory': 'development'}[phase]
    previous = arrays(root / previous_phase / (spec['model'] + '.npz')) if previous_phase else None
    if phase == 'inner':
        records = []
        for step in spec['neural']['checkpoints']:
            result = evaluate(spec, phase, cache, data, scaling, step, previous, out / f'checkpoint_{step}', reference, recorder)
            records.append(dict(step=step, q=result['q'], physics_pass=result['physics_pass']))
            print(json.dumps(records[-1]), flush=True)
        best = min(r['q'] for r in records)
        tol = spec['selection']['inner_tolerance']
        choice = min(r['step'] for r in records if r['q'] <= best + tol)
        zero = records[0]['q']
        passed = choice != 0 and best < zero - tol and all(r['physics_pass'] for r in records)
        decision = dict(passed=bool(passed), step=choice, zero_q=zero, best_q=best, checkpoints=records,
                        selection_uses='inner common 7-target origins only', later_reselection=False, new_internal_training=0)
        copy_selected(out / f'checkpoint_{choice}', out, spec['model'])
        save_json(root / 'internal_selection.json', decision)
    else:
        result = evaluate(spec, phase, cache, data, scaling, selected['step'], previous, out / 'selected', reference, recorder)
        copy_selected(out / 'selected', out, spec['model'])
        decision = development_gates(spec, result['metrics'], result['summary'])
        decision.update(physics_pass=result['physics_pass'], step=selected['step'], phase=phase, exploratory=True)
    save_json(out / 'decision.json', decision)
    save_json(out / 'completion.json', dict(status='completed', utc=now(), calls=CALLS.copy(), decision=decision))
    recorder.event('phase_completed', decision=decision, calls=CALLS.copy())
    return decision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--phase', choices=['preflight', 'inner', 'development', 'later_exploratory'], required=True)
    args = parser.parse_args()
    spec = read_spec(args.config)
    guard(spec)
    root = ROOT / spec['output_root']
    root.mkdir(parents=True, exist_ok=True)
    lock_code(spec, args.config)
    recorder = Recorder(root, spec, args.phase)
    torch.set_num_threads(spec['neural']['cpu_threads'])
    try:
        result = preflight(spec, recorder) if args.phase == 'preflight' else phase_run(spec, args.phase, recorder)
        print(json.dumps(result, indent=2), flush=True)
    except Exception as error:
        save_json(root / (args.phase + '_failure.json'), dict(error=repr(error), traceback=traceback.format_exc(), utc=now(), calls=CALLS.copy()))
        recorder.event('phase_failed', error=repr(error), calls=CALLS.copy())
        raise


if __name__ == '__main__':
    main()
