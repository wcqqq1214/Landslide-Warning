"""Evaluate saved weights on fixed panels and mature historical paths; never fit."""
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c


def main():
    cfg = c.spec(); c.o.setup(cfg); sources = c.guard()
    root = c.ROOT/cfg['out']; out = root/'diagnostic'; out.mkdir(exist_ok=False)
    assert c.o.read_json(root/'preflight/receipt.json')['status'] == 'passed'
    bank = c.o.bank(cfg); train_rows, train_points, reload_checks = [], [], []
    started = time.monotonic()
    for n in cfg['diagnostic']['training_origins']:
        c.o.check_deadline(cfg)
        y = c.o.labels(cfg, n, 'fixed_training_panel_no_optimizer')
        sc = c.o.read_json(c.ROOT/cfg['source_experiment']/f'origin_{n}/scaling.json')
        ms, hs = c.panel(n, cfg); target = c.direct_targets(bank, y, ms, hs, sc)
        unit = np.asarray(sc['unit']); arrays = dict(origins=ms, horizons=hs, target=target, unit=unit)
        metadata = [dict(slot=int(i), origin=int(m), history_last=int(m)-1,
                         teacher_prefix=c.o.teacher_id(int(m)), target_max=int((m+hs[i]-1).max()),
                         training_prefix=n, scaling_prefix=sc['fit_prefix']) for i, m in enumerate(ms)]
        pd.DataFrame(metadata).to_csv(out/f'panel_{n}.csv', index=False)
        bs = cfg['diagnostic']['batch_origins']
        batches = [(start, c.base.tensors(bank, y, ms[start:start+bs], hs[start:start+bs], sc, True))
                   for start in range(0, len(ms), bs)]
        for arm in cfg['arms']:
            for seed in cfg['seeds']:
                for step in cfg['diagnostic']['checkpoints']:
                    c.o.check_deadline(cfg)
                    path = c.checkpoint(n, arm, seed, step)
                    model, saved_scale, saved = c.base.reload(path)
                    assert saved_scale == sc and saved['training_prefix'] == n
                    assert saved['seed'] == seed and saved['arm'] == arm and saved['step'] == step
                    assert saved['anchor'] and not saved['boundary']
                    initial = {k: v.detach().clone() for k, v in model.state_dict().items()}
                    model.eval(); model.zero_grad(set_to_none=True); outputs = []
                    for start, values in batches:
                        q = c.base.learned(model, values, True)
                        t = torch.from_numpy(target[start:start+bs])
                        loss = ((q-t).square().mean()+q.square().mean())/len(batches)
                        loss.backward()  # Diagnostic gradient only; no optimizer exists.
                        outputs.append(q.detach().numpy())
                    grad = float(sum(p.grad.square().sum() for p in model.parameters() if p.grad is not None).sqrt())
                    assert np.isfinite(grad)
                    for name, value in model.state_dict().items():
                        assert torch.equal(value, initial[name]), name
                    q = np.concatenate(outputs); arrays[f'{arm}_s{seed}_e{step}'] = q
                    losses = c.point_losses(q, target, unit)
                    np.testing.assert_allclose(losses['objective'], losses['excess_objective']+losses['free_output_lower_bound'], atol=1e-12, rtol=0)
                    identity = dict(origin=n, arm=arm, seed=seed, step=step)
                    train_rows.append(dict(identity, gradient_norm=grad, **{k: float(v.mean()) for k, v in losses.items()}))
                    for p, point in enumerate(cfg['points']):
                        train_points.append(dict(identity, point=point, **{k: float(v[p]) for k, v in losses.items()}))
                    mu = c.base.predict(model, bank, y, n, n+293, sc, True)[0]
                    original = np.load(path.with_name(f'e{step}_mean.npy'))
                    np.testing.assert_array_equal(mu, original)
                    reload_checks.append(dict(identity, checkpoint=str(path.relative_to(c.ROOT)),
                                              checkpoint_sha256=c.o.sha(path), values=int(mu.size), difference_mm=0.))
        np.savez_compressed(out/f'panel_{n}.npz', **arrays)
        print(f'fixed panel n={n}: 24 checkpoints, no updates', flush=True)

    y = c.o.labels(cfg, cfg['diagnostic']['read_label_max'], 'mature_historical_paths_only_no_final_labels')
    rows, points, daily, path_arrays = [], [], [], {}
    for n, end in zip(cfg['diagnostic']['historical_origins'], cfg['diagnostic']['historical_ends']):
        assert end <= len(y) and end-n == 293
        path_arrays[f'truth_{n}'] = y[n:end]
        for arm in cfg['arms']:
            for step in cfg['diagnostic']['checkpoints']:
                predictions = np.stack([np.load(c.checkpoint(n, arm, seed, step).with_name(f'e{step}_mean.npy')) for seed in cfg['seeds']])
                path_arrays[f'{n}_{arm}_e{step}'] = predictions
                for seed, mu in [('ensemble', predictions.mean(0))]+[(str(s), predictions[s]) for s in cfg['seeds']]:
                    metrics = c.mean_scores(mu, y[n:end]); identity = dict(origin=n, arm=arm, seed=seed, step=step)
                    rows.append(dict(identity, **{k: float(v.mean()) for k, v in metrics.items()}))
                    for p, point in enumerate(cfg['points']):
                        points.append(dict(identity, point=point, n=293, **{k: float(v[p]) for k, v in metrics.items()}))
                    if seed == 'ensemble':
                        for day in range(293):
                            for p, point in enumerate(cfg['points']):
                                daily.append(dict(identity, target_index=n+day, distance=day+1, point=point,
                                                  observed=float(y[n+day,p]), prediction=float(mu[day,p])))
    np.savez_compressed(out/'historical_predictions.npz', **path_arrays)
    for name, values in [('training_summary', train_rows), ('training_points', train_points),
                         ('historical_summary', rows), ('historical_points', points), ('historical_daily', daily)]:
        pd.DataFrame(values).to_csv(out/f'{name}.csv', index=False, float_format='%.17g')
    c.o.write_json(out/'reload_checks.json', reload_checks)
    gate = c.decision(train_rows, rows, cfg); c.o.write_json(out/'trigger.json', gate)
    c.o.write_json(out/'receipt.json', dict(status='complete', checkpoints=len(reload_checks), source_files=sources,
        training_rows=len(train_rows), training_point_rows=len(train_points), historical_rows=len(rows),
        historical_point_rows=len(points), daily_rows=len(daily), new_fits=0, optimizer_updates=0,
        physical_forwards=0, max_label_rows=1168, elapsed_seconds=time.monotonic()-started, extend=gate['extend']))
    c.o.lock(root, 'diagnostic_lock.json', list(out.glob('*')), status='complete', extend=gate['extend'])
    c.o.event(root, 'diagnostic_locked', extend=gate['extend'], optimizer_updates=0)
    print(json.dumps(gate, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        c.o.write_json(c.ROOT/c.spec()['out']/f'diagnostic_error_{int(time.time())}.json', dict(error=traceback.format_exc(), time_utc=c.o.utc()))
        raise
