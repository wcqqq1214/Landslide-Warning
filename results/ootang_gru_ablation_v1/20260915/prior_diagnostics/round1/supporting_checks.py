"""Check the diagnosis against original point arrays and the frozen results table."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
import overnight_graph.core as g

P = Path(__file__).parent
cfg = g.spec()
bank = g.bank(cfg)
y = g.read_labels(g.ROOT / cfg['data'], 1461)
t = pd.read_csv(P / 'training_summary.csv')
outer = pd.read_csv(P / 'outer_summary.csv')
points = pd.read_csv(P / 'outer_by_point.csv')
counter = pd.read_csv(P / 'teacher_counterfactual_by_point.csv')
old = pd.read_csv(g.ROOT / cfg['out'] / 'analysis/phase_summary.csv')
methods = {'GRU_LOCAL': 'GRU_LOCAL', 'GRU_GRAPH': 'GRU_GRAPH', 'COND_ATTN': 'COND_ATTN'}
comparisons = []
for n in [792, 972, 1168]:
    for arm, name in methods.items():
        a = outer.query('origin == @n and arm == @arm and step == 200 and seed == "ensemble"').iloc[0]
        b = old.query('origin == @n and method == @name').iloc[0]
        for metric in ['mae', 'rmse']:
            delta = float(abs(a[metric] - b[metric]))
            assert delta < 1e-10
            comparisons.append(delta)

monotonic = t.groupby(['origin', 'arm', 'seed']).apply(
    lambda a: bool((a.sort_values('step').objective.diff().dropna() < 0).all()),
    include_groups=False)
assert monotonic.all() and len(monotonic) == 36
end = outer.query('origin == 1168 and step == 200 and seed != "ensemble"')
assert len(end) == 9 and (end.rmse > end.bplus_rmse).all()
r = y[1168:] - bank[1168]['mean'][1168:1461]
r0 = y[1167] - bank[1168]['mean'][1167]
assert len(r) == 293 and np.all(r[:, 2] > 1e-6)
assert max(abs(r0)) < .02
negative_fractions = []
root = g.ROOT / cfg['out'] / 'base'
for seed in [0, 1, 2]:
    schedule = g.load_npz(root / f'origin_1168/GRU_LOCAL/seed_{seed}/schedule.npz')
    origins = schedule['origins'].reshape(-1)
    horizons = schedule['horizons'].reshape(-1, 16)
    values = np.concatenate([
        y[m + h - 1, 2] - bank[g.teacher_id(int(m))]['mean'][m + h - 1, 2]
        for m, h in zip(origins, horizons)
    ])
    negative_fractions.append(float((values < -1e-6).mean()))
for arm in methods:
    a = points.query('origin == 1168 and step == 200 and seed == "ensemble" and arm == @arm')
    np.testing.assert_allclose(a.initial_residual_mm, r0, atol=1e-10, rtol=0)
    np.testing.assert_allclose(a.true_residual_mean_mm, r.mean(0), atol=1e-10, rtol=0)
    assert a.query('point == "MJ3"').opposite_sign_fraction.iloc[0] == 1
    model_root = root if arm.startswith('GRU') else g.ROOT / g.read_json(g.ROOT / 'config/ootang_transformer_origin.v1_0.json')['out']
    mean = np.mean([np.load(model_root / f'origin_1168/{arm}/seed_{s}/e200_mean.npy') for s in [0, 1, 2]], axis=0)
    assert np.all(mean[:, 2] - bank[1168]['mean'][1168:1461, 2] < -1e-6)

# The lambda=1 per-query optimum has half the residual, not the full residual.
lam = counter.query('origin == 1168 and seed == "ensemble" and coherent').copy()
lam['half_target_mean_mm'] = lam.bplus_mean_residual_mm / 2
lam['error_to_half_target_mean_mm'] = lam.correction_mean_mm - lam.half_target_mean_mm
lam['half_target_change_mm'] = lam.required_correction_change_mm / 2
lam.to_csv(P / 'teacher_counterfactual_lambda_adjusted.csv', index=False, float_format='%.17g')

result = dict(status='passed', checked_utc=g.utc(), original_summary_metric_checks=len(comparisons),
              maximum_summary_difference=max(comparisons),
              decreasing_training_checkpoint_sequences=int(monotonic.sum()),
              final_e200_seed_forecasts_worse_than_bplus=int((end.rmse > end.bplus_rmse).sum()),
              mj3_final_positive_residual_days=int((r[:, 2] > 1e-6).sum()),
              mj3_training_negative_target_fraction=float(np.mean(negative_fractions)),
              final_first_day_true_residual_mm=r[0].tolist(),
              final_maximum_absolute_initial_residual_mm=float(max(abs(r0))),
              training_updates=0, physical_forwards=0,
              source_guard=json.loads((P / 'source_guard_receipt.json').read_text()))
(P / 'supporting_checks_receipt.json').write_text(json.dumps(result, indent=2))
files = sorted([*P.glob('*.csv'), *P.glob('*.py'), P/'receipt.json', P/'source_guard_receipt.json'])
(P / 'diagnostic_hashes.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, indent=2))
print(json.dumps(result))
