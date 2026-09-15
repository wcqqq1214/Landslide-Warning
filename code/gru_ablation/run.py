"""Chronological fixed fits, immutable issue records, and full deferred scoring."""
import argparse
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c

o=c.old


def fit_one(cfg,bank,y,cache,n,end,arm,seed,sc):
    root=c.ROOT/cfg['out'];folder=root/f'origin_{n}'/arm/f'seed_{seed}'
    if (folder/'complete.json').exists():
        o.verify_lock(folder/'complete.json')
        return np.load(folder/'e200_mean.npy')
    folder.mkdir(parents=True,exist_ok=False)
    anchor=cfg['factors'][arm]['anchor'];boundary=cfg['factors'][arm]['boundary']
    model=c.create_model(seed)
    opt=cfg['optimizer'];optimizer=torch.optim.Adam(model.parameters(),lr=opt['lr'],betas=tuple(opt['betas']),eps=opt['eps'],weight_decay=opt['weight_decay'])
    ms,hs=c.schedule(n,seed,boundary,cfg)
    np.savez_compressed(folder/'schedule.npz',origins=ms,horizons=hs)
    o.event(root,'fit_started',origin=n,arm=arm,seed=seed,updates=cfg['updates'])
    started=time.monotonic()
    with (folder/'training.jsonl').open('w') as log:
        for step in range(cfg['updates']+1):
            o.check_deadline(cfg)
            if step:
                model.train();optimizer.zero_grad(set_to_none=True)
                values=c.tensors(bank,y,ms[step-1],hs[step-1],sc,anchor)
                output=c.learned(model,values,anchor)
                target=c.target(cache,bank,y,ms[step-1],hs[step-1],sc,anchor)
                mse=(output-target).square().mean();penalty=output.square().mean()
                loss=mse+cfg['residual_penalty']*penalty
                if not torch.isfinite(loss):raise ArithmeticError('Nonfinite loss')
                loss.backward()
                grad=float(sum(p.grad.square().sum() for p in model.parameters() if p.grad is not None).sqrt())
                if not np.isfinite(grad):raise ArithmeticError('Nonfinite gradient')
                optimizer.step()
                log.write(json.dumps(dict(step=step,mse=float(mse.detach()),penalty=float(penalty.detach()),loss=float(loss.detach()),grad_norm=grad),allow_nan=False)+'\n');log.flush()
            if step in cfg['checkpoints']:
                mu,change,r0=c.predict(model,bank,y,n,end,sc,anchor)
                torch.save(dict(state_dict=model.state_dict(),scaling=sc,seed=seed,arm=arm,step=step,training_prefix=n,anchor=anchor,boundary=boundary,config_sha256=o.sha(c.CONFIG)),folder/f'e{step}.pt')
                np.save(folder/f'e{step}_mean.npy',mu);np.save(folder/f'e{step}_learned_mm.npy',change)
                print(f'origin={n} {arm} seed={seed} update={step}',flush=True)
    elapsed=time.monotonic()-started
    o.lock(folder,'complete.json',list(folder.glob('*')),status='complete',new_fits=1,updates=cfg['updates'],elapsed_seconds=elapsed)
    o.event(root,'fit_completed',origin=n,arm=arm,seed=seed,updates=cfg['updates'],elapsed_seconds=elapsed)
    return mu


def train():
    cfg=c.spec();o.setup(cfg);c.guard();root=c.ROOT/cfg['out'];bank=o.bank(cfg);previous=None
    for n,end in zip(cfg['origins'],cfg['ends']):
        folder=root/f'origin_{n}'
        if (folder/'issue_lock.json').exists():
            o.verify_lock(folder/'issue_lock.json');previous=n;continue
        folder.mkdir(parents=True,exist_ok=True)
        y=o.labels(cfg,n,'fit_current_prefix_and_calibrate_previous_issued_errors')
        teacher=bank[o.teacher_id(n,True)];sc=o.scaling(teacher,y,cfg);cache=o.target_bank(bank,y)
        o.write_json(folder/'scaling.json',sc)
        prior=c.ROOT/cfg['prior_controls_out']/f'origin_{n}'
        pm=o.load_npz(prior/'means.npz');ps=o.load_npz(prior/'seeds.npz')
        means={k:pm[k] for k in [c.B,'DRIFT1','RR_COND']}
        seeds={k:ps[k] for k in means}
        r0=c.origin_residual(bank,y,[n],True)[0]
        means[c.BA]=means[c.B]+r0;seeds[c.BA]=np.repeat(means[c.BA][None],3,axis=0)
        for arm in cfg['arms']:
            seeds[arm]=np.stack([fit_one(cfg,bank,y,cache,n,end,arm,s,sc) for s in cfg['seeds']])
            means[arm]=seeds[arm].mean(0)
        np.savez_compressed(folder/'means.npz',**means);np.savez_compressed(folder/'seeds.npz',**seeds)
        files=[folder/f for f in ['means.npz','seeds.npz','scaling.json']]
        if previous is not None:
            previous_folder=root/f'origin_{previous}';o.verify_lock(previous_folder/'issue_lock.json')
            issued=o.load_npz(previous_folder/'means.npz')
            errors={m:y[previous+90:previous+180]-issued[m][90:180] for m in means}
            assert previous+180<=n and all(e.shape==(90,4) for e in errors.values())
            sigmas={m:np.maximum(np.sqrt(np.mean(e*e,axis=0)),cfg['calibration']['sigma_floor_mm']) for m,e in errors.items()}
            np.savez_compressed(folder/'calibration_errors.npz',**errors);np.savez_compressed(folder/'sigmas.npz',**sigmas)
            o.write_json(folder/'calibration.json',dict(previous_origin=previous,start=previous+90,end=previous+180,matured_before=n,count=90,gap=n-previous-180))
            files += [folder/f for f in ['calibration_errors.npz','sigmas.npz','calibration.json']]
        o.lock(folder,'issue_lock.json',files,status='bootstrap' if previous is None else 'issued',origin=n,end=end,displacement_feedback=False)
        o.event(root,'trajectory_issued',origin=n,end=end,lock_sha256=o.sha(folder/'issue_lock.json'))
        previous=n
    done=[o.read_json(p) for p in root.glob('origin_*/*/seed_*/complete.json')]
    assert len(done)==cfg['max_new_fits'] and sum(d['updates'] for d in done)==cfg['max_optimizer_updates']
    o.lock(root,'training_complete.json',[root/f'origin_{n}/issue_lock.json' for n in cfg['origins']],status='complete',new_fits=len(done),updates=sum(d['updates'] for d in done))


def score():
    cfg=c.spec();c.guard();root=c.ROOT/cfg['out'];o.verify_lock(root/'training_complete.json')
    for n in cfg['origins']:o.verify_lock(root/f'origin_{n}/issue_lock.json')
    if (root/'analysis_lock.json').exists():o.verify_lock(root/'analysis_lock.json');return
    y=o.labels(cfg,1461,'all_paths_issued_before_full_scoring');_,dates=o.read_forcing(c.ROOT/cfg['data'],1461)
    out=root/'analysis';out.mkdir(exist_ok=False)
    rows=[];summaries=[];seedrows=[];seedpoints=[];daily=[];pairs=[];endpoints=[]
    for n,end in list(zip(cfg['origins'],cfg['ends']))[1:]:
        folder=root/f'origin_{n}';means=o.load_npz(folder/'means.npz');seeds=o.load_npz(folder/'seeds.npz');sigmas=o.load_npz(folder/'sigmas.npz')
        by={}
        for method,mu in means.items():
            by[method]=o.scores(y[n:end],mu,sigmas[method])
            rows.extend(dict(origin=n,method=method,**r) for r in by[method]);summaries.append(dict(origin=n,method=method,**o.summarize(by[method])))
            for seed,sm in enumerate(seeds[method]):
                sr=o.scores(y[n:end],sm,sigmas[method]);seedrows.append(dict(origin=n,method=method,seed=seed,**o.summarize(sr)))
                seedpoints.extend(dict(origin=n,method=method,seed=seed,**r) for r in sr)
            for si,sm in [('ensemble',mu)]+[(str(i),v) for i,v in enumerate(seeds[method])]:
                for h in cfg['reporting']['boundary_diagnostic_horizons']:
                    for p,point in enumerate(cfg['points']):
                        e=float(sm[h-1,p]-y[n+h-1,p]);endpoints.append(dict(origin=n,method=method,seed=si,horizon=h,point=point,error=e,absolute_error=abs(e),squared_error=e*e))
            for h in range(293):
                for p,point in enumerate(cfg['points']):
                    daily.append(dict(origin=n,issue_date=dates[n-1],target_index=n+h,date=dates[n+h],distance=h+1,method=method,point=point,observed=y[n+h,p],mean=mu[h,p],sigma=sigmas[method][p]))
        comparisons=[(a,r) for a in cfg['arms'] for r in cfg['controls']]+[tuple(v) for v in cfg['reporting']['paired_edges']]
        for a,b in comparisons:
            flags=[]
            for seed in cfg['seeds']:
                sa=o.summarize(o.scores(y[n:end],seeds[a][seed]));sb=o.summarize(o.scores(y[n:end],seeds[b][seed]));flags.append(all(sa[k]<sb[k] for k in ['mae','rmse']))
            result=o.effect(by[a],by[b],cfg)
            pairs.append(dict(origin=n,candidate=a,reference=b,seed_flags=flags,seed_both_improve=sum(flags),seed_agreement_pass=sum(flags)>=cfg['effect']['minimum_seed_agreement'],**result))
    for name,data in [('metrics_by_point',rows),('phase_summary',summaries),('seed_summary',seedrows),('seed_metrics_by_point',seedpoints),('daily_predictions',daily),('endpoint_errors',endpoints)]:
        pd.DataFrame(data).to_csv(out/f'{name}.csv',index=False,float_format='%.17g')
    metrics=['mae','rmse','crps','interval_score90','coverage90','width90']
    for name,data,keys in [('factorial_summary',summaries,['origin']),('factorial_points',rows,['origin','point']),('factorial_seeds',seedrows,['origin','seed']),('factorial_seed_points',seedpoints,['origin','seed','point'])]:
        pd.DataFrame(c.factorial_rows(pd.DataFrame(data),keys,metrics)).to_csv(out/f'{name}.csv',index=False,float_format='%.17g')
    o.write_json(out/'pairing.json',pairs)
    o.write_json(out/'outcome.json',dict(all_prescribed_windows_complete=True,new_fits=48,updates=9600,summary_rows=len(summaries),point_rows=len(rows),seed_rows=len(seedrows),daily_rows=len(daily),endpoint_rows=len(endpoints),selection=False))
    o.lock(root,'analysis_lock.json',list(out.glob('*')),status='complete');o.event(root,'scoring_complete',summary_rows=len(summaries))
    print(pd.DataFrame(summaries)[['origin','method','mae','rmse','crps']].to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['train','score']);args=parser.parse_args()
    try:
        train() if args.command=='train' else score()
    except Exception:
        o.write_json(c.ROOT/c.spec()['out']/f'error_{args.command}_{int(time.time())}.json',dict(time_utc=o.utc(),error=traceback.format_exc()))
        raise
