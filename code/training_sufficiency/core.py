"""Diagnostic panels, loss accounting, and the frozen continuation decision."""
import numpy as np
import pandas as pd
from backbone_anchor import core as base

o = base.old
ROOT = base.ROOT
CONFIG = ROOT / 'config/ootang_training_sufficiency.v1_0.json'
SOURCES = ROOT / 'docs/ootang_training_sufficiency_sources.v1.0.json'


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)['files']
    for path, digest in files.items():
        if o.sha(ROOT / path) != digest:
            raise ValueError('Changed frozen source: ' + path)
    if implementation:
        for path, digest in o.read_json(ROOT/spec()['out']/'implementation_lock.json')['files'].items():
            if o.sha(ROOT/path) != digest:
                raise ValueError('Changed diagnostic implementation: ' + path)
    return len(files)


def checkpoint(n, arm, seed, step):
    return base.checkpoint_folder(base.spec(), n, arm, seed) / f'e{step}.pt'


def panel(n, cfg):
    k, hcount = cfg['diagnostic']['panel_origins'], cfg['diagnostic']['panel_horizons']
    first = cfg['training']['first_origin']
    ms = first + (2*np.arange(k)+1)*(n-first)//(2*k)
    maximum = np.minimum(cfg['training']['max_horizon'], n-ms)
    hs = 1 + (2*np.arange(hcount)[None]+1)*maximum[:, None]//(2*hcount)
    assert np.all(ms < n) and np.all(ms[:, None]+hs-1 < n)
    return ms, hs


def direct_targets(bank, y, ms, hs, sc):
    """Independent algebraic target; never call the slow/fast training adapter."""
    result = []
    for m, distances in zip(ms, hs):
        teacher = bank[o.teacher_id(int(m))]['mean']
        ix = m + distances - 1
        if ix.max() >= len(y) or ix.max() >= sc['fit_prefix']:
            raise ValueError('Unmatured diagnostic target')
        r0 = y[m-1]-teacher[m-1]
        result.append((y[ix]-teacher[ix]-r0)/np.asarray(sc['unit']))
    return np.array(result)


def point_losses(q, target, unit):
    axes = tuple(range(q.ndim-1))
    mse = np.mean((q-target)**2, axis=axes)
    penalty = np.mean(q*q, axis=axes)
    lower = .5*np.mean(target*target, axis=axes)
    return dict(mse=mse, penalty=penalty, objective=mse+penalty,
                free_output_lower_bound=lower,
                excess_objective=2*np.mean((q-.5*target)**2, axis=axes),
                mae_mm=np.mean(np.abs(q-target), axis=axes)*unit,
                rmse_mm=np.sqrt(mse)*unit)


def mean_scores(prediction, truth):
    error = prediction-truth
    return dict(mae=np.abs(error).mean(0), rmse=np.sqrt((error*error).mean(0)))


def reduction(before, after):
    return (float(before)-float(after))/max(abs(float(before)), 1e-15)


def decision(training, historical, cfg):
    """Only fixed e100/e200 rows and matured historical paths can affect this gate."""
    tr = pd.DataFrame(training).set_index(['origin', 'arm', 'seed', 'step'])
    hi = pd.DataFrame(historical).assign(seed=lambda x: x.seed.astype(str))
    hi = hi.set_index(['origin', 'arm', 'seed', 'step'])
    rule = cfg['trigger']; before, after = rule['before'], rule['after']
    result = {}
    for arm in cfg['arms']:
        train_items, hist_items = [], []
        for n in cfg['diagnostic']['training_origins']:
            flags, differences = [], []
            for seed in cfg['seeds']:
                a, b = [tr.loc[(n, arm, seed, step)] for step in (before, after)]
                changes = {metric: reduction(a[metric], b[metric]) for metric in rule['training_metrics']}
                passed = all(v >= rule['training_relative_improvement'] for v in changes.values())
                flags.append(passed)
                differences.append(dict(seed=seed, reductions=changes, passed=passed))
            train_items.append(dict(origin=n, seed_details=differences, count=sum(flags),
                                    passed=sum(flags) >= rule['minimum_same_seed_training']))
        for n in cfg['diagnostic']['historical_origins']:
            a, b = [hi.loc[(n, arm, 'ensemble', step)] for step in (before, after)]
            changes = {metric: reduction(a[metric], b[metric]) for metric in rule['historical_metrics']}
            seed_details = []
            for seed in cfg['seeds']:
                sa, sb = [hi.loc[(n, arm, str(seed), step)] for step in (before, after)]
                seed_details.append(dict(seed=seed, both_improve=all(sb[k] < sa[k] for k in rule['historical_metrics'])))
            count = sum(v['both_improve'] for v in seed_details)
            hist_items.append(dict(origin=n, reductions=changes, seed_details=seed_details,
                                   seed_count=count,
                                   protected=all(v >= -rule['historical_window_max_regression'] for v in changes.values()),
                                   seed_pass=count >= rule['minimum_same_seed_historical']))
        average_changes = {}
        for metric in rule['historical_metrics']:
            a, b = [np.mean([hi.loc[(n, arm, 'ensemble', step), metric]
                             for n in cfg['diagnostic']['historical_origins']]) for step in (before, after)]
            average_changes[metric] = reduction(a, b)
        train_pass = sum(v['passed'] for v in train_items) >= rule['minimum_training_prefixes']
        average_pass = all(v >= rule['historical_average_relative_improvement'] for v in average_changes.values())
        history_pass = average_pass and all(v['protected'] and v['seed_pass'] for v in hist_items)
        result[arm] = dict(training=train_items, training_pass=train_pass,
                           historical=hist_items, historical_average_reductions=average_changes,
                           historical_average_pass=average_pass, historical_pass=history_pass,
                           combined_pass=train_pass and history_pass)
    return dict(version=cfg['version'], asof=cfg['diagnostic']['decision_asof'],
                candidate=rule['candidate'], before=before, after=after,
                extend=bool(result[rule['candidate']]['combined_pass']), arms=result,
                final_labels_used=False, checkpoint_selection=False,
                interpretation=rule['interpretation'])


def extended_schedule(n, seed, cfg):
    """Two 200-update RNG blocks preserve every original first-block draw."""
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(2):
        ms = rng.integers(432, n, size=(200, 4))
        hs = np.array([[rng.integers(1, min(293, n-int(m))+1, 16) for m in row] for row in ms])
        blocks.append((ms, hs))
    return tuple(np.concatenate([block[i] for block in blocks]) for i in (0, 1))
