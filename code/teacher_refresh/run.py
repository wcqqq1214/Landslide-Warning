"""Chronological teacher updates and paired fits; all paths issued before scoring."""
import argparse
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c
from .teachers import ensure_for_prefix

o = c.o


def fit_one(cfg, teachers, y, n, end, arm, seed, sc):
    root = c.ROOT/cfg['out']; dest = root/f'origin_{n}'/arm/f'seed_{seed}'
    if (dest/'complete.json').exists():
        o.verify_lock(dest/'complete.json')
        return np.load(dest/'e200_mean.npy')
    dest.mkdir(parents=True, exist_ok=False)
    model = c.create_model(seed)
    opt = cfg['optimizer']
    optimizer = torch.optim.Adam(model.parameters(), lr=opt['lr'], betas=tuple(opt['betas']),
                                 eps=opt['eps'], weight_decay=opt['weight_decay'])
    ms, hs = c.schedule(n, seed, cfg)
    np.savez_compressed(dest/'schedule.npz', origins=ms, horizons=hs)
    teacher_ids = sorted({int(c.teacher_for(teachers,m,arm)['teacher_fit_prefix']) for m in ms.ravel()})
    assert all(t < n for t in teacher_ids)
    o.event(root, 'fit_started', origin=n, arm=arm, seed=seed, updates=cfg['updates'], teachers=teacher_ids)
    started = time.monotonic()
    with (dest/'training.jsonl').open('w') as log:
        for step in range(cfg['updates']+1):
            o.check_deadline(cfg)
            if step:
                model.train(); optimizer.zero_grad(set_to_none=True)
                v = c.tensors(teachers, y, ms[step-1], hs[step-1], sc, arm)
                q = c.learned(model, v)
                target = c.target(teachers, y, ms[step-1], hs[step-1], sc, arm)
                mse = (q-target).square().mean(); penalty = q.square().mean()
                loss = mse+cfg['residual_penalty']*penalty
                if not torch.isfinite(loss):
                    raise ArithmeticError('Nonfinite neural objective')
                loss.backward()
                grad = float(sum(p.grad.square().sum() for p in model.parameters() if p.grad is not None).sqrt())
                if not np.isfinite(grad):
                    raise ArithmeticError('Nonfinite neural gradient')
                optimizer.step()
                log.write(json.dumps(dict(step=step, mse=float(mse.detach()), penalty=float(penalty.detach()),
                    loss=float(loss.detach()), grad_norm=grad), allow_nan=False)+'\n'); log.flush()
            if step in cfg['checkpoints']:
                mu, change, r0 = c.predict(model, teachers, y, n, end, sc)
                torch.save(dict(state_dict=model.state_dict(), optimizer_state_dict=optimizer.state_dict(),
                    scaling=sc, seed=seed, arm=arm, step=step, training_prefix=n,
                    teacher_ids=teacher_ids, anchor=True, config_sha256=o.sha(c.CONFIG)), dest/f'e{step}.pt')
                np.save(dest/f'e{step}_mean.npy', mu)
                np.save(dest/f'e{step}_learned_mm.npy', change)
                print(f'origin={n} {arm} seed={seed} update={step}', flush=True)
    elapsed = time.monotonic()-started
    o.lock(dest, 'complete.json', list(dest.glob('*')), status='complete', new_fits=1,
           updates=cfg['updates'], elapsed_seconds=elapsed)
    o.event(root, 'fit_completed', origin=n, arm=arm, seed=seed, updates=cfg['updates'], elapsed_seconds=elapsed)
    return mu


def train():
    cfg = c.spec(); o.setup(cfg); c.guard(); root = c.ROOT/cfg['out']
    previous = None
    for n, end in zip(cfg['origins'], cfg['ends']):
        folder = root/f'origin_{n}'
        if (folder/'issue_lock.json').exists():
            o.verify_lock(folder/'issue_lock.json'); previous = n; continue
        ensure_for_prefix(cfg, n)
        teachers = c.bank(cfg, n)
        for m in c.grid(n, cfg):
            for arm in cfg['arms']:
                t = c.teacher_for(teachers, m, arm)
                assert int(t['teacher_fit_prefix']) <= m < n
        o.event(root, 'teachers_ready_for_neural_prefix', origin=n, training_origins=c.grid(n,cfg).tolist())
        folder.mkdir(parents=True, exist_ok=True)
        y = o.labels(cfg, n, 'neural_fit_prefix_and_mature_previous_errors')
        teacher = c.teacher_for(teachers, n, cfg['arms'][0], True)
        sc = o.scaling(teacher, y, cfg)
        o.write_json(folder/'scaling.json', sc)
        prior = c.ROOT/cfg['prior_controls_out']/f'origin_{n}'
        pm, ps = o.load_npz(prior/'means.npz'), o.load_npz(prior/'seeds.npz')
        means = {k:pm[k] for k in (c.B, 'DRIFT1', 'RR_COND')}
        seeds = {k:ps[k] for k in means}
        r0 = y[n-1]-teacher['mean'][n-1]
        means[c.BA] = means[c.B]+r0
        seeds[c.BA] = np.repeat(means[c.BA][None], len(cfg['seeds']), axis=0)
        for arm in cfg['arms']:
            seeds[arm] = np.stack([fit_one(cfg,teachers,y,n,end,arm,s,sc) for s in cfg['seeds']])
            means[arm] = seeds[arm].mean(0)
        np.savez_compressed(folder/'means.npz', **means)
        np.savez_compressed(folder/'seeds.npz', **seeds)
        paths = [folder/f for f in ('means.npz','seeds.npz','scaling.json')]
        if previous is not None:
            pf = root/f'origin_{previous}'; o.verify_lock(pf/'issue_lock.json')
            issued = o.load_npz(pf/'means.npz')
            assert previous+180 <= n
            errors = {m:y[previous+90:previous+180]-issued[m][90:180] for m in means}
            assert all(e.shape == (90,4) for e in errors.values())
            sigmas = {m:np.maximum(np.sqrt(np.mean(e*e, axis=0)),cfg['calibration']['sigma_floor_mm']) for m,e in errors.items()}
            np.savez_compressed(folder/'calibration_errors.npz', **errors)
            np.savez_compressed(folder/'sigmas.npz', **sigmas)
            o.write_json(folder/'calibration.json', dict(previous_origin=previous,start=previous+90,
                end=previous+180,matured_before=n,count=90,gap=n-previous-180))
            paths += [folder/f for f in ('calibration_errors.npz','sigmas.npz','calibration.json')]
        o.lock(folder,'issue_lock.json',paths,status='bootstrap' if previous is None else 'issued',
               origin=n,end=end,displacement_feedback=False)
        o.event(root,'trajectory_issued',origin=n,end=end,lock_sha256=o.sha(folder/'issue_lock.json'))
        previous=n
    done = [o.read_json(p) for p in root.glob('origin_*/*/seed_*/complete.json')]
    physical = [o.read_json(p) for p in root.glob('teachers/fit_*/complete.json')]
    assert len(done)==cfg['max_new_fits'] and sum(d['updates'] for d in done)==cfg['max_optimizer_updates']
    assert len(physical)==cfg['max_bplus_refits']
    o.lock(root,'training_complete.json',[root/f'origin_{n}/issue_lock.json' for n in cfg['origins']]+
           list(root.glob('teachers/fit_*/complete.json')),status='complete',new_fits=len(done),
           updates=sum(d['updates'] for d in done),new_bplus_fits=len(physical))


def score():
    cfg=c.spec(); c.guard(); root=c.ROOT/cfg['out']; o.verify_lock(root/'training_complete.json')
    for n in cfg['origins']:
        o.verify_lock(root/f'origin_{n}/issue_lock.json')
    if (root/'analysis_lock.json').exists():
        o.verify_lock(root/'analysis_lock.json'); return
    y=o.labels(cfg,1461,'all_issued_before_full_scoring')
    _,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    out=root/'analysis';out.mkdir(exist_ok=False)
    rows=[]; summaries=[]; sr=[]; sp=[]; daily=[]; pairs=[]; endpoints=[]; differences=[]
    for n,end in list(zip(cfg['origins'],cfg['ends']))[1:]:
        folder=root/f'origin_{n}'
        means=o.load_npz(folder/'means.npz');seeds=o.load_npz(folder/'seeds.npz');sigmas=o.load_npz(folder/'sigmas.npz')
        by={}
        for method,mu in means.items():
            by[method]=o.scores(y[n:end],mu,sigmas[method])
            rows.extend(dict(origin=n,method=method,**r) for r in by[method])
            summaries.append(dict(origin=n,method=method,**o.summarize(by[method])))
            for seed,sm in enumerate(seeds[method]):
                values=o.scores(y[n:end],sm,sigmas[method])
                sr.append(dict(origin=n,method=method,seed=seed,**o.summarize(values)))
                sp.extend(dict(origin=n,method=method,seed=seed,**v) for v in values)
            for ident,sm in [('ensemble',mu)]+[(str(i),v) for i,v in enumerate(seeds[method])]:
                for h in cfg['reporting']['boundary_diagnostic_horizons']:
                    for p,point in enumerate(cfg['points']):
                        e=float(sm[h-1,p]-y[n+h-1,p])
                        endpoints.append(dict(origin=n,method=method,seed=ident,horizon=h,point=point,
                                              error=e,absolute_error=abs(e),squared_error=e*e))
            for h in range(293):
                for p,point in enumerate(cfg['points']):
                    daily.append(dict(origin=n,issue_date=dates[n-1],target_index=n+h,date=dates[n+h],
                        distance=h+1,method=method,point=point,observed=y[n+h,p],mean=mu[h,p],sigma=sigmas[method][p]))
        comparisons=[(a,b) for a in cfg['arms'] for b in cfg['controls']]+[('G_REFRESH','G_CACHED')]
        for a,b in comparisons:
            flags=[]
            for seed in cfg['seeds']:
                va=o.summarize(o.scores(y[n:end],seeds[a][seed]))
                vb=o.summarize(o.scores(y[n:end],seeds[b][seed]))
                flags.append(all(va[k]<vb[k] for k in ('mae','rmse')))
            pairs.append(dict(origin=n,candidate=a,reference=b,seed_flags=flags,seed_both_improve=sum(flags),
                seed_agreement_pass=sum(flags)>=cfg['effect']['minimum_seed_agreement'],**o.effect(by[a],by[b],cfg)))
        for ident,group in [('ensemble',means)]+[(str(s),{k:v[s] for k,v in seeds.items()}) for s in cfg['seeds']]:
            va=o.scores(y[n:end],group['G_REFRESH'],sigmas['G_REFRESH'])
            vb=o.scores(y[n:end],group['G_CACHED'],sigmas['G_CACHED'])
            for point,a,b in [('average',o.summarize(va),o.summarize(vb))]+[(cfg['points'][p],va[p],vb[p]) for p in range(4)]:
                for metric in ('mae','rmse','crps','interval_score90','coverage90','width90'):
                    differences.append(dict(origin=n,seed=ident,point=point,metric=metric,cached=b[metric],
                        refreshed=a[metric],difference=a[metric]-b[metric],relative_change=(a[metric]/b[metric]-1) if b[metric] else None))
    for name,values in [('metrics_by_point',rows),('phase_summary',summaries),('seed_summary',sr),
                        ('seed_metrics_by_point',sp),('daily_predictions',daily),('endpoint_errors',endpoints),
                        ('paired_differences',differences)]:
        pd.DataFrame(values).to_csv(out/f'{name}.csv',index=False,float_format='%.17g')
    o.write_json(out/'pairing.json',pairs)
    policy=[p for p in pairs if p['reference']=='G_CACHED']
    stable=all(p['mean_pass'] and p['seed_agreement_pass'] for p in policy)
    bplus={a:[p for p in pairs if p['candidate']==a and p['reference']==c.B] for a in cfg['arms']}
    outcome=dict(all_prescribed_windows_complete=True,new_fits=cfg['max_new_fits'],updates=cfg['max_optimizer_updates'],
        new_bplus_fits=cfg['max_bplus_refits'],summary_rows=len(summaries),point_rows=len(rows),seed_rows=len(sr),
        daily_rows=len(daily),endpoint_rows=len(endpoints),stable_mean_policy=stable,
        joint_bplus_pass_counts={a:sum(p['joint_pass'] for p in ps) for a,ps in bplus.items()},
        replacement_goal_met=any(all(p['joint_pass'] for p in ps) for ps in bplus.values()),selection=False)
    o.write_json(out/'outcome.json',outcome)
    o.lock(root,'analysis_lock.json',list(out.glob('*')),status='complete')
    o.event(root,'scoring_complete',**outcome)
    print(pd.DataFrame(summaries)[['origin','method','mae','rmse','crps']].to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['train','score']);args=parser.parse_args()
    try:
        train() if args.command=='train' else score()
    except Exception:
        o.write_json(c.ROOT/c.spec()['out']/f'error_{args.command}_{int(time.time())}.json',
                     dict(time_utc=o.utc(),error=traceback.format_exc()))
        raise
