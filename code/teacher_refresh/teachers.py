"""Finite-budget prefix-only B+ updates, with immutable old initialization."""
import time
import warnings
import numpy as np
from scipy.optimize import least_squares
from physics_guided.reference import load
from physics_guided_diagnostics.core import bounded_jacobian, residual_vector, array_sha
from tcn_conditional_trajectory.core import feature_matrix
from overnight_graph.audit import physical
from . import core as c

o = c.o


def context_check(ref, ctx):
    with np.load(ref.ROOT/'results/reservoir_force_lookup_final.npz') as table:
        errors = [float(np.max(abs(getattr(ctx,key)-np.einsum('ij,jk->ik',table[name],ctx.T,optimize=False))))
                  for key,name in [('pore','pore'),('ext','external')]]
    if not np.isfinite(errors).all() or max(errors)>1e-8:
        raise ArithmeticError('Context matrix contraction mismatch')
    return max(errors)


def objective_inputs(ref, cfg, m, forcing, y):
    if forcing.shape != (m, 2) or y.shape != (m, 4):
        raise ValueError('B+ fit accepts exactly the registered historical prefix')
    if m not in cfg['teacher_update']['new_prefixes']:
        raise ValueError('Unregistered teacher fit')
    if not np.isfinite(forcing).all() or not np.isfinite(y).all():
        raise ValueError('Nonfinite teacher data')
    return ref.Context(forcing.copy()), y.copy()-y[0]


def fit_one(cfg, m, cached, forcing, dates):
    root = c.ROOT/cfg['out']; out = root/f'teachers/fit_{m}'
    if (out/'complete.json').exists():
        o.verify_lock(out/'complete.json')
        return
    out.mkdir(parents=True, exist_ok=False)
    start_time = o.utc(); started = time.monotonic()
    ref = load(c.ROOT/cfg['runtime']); tc = cfg['teacher_update']
    y = o.labels(cfg, m, f'physical_teacher_fit_{m}_prefix_only')
    tid = c.cached_id(m); original = cached[tid]['theta'].copy()
    assert int(cached[tid]['teacher_fit_prefix']) == tid < m
    with warnings.catch_warnings(record=True) as context_log:
        warnings.simplefilter('default')
        ctx, target = objective_inputs(ref, cfg, m, forcing[:m], y)
    context_error = context_check(ref,ctx)
    weight = tc['terminal_weight_multiplier']*m
    start = np.clip(original, ref.LO+1e-9, ref.HI-1e-9)
    o.write_json(out/'input.json', dict(started_utc=start_time, fit_prefix=m,
        initialization_teacher=tid, initialization_fit_prefix=tid, input_theta=original.tolist(),
        optimizer_initial_theta=start.tolist(), training_label_sha256=array_sha(y),
        training_forcing_sha256=array_sha(forcing[:m]), training_last_date=str(dates[m-1]),
        label_rows=m, forcing_rows=m, config_sha256=o.sha(c.CONFIG)))
    o.event(root, 'teacher_fit_started', fit_prefix=m, initialization_teacher=tid)
    calls = 0

    def fun(theta):
        nonlocal calls
        o.check_deadline(cfg); calls += 1
        value = residual_vector(ref.forward(theta, ctx, substeps=64), target, weight)
        if not np.isfinite(value).all():
            raise ArithmeticError('Nonfinite teacher objective')
        if calls % 5000 == 0:
            record = dict(fit_prefix=m, calls=calls, elapsed_seconds=time.monotonic()-started)
            o.write_json(out/'progress.json', record)
            print(record, flush=True)
        return value

    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter('default')
        op = least_squares(fun, start, jac=lambda th: bounded_jacobian(fun, th, ref.HI),
            bounds=(ref.LO, ref.HI), method=tc['method'], loss=tc['loss'], x_scale=tc['x_scale'],
            max_nfev=tc['max_nfev'], ftol=tc['ftol'], xtol=tc['xtol'], gtol=tc['gtol'])
        theta = op.x.copy()
        if theta.shape != (54,) or not np.isfinite(theta).all() or np.any(theta < ref.LO) or np.any(theta > ref.HI):
            raise ArithmeticError('Invalid B+ parameters')
        fitted, fit_states = ref.forward(theta, ctx, substeps=64, states=True)
        end = m+293
        extended, states = ref.forward(theta, ref.Context(forcing[:end]), substeps=64, states=True)
    differences = [float(np.max(abs(extended[:m]-fitted)))]
    for k in fit_states:
        a, b = fit_states[k], states[k]
        differences.append(float(np.max(abs(a-(b[:m] if len(b) == end else b)))))
    if max(differences) > cfg['numerical']['physical_replay_atol_mm']:
        raise ArithmeticError('Future forcing altered historical physical state')
    residual = (fitted-target)/tc['residual_scale_mm']
    trajectory = float(np.sum(residual**2)); terminal = float(weight*np.sum(residual[-1]**2))
    error = abs(trajectory+terminal-2*op.cost)
    if error > cfg['numerical']['score_atol_mm']:
        raise ArithmeticError('Teacher objective recomputation differs')
    mean = extended+y[0]
    data = dict(mean=mean, theta=theta, forcing=forcing[:end].copy(), y0=y[0].copy(),
                x=feature_matrix(mean, forcing[:end], states, y[0]), **states,
                dates=dates[:end], teacher_fit_prefix=np.array(m))
    for key,value in data.items():
        if key!='dates' and not np.isfinite(value).all():
            raise ArithmeticError('Nonfinite physical state: '+key)
    projection_error = float(np.max(abs(extended-np.einsum('ij,kj->ik',states['coordinates'],ctx.obs,optimize=False))))
    if projection_error > cfg['numerical']['physical_replay_atol_mm']:
        raise ArithmeticError('Physical projection differs from explicit contraction')
    feature_error = float(np.max(abs(o.own_physics(data)-physical(data))))
    if feature_error > cfg['numerical']['physical_replay_atol_mm']:
        raise ArithmeticError('Teacher feature derivation mismatch')
    np.savez_compressed(out/'teacher.npz', **data)
    record = dict(status='valid_finite_budget_output', started_utc=start_time, completed_utc=o.utc(),
        fit_prefix=m, initialization_teacher=tid, theta=theta.tolist(), terminal_weight=weight,
        max_nfev=tc['max_nfev'], nfev=int(op.nfev), njev=int(op.njev),
        optimization_forward_calls=calls, postcheck_forward_calls=2,
        objective=float(2*op.cost), trajectory_objective=trajectory, terminal_objective=terminal,
        terminal_error_mm=(fitted[-1]-target[-1]).tolist(), optimizer_success=bool(op.success),
        optimizer_status=int(op.status), message=str(op.message), optimality=float(op.optimality),
        gtol_met=bool(op.optimality <= tc['gtol']), globally_optimal=False,
        prefix_state_replay_max_difference=max(differences), objective_difference=error,
        independent_feature_max_difference=feature_error, rollout_end_exclusive=end,
        context_matrix_max_difference=context_error,projection_max_difference=projection_error,
        warnings=[str(w.message) for w in [*context_log,*log]], elapsed_seconds=time.monotonic()-started,
        training_label_sha256=array_sha(y), training_forcing_sha256=array_sha(forcing[:m]))
    o.write_json(out/'fit.json', record)
    o.lock(out, 'complete.json', list(out.glob('*')), status='complete', new_bplus_fits=1,
           fit_prefix=m, numeric_valid=True, optimizer_success=bool(op.success))
    o.event(root, 'teacher_fit_completed', fit_prefix=m, forward_calls=calls,
            elapsed_seconds=record['elapsed_seconds'], optimizer_success=bool(op.success))
    print({'teacher_complete':m,'nfev':int(op.nfev),'converged':bool(op.success),
           'seconds':record['elapsed_seconds'],'terminal_error_mm':record['terminal_error_mm']}, flush=True)


def ensure_for_prefix(cfg, n):
    cached = o.bank(cfg)
    forcing, dates = o.read_forcing(c.ROOT/cfg['data'], 1461)
    for m in cfg['teacher_update']['new_prefixes']:
        if m < n:
            fit_one(cfg, m, cached, forcing, dates)
