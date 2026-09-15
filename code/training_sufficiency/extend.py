"""One gated 200-versus-400 budget comparison, with exact optimizer replay."""
import argparse
import json
import time
import traceback
import numpy as np
import pandas as pd
import torch
from . import core as c


def extension_guard():
    cfg=c.spec(); c.guard(); root=c.ROOT/cfg['out']
    c.o.verify_lock(root/'diagnostic_lock.json')
    audit=c.o.verify_lock(root/'diagnostic_audit_lock.json')
    gate=c.o.read_json(root/'diagnostic/trigger.json')
    assert audit['status']=='passed' and audit['extend'] and gate['extend']
    for path,digest in c.o.read_json(root/'extension_implementation_lock.json')['files'].items():
        assert c.o.sha(c.ROOT/path)==digest,path
    return cfg,root,root/'extension'


def fit(cfg,root,output,bank,y,cache,n,arm,seed,sc):
    dest=output/f'origin_{n}'/arm/f'seed_{seed}'
    dest.mkdir(parents=True,exist_ok=False)
    model=c.base.create_model(seed,arm)
    opt=cfg['optimizer']
    optimizer=torch.optim.Adam(model.parameters(),lr=opt['lr'],betas=tuple(opt['betas']),eps=opt['eps'],weight_decay=opt['weight_decay'])
    ms,hs=c.extended_schedule(n,seed,cfg)
    np.savez_compressed(dest/'schedule.npz',origins=ms,horizons=hs)
    original=c.o.load_npz(c.checkpoint(n,arm,seed,200).parent/'schedule.npz')
    np.testing.assert_array_equal(ms[:200],original['origins']);np.testing.assert_array_equal(hs[:200],original['horizons'])
    oldlog=[json.loads(s) for s in c.checkpoint(n,arm,seed,200).with_name('training.jsonl').read_text().splitlines()]
    c.o.event(root,'extension_fit_started',origin=n,arm=arm,seed=seed,updates=400)
    started=time.monotonic(); replay=[]
    with (dest/'training.jsonl').open('w') as stream:
        for step in range(401):
            c.o.check_deadline(cfg)
            if step:
                model.train();optimizer.zero_grad(set_to_none=True)
                values=c.base.tensors(bank,y,ms[step-1],hs[step-1],sc,True)
                q=c.base.learned(model,values,True)
                target=c.base.target(cache,bank,y,ms[step-1],hs[step-1],sc,True)
                mse=(q-target).square().mean();penalty=q.square().mean()
                loss=mse+cfg['residual_penalty']*penalty
                if not torch.isfinite(loss):raise ArithmeticError('Nonfinite objective')
                loss.backward()
                grad=float(sum(p.grad.square().sum() for p in model.parameters() if p.grad is not None).sqrt())
                if not np.isfinite(grad):raise ArithmeticError('Nonfinite gradient')
                optimizer.step()
                row=dict(step=step,mse=float(mse.detach()),penalty=float(penalty.detach()),loss=float(loss.detach()),grad_norm=grad)
                if step<=200:
                    for key in ['mse','penalty','loss','grad_norm']:
                        if row[key]!=oldlog[step-1][key]:
                            raise AssertionError(('Original optimization trace changed',n,arm,seed,step,key,row[key],oldlog[step-1][key]))
                stream.write(json.dumps(row,allow_nan=False)+'\n');stream.flush()
            if step in [0,200,400]:
                mu,change,_=c.base.predict(model,bank,y,n,n+293,sc,True)
                if step in [0,200]:
                    old=torch.load(c.checkpoint(n,arm,seed,step),map_location='cpu',weights_only=True)
                    for name,value in model.state_dict().items():
                        assert torch.equal(value,old['state_dict'][name]),(n,arm,seed,step,name)
                    np.testing.assert_array_equal(mu,np.load(c.checkpoint(n,arm,seed,step).with_name(f'e{step}_mean.npy')))
                    replay.append(dict(step=step,all_parameters_exact=True,prediction_difference_mm=0.))
                saved=dict(state_dict=model.state_dict(),optimizer_state_dict=optimizer.state_dict(),scaling=sc,
                           seed=seed,arm=arm,step=step,training_prefix=n,anchor=True,boundary=False,
                           config_sha256=c.o.sha(c.CONFIG),parameters=sum(p.numel() for p in model.parameters()),
                           backbone=cfg['factors'][arm]['backbone'])
                torch.save(saved,dest/f'e{step}.pt')
                np.save(dest/f'e{step}_mean.npy',mu);np.save(dest/f'e{step}_learned_mm.npy',change)
                print(f'n={n} {arm} seed={seed} step={step}',flush=True)
    c.o.write_json(dest/'replay.json',replay)
    elapsed=time.monotonic()-started
    c.o.lock(dest,'complete.json',list(dest.glob('*')),status='complete',new_fits=1,actual_updates=400,replayed_updates=200,additional_updates=200,elapsed_seconds=elapsed)
    c.o.event(root,'extension_fit_completed',origin=n,arm=arm,seed=seed,updates=400,elapsed_seconds=elapsed)
    return mu


def train():
    cfg,root,out=extension_guard();c.o.setup(cfg);out.mkdir(exist_ok=False)
    bank=c.o.bank(cfg);previous=None
    for n in cfg['origins']:
        folder=out/f'origin_{n}';folder.mkdir(exist_ok=False)
        y=c.o.labels(cfg,n,'conditional_fit_current_prefix_and_calibration')
        sc=c.o.scaling(bank[c.o.teacher_id(n,True)],y,cfg);cache=c.o.target_bank(bank,y)
        c.o.write_json(folder/'scaling.json',sc)
        source=c.ROOT/cfg['source_experiment']/f'origin_{n}'
        means_old=c.o.load_npz(source/'means.npz');seeds_old=c.o.load_npz(source/'seeds.npz')
        means={m:means_old[m] for m in cfg['controls']};seeds={m:seeds_old[m] for m in cfg['controls']}
        for arm in cfg['arms']:
            means[arm+'_E200']=means_old[arm];seeds[arm+'_E200']=seeds_old[arm]
            predictions=np.stack([fit(cfg,root,out,bank,y,cache,n,arm,s,sc) for s in cfg['seeds']])
            seeds[arm+'_E400']=predictions;means[arm+'_E400']=predictions.mean(0)
        np.savez_compressed(folder/'means.npz',**means);np.savez_compressed(folder/'seeds.npz',**seeds)
        paths=[folder/name for name in ['scaling.json','means.npz','seeds.npz']]
        if previous is not None:
            c.o.verify_lock(out/f'origin_{previous}/issue_lock.json')
            prior=c.o.load_npz(out/f'origin_{previous}/means.npz')
            errors={m:y[previous+90:previous+180]-prior[m][90:180] for m in means}
            assert previous+180<=n
            sigmas={m:np.maximum(np.sqrt(np.mean(e**2,axis=0)),1e-6) for m,e in errors.items()}
            oldsig=c.o.load_npz(source/'sigmas.npz')
            for method in cfg['controls']:
                np.testing.assert_array_equal(sigmas[method],oldsig[method])
            for arm in cfg['arms']:
                np.testing.assert_array_equal(sigmas[arm+'_E200'],oldsig[arm])
            np.savez_compressed(folder/'sigmas.npz',**sigmas)
            np.savez_compressed(folder/'calibration_errors.npz',**errors)
            c.o.write_json(folder/'calibration.json',dict(previous_origin=previous,start=previous+90,end=previous+180,matured_before=n,count=90,gap=n-previous-180))
            paths.extend(folder/name for name in ['sigmas.npz','calibration_errors.npz','calibration.json'])
        c.o.lock(folder,'issue_lock.json',paths,status='issued' if previous is not None else 'bootstrap',origin=n,end=n+293)
        c.o.event(root,'extension_trajectory_issued',origin=n,end=n+293)
        previous=n
    complete=[c.o.read_json(p) for p in out.glob('origin_*/*/seed_*/complete.json')]
    assert len(complete)==24 and sum(v['actual_updates'] for v in complete)==9600
    c.o.lock(out,'training_complete.json',[out/f'origin_{n}/issue_lock.json' for n in cfg['origins']],status='complete',new_fits=24,actual_updates=9600,replayed_updates=4800,additional_updates=4800)


def score():
    cfg,root,out=extension_guard();c.o.verify_lock(out/'training_complete.json')
    for n in cfg['origins']:c.o.verify_lock(out/f'origin_{n}/issue_lock.json')
    y=c.o.labels(cfg,1461,'all_conditional_paths_locked_before_full_scoring')
    _,dates=c.o.read_forcing(c.ROOT/cfg['data'],1461)
    dest=out/'analysis';dest.mkdir(exist_ok=False)
    rows=[];points=[];sr=[];sp=[];daily=[];pairs=[]
    arms=[a+f'_E{s}' for a in cfg['arms'] for s in [200,400]]
    for n in cfg['origins'][1:]:
        folder=out/f'origin_{n}';means=c.o.load_npz(folder/'means.npz');seeds=c.o.load_npz(folder/'seeds.npz');sigmas=c.o.load_npz(folder/'sigmas.npz')
        metrics={};seedmetrics={}
        for method,mu in means.items():
            metrics[method]=c.o.scores(y[n:n+293],mu,sigmas[method]);summary=c.o.summarize(metrics[method])
            rows.append(dict(origin=n,method=method,**summary));points.extend(dict(origin=n,method=method,**v) for v in metrics[method])
            for seed,prediction in enumerate(seeds[method]):
                scores=c.o.scores(y[n:n+293],prediction,sigmas[method]);seedmetrics[method,seed]=c.o.summarize(scores)
                sr.append(dict(origin=n,method=method,seed=seed,**seedmetrics[method,seed]));sp.extend(dict(origin=n,method=method,seed=seed,**v) for v in scores)
            for d in range(293):
                for p,point in enumerate(cfg['points']):
                    daily.append(dict(origin=n,method=method,point=point,issue_date=dates[n-1],target_index=n+d,date=dates[n+d],distance=d+1,observed=y[n+d,p],mean=mu[d,p],sigma=sigmas[method][p]))
        edges=[(a,b) for a in arms for b in cfg['controls']]+[(a+'_E400',a+'_E200') for a in cfg['arms']]+[('T10_ANCHOR_UNIFORM_E400','G10_ANCHOR_UNIFORM_E400')]
        for a,b in edges:
            flags=[all(seedmetrics[a,s][k]<seedmetrics[b,s][k] for k in ['mae','rmse']) for s in cfg['seeds']]
            pairs.append(dict(origin=n,candidate=a,reference=b,seed_both_improve=sum(flags),seed_flags=flags,
                              **c.o.effect(metrics[a],metrics[b],cfg)))
    for name,data in [('phase_summary',rows),('metrics_by_point',points),('seed_summary',sr),('seed_metrics_by_point',sp),('daily_predictions',daily)]:
        pd.DataFrame(data).to_csv(dest/f'{name}.csv',index=False,float_format='%.17g')
    c.o.write_json(dest/'pairing.json',pairs)
    c.o.write_json(dest/'receipt.json',dict(status='complete',new_fits=24,actual_updates=9600,replayed_updates=4800,additional_updates=4800,summary_rows=len(rows),point_rows=len(points),seed_rows=len(sr),daily_rows=len(daily),pairs=len(pairs),checkpoint_selection=False))
    c.o.lock(out,'analysis_lock.json',list(dest.glob('*')),status='complete')
    c.o.event(root,'extension_scoring_completed',new_fits=24,actual_updates=9600)
    print(pd.DataFrame(rows)[['origin','method','mae','rmse','crps']].to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['train','score']);args=parser.parse_args()
    try: train() if args.command=='train' else score()
    except Exception:
        c.o.write_json(c.ROOT/c.spec()['out']/f'extension_{args.command}_error_{int(time.time())}.json',dict(error=traceback.format_exc(),time_utc=c.o.utc()))
        raise
