"""Leakage, loss identity, schedule-prefix and gate-boundary checks; no updates."""
import copy
import json
import numpy as np
import torch
from . import core as c


def main():
    cfg = c.spec(); c.o.setup(cfg); root = c.ROOT/cfg['out']
    out = root/'preflight'; out.mkdir(parents=True, exist_ok=False)
    records = []

    def close(name, a, b, atol=1e-10):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), name
        difference = float(np.max(np.abs(a-b))) if a.size else 0.
        assert difference <= atol, (name, difference)
        records.append(dict(check=name, values=int(a.size), max_difference=difference))

    count = c.guard(False); bank = c.o.bank(cfg)
    y = c.o.labels(cfg, 1168, 'preflight_training_prefix_only')
    for n in cfg['origins']:
        ms, hs = c.panel(n, cfg)
        assert ms.shape == (128,) and hs.shape == (128, 32)
        assert np.all(ms[:, None]+hs-1 < n) and all(c.o.teacher_id(int(m)) <= m for m in ms)
        expected_m = np.array([432+int(np.floor((i+.5)*(n-432)/128)) for i in range(128)])
        expected_h = np.array([[1+int(np.floor((j+.5)*min(293, n-int(m))/32)) for j in range(32)] for m in ms])
        close(f'{n}/panel_origin', ms, expected_m, 0)
        close(f'{n}/panel_horizon', hs, expected_h, 0)
        for seed in cfg['seeds']:
            exm, exh = c.extended_schedule(n, seed, cfg)
            assert np.all(exm[:, :, None]+exh-1 < n)
            for arm in cfg['arms']:
                source = c.o.load_npz(c.checkpoint(n, arm, seed, 200).parent/'schedule.npz')
                close(f'{n}/{seed}/{arm}/original200_origins', exm[:200], source['origins'], 0)
                close(f'{n}/{seed}/{arm}/original200_horizons', exh[:200], source['horizons'], 0)
    n = 792
    ms = np.array([432, 612, 687]); hs = np.array([[1, 7, 80], [1, 7, 80], [1, 7, 80]])
    for arm in cfg['arms']:
        model, sc, saved = c.base.reload(c.checkpoint(n, arm, 0, 200))
        model.eval(); values = c.base.tensors(bank, y[:n], ms, hs, sc, True)
        target = c.direct_targets(bank, y[:n], ms, hs, sc)
        close(arm+'/direct_target', target, c.base.target(c.o.target_bank(bank, y[:n]), bank, y[:n], ms, hs, sc, True).numpy())
        with torch.no_grad():
            q = c.base.learned(model, values, True).numpy()
            singles = np.concatenate([c.base.learned(model, c.base.tensors(bank, y[:n], [m], h[None], sc, True), True).numpy() for m, h in zip(ms, hs)])
        close(arm+'/batch_single', q, singles)
        loss = c.point_losses(q, target, np.asarray(sc['unit']))
        close(arm+'/loss_identity', loss['objective'], loss['free_output_lower_bound']+loss['excess_objective'])
        for m, h in zip(ms, hs):
            poison = y[:n].copy(); poison[m:] = 1e12
            actual = c.base.tensors(bank, y[:n], [m], h[None], sc, True)
            poisoned = c.base.tensors(bank, poison, [m], h[None], sc, True)
            for i, (a, b) in enumerate(zip(actual, poisoned)):
                close(f'{arm}/{m}/no_future_y/{i}', a.numpy(), b.numpy(), 0)
        try:
            c.direct_targets(bank, y[:n], [687], np.array([[106]]), sc)
        except ValueError:
            records.append(dict(check=arm+'/unmatured_target_rejected', values=1, max_difference=0.))
        else:
            raise AssertionError('Unmatured label accepted')
        assert 'optimizer_state_dict' not in saved

    # A favorable history alone, a lower penalty alone, or final-window rows cannot trigger.
    train, history = [], []
    for arm in cfg['arms']:
        for n in cfg['origins']:
            for seed in cfg['seeds']:
                for step, value in [(100, 1.), (200, .8)]:
                    train.append(dict(origin=n, arm=arm, seed=seed, step=step, mse=value, objective=2*value))
        for n in cfg['diagnostic']['historical_origins']:
            for seed in ['ensemble', '0', '1', '2']:
                for step, value in [(100, 10.), (200, 8.)]:
                    history.append(dict(origin=n, arm=arm, seed=seed, step=step, mae=value, rmse=value))
    good = c.decision(train, history, cfg); assert good['extend']
    bad = copy.deepcopy(train)
    for row in bad:
        if row['step'] == 200: row['mse'] = 1.1
    assert not c.decision(bad, history, cfg)['extend']
    bad = copy.deepcopy(history)
    for row in bad:
        if row['origin'] == 792 and row['step'] == 200: row.update(mae=11., rmse=11.)
    assert not c.decision(train, bad, cfg)['extend']
    extra = copy.deepcopy(history)
    for row in history:
        if row['origin'] == 792: extra.append(dict(row, origin=1168, mae=1e10, rmse=1e10))
    assert c.decision(train, extra, cfg) == good
    records.append(dict(check='gate_positive_negative_and_final_row_invariance', values=4, max_difference=0.))
    c.o.write_json(out/'checks.json', records)
    c.o.write_json(out/'receipt.json', dict(status='passed', source_files=count, checks=len(records),
                     values=sum(r['values'] for r in records), max_difference=max(r['max_difference'] for r in records),
                     new_fits=0, optimizer_updates=0, max_label_rows=1168))
    print(json.dumps(c.o.read_json(out/'receipt.json')), flush=True)


if __name__ == '__main__':
    main()
