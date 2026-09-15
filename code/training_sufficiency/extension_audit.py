"""Independent saved-result audit and fixed-panel e400 measurement, without fitting."""
import json
import traceback
import numpy as np
import pandas as pd
import torch
from overnight_graph.audit import independent_inputs
from backbone_anchor.numpy_model import numpy_forward
from transformer_temporal.audit import independent_scores, independent_gate
from . import core as c
from .extend import extension_guard


def main():
    cfg,root,ext=extension_guard();c.o.setup(cfg)
    c.o.verify_lock(ext/'training_complete.json');c.o.verify_lock(ext/'analysis_lock.json')
    out=ext/'audit';out.mkdir(exist_ok=False);checks=[];checkpoints=0

    def close(name,a,b,tol=1e-8):
        a,b=np.asarray(a,float),np.asarray(b,float)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
        difference=float(np.max(abs(a-b))) if a.size else 0.
        assert difference<=tol,(name,difference,tol)
        checks.append(dict(check=name,values=int(a.size),max_difference=difference,tolerance=tol))

    y=c.o.labels(cfg,1461,'independent_extension_audit_after_all_paths_and_scores_locked');bank=c.o.bank(cfg)
    _,dates=c.o.read_forcing(c.ROOT/cfg['data'],1461)
    tables={name:pd.read_csv(ext/'analysis'/f'{name}.csv',float_precision='round_trip').set_index(keys)
            for name,keys in [('phase_summary',['origin','method']),('metrics_by_point',['origin','method','point']),
                              ('seed_summary',['origin','method','seed']),('seed_metrics_by_point',['origin','method','seed','point'])]}
    daily=pd.read_csv(ext/'analysis/daily_predictions.csv',float_precision='round_trip')
    allmeans={};allseeds={};pointmetrics={};seedmetrics={};panelrows=[];panelpoints=[]
    fits=0;actual_updates=0
    for n in cfg['origins']:
        c.o.check_deadline(cfg);folder=ext/f'origin_{n}';c.o.verify_lock(folder/'issue_lock.json')
        sc=c.o.read_json(folder/'scaling.json')
        assert sc==c.o.read_json(c.ROOT/cfg['source_experiment']/f'origin_{n}/scaling.json')
        means=c.o.load_npz(folder/'means.npz');seeds=c.o.load_npz(folder/'seeds.npz')
        allmeans[n]=means;allseeds[n]=seeds
        original=c.o.load_npz(c.ROOT/cfg['source_experiment']/f'origin_{n}/means.npz')
        origseeds=c.o.load_npz(c.ROOT/cfg['source_experiment']/f'origin_{n}/seeds.npz')
        for method in cfg['controls']:
            close(f'{n}/{method}/control_mean',means[method],original[method],0)
            close(f'{n}/{method}/control_seeds',seeds[method],origseeds[method],0)
        pdata=c.o.load_npz(root/'diagnostic'/f'panel_{n}.npz');ms,hs,t,unit=[pdata[k] for k in ['origins','horizons','target','unit']]
        panel_arrays={};batches=[(i,c.base.tensors(bank,y[:n],ms[i:i+8],hs[i:i+8],sc,True)) for i in range(0,128,8)]
        for arm in cfg['arms']:
            close(f'{n}/{arm}/e200_mean_preserved',means[arm+'_E200'],original[arm],0)
            close(f'{n}/{arm}/e200_seeds_preserved',seeds[arm+'_E200'],origseeds[arm],0)
            for seed in cfg['seeds']:
                dest=folder/arm/f'seed_{seed}';done=c.o.verify_lock(dest/'complete.json')
                assert done['actual_updates']==400 and done['replayed_updates']==200 and done['additional_updates']==200
                fits+=1;actual_updates+=done['actual_updates']
                draws=c.o.load_npz(dest/'schedule.npz');rng=np.random.default_rng(seed);mm=[];hh=[]
                for _ in range(2):
                    origins=rng.integers(432,n,(200,4));distances=np.array([[rng.integers(1,min(293,n-int(m))+1,16) for m in row] for row in origins])
                    mm.extend(origins);hh.extend(distances)
                close(f'{n}/{arm}/{seed}/schedule_m',draws['origins'],mm,0)
                close(f'{n}/{arm}/{seed}/schedule_h',draws['horizons'],hh,0)
                log=[json.loads(s) for s in (dest/'training.jsonl').read_text().splitlines()]
                oldlog=[json.loads(s) for s in c.checkpoint(n,arm,seed,200).with_name('training.jsonl').read_text().splitlines()]
                assert len(log)==400 and log[:200]==oldlog
                close(f'{n}/{arm}/{seed}/loss',[v['loss'] for v in log],[v['mse']+v['penalty'] for v in log],0)
                assert all(np.isfinite(v['grad_norm']) for v in log)
                for step in [0,200,400]:
                    path=dest/f'e{step}.pt';model,scale,saved=c.base.reload(path)
                    assert scale==sc and saved['training_prefix']==n and saved['step']==step and saved['anchor'] and not saved['boundary']
                    assert saved['config_sha256']==c.o.sha(c.CONFIG)
                    assert all(float(v['step'])==step and torch.isfinite(v['exp_avg']).all() and torch.isfinite(v['exp_avg_sq']).all()
                               for v in saved['optimizer_state_dict']['state'].values())
                    if step==0: assert saved['optimizer_state_dict']['state']=={}
                    if step in [0,200]:
                        old=torch.load(c.checkpoint(n,arm,seed,step),map_location='cpu',weights_only=True)
                        for key,value in saved['state_dict'].items():close(f'{n}/{arm}/{seed}/{step}/old_parameter/{key}',value.numpy(),old['state_dict'][key].numpy(),0)
                    mu,change,_=c.base.predict(model,bank,y[:n],n,n+293,sc,True)
                    close(f'{n}/{arm}/{seed}/{step}/reload',mu,np.load(dest/f'e{step}_mean.npy'),0)
                    close(f'{n}/{arm}/{seed}/{step}/reload_change',change,np.load(dest/f'e{step}_learned_mm.npy'),0)
                    inputs=independent_inputs(bank,y[:n],[n],np.arange(294)[None],sc,True)
                    output,_=numpy_forward(saved,inputs,np.zeros((294,4)))
                    teacher=bank[1168 if n==1168 else 792 if n>=792 else 612]['mean']
                    reference=teacher[n:n+293]+y[n-1]-teacher[n-1]+output[1:]-output[:1]
                    close(f'{n}/{arm}/{seed}/{step}/numpy',mu,reference,1e-7)
                    if step==400:
                        close(f'{n}/{arm}/{seed}/issued_seed',seeds[arm+'_E400'][seed],mu,0)
                        poison=y.copy();poison[n:]=-1e15
                        close(f'{n}/{arm}/{seed}/future_y_invariance',mu,c.base.predict(model,bank,poison,n,n+293,sc,True)[0],0)
                        initial={k:v.detach().clone() for k,v in model.state_dict().items()}
                        model.eval();model.zero_grad(set_to_none=True);qs=[]
                        for i,values in batches:
                            q=c.base.learned(model,values,True)
                            loss=((q-torch.from_numpy(t[i:i+8])).square().mean()+q.square().mean())/16
                            loss.backward();qs.append(q.detach().numpy())
                        q=np.concatenate(qs);panel_arrays[f'{arm}_s{seed}_e400']=q
                        gradient=float(sum(p.grad.square().sum() for p in model.parameters() if p.grad is not None).sqrt())
                        assert np.isfinite(gradient)
                        for key,value in model.state_dict().items():assert torch.equal(value,initial[key])
                        err=q-t;mse=np.mean(err**2,axis=(0,1));penalty=np.mean(q*q,axis=(0,1));lower=.5*np.mean(t*t,axis=(0,1))
                        met=dict(mse=mse,penalty=penalty,objective=mse+penalty,free_output_lower_bound=lower,
                                 excess_objective=2*np.mean((q-.5*t)**2,axis=(0,1)),mae_mm=np.mean(abs(err),axis=(0,1))*unit,rmse_mm=np.sqrt(mse)*unit)
                        close(f'{n}/{arm}/{seed}/objective_identity',met['objective'],met['free_output_lower_bound']+met['excess_objective'],1e-12)
                        identity=dict(origin=n,arm=arm,seed=seed,step=400)
                        panelrows.append(dict(identity,gradient_norm=gradient,**{k:float(v.mean()) for k,v in met.items()}))
                        for p,point in enumerate(cfg['points']):panelpoints.append(dict(identity,point=point,**{k:float(v[p]) for k,v in met.items()}))
                        for slot in [0,42,85,127]:
                            values=independent_inputs(bank,y[:n],[ms[slot]],np.r_[0,hs[slot]][None],sc)
                            v,_=numpy_forward(saved,values,np.zeros((33,4)))
                            close(f'{n}/{arm}/{seed}/e400_numpy_panel{slot}',q[slot],(v[1:]-v[:1])/unit,1e-8)
                    checkpoints+=1
            close(f'{n}/{arm}/ensemble',means[arm+'_E400'],seeds[arm+'_E400'].mean(0),0)
        np.savez_compressed(out/f'panel400_{n}.npz',**panel_arrays)
        if n!=612:
            previous=cfg['origins'][cfg['origins'].index(n)-1]
            sigmas=c.o.load_npz(folder/'sigmas.npz');errors=c.o.load_npz(folder/'calibration_errors.npz')
            assert previous+180<=n
            for method,mu in means.items():
                es=y[previous+90:previous+180]-allmeans[previous][method][90:180]
                close(f'{n}/{method}/calibration_errors',errors[method],es,0)
                close(f'{n}/{method}/sigma',sigmas[method],np.maximum(np.sqrt(np.mean(es**2,axis=0)),1e-6),0)
                met=independent_scores(y[n:n+293],mu,sigmas[method]);pointmetrics[n,method]=met
                for key,value in met.items():
                    close(f'{n}/{method}/{key}/phase',tables['phase_summary'].loc[(n,method),key],value.mean())
                    close(f'{n}/{method}/{key}/points',[tables['metrics_by_point'].loc[(n,method,p),key] for p in cfg['points']],value)
                for seed,sm in enumerate(seeds[method]):
                    met=independent_scores(y[n:n+293],sm,sigmas[method]);seedmetrics[n,method,seed]=met
                    for key,value in met.items():
                        close(f'{n}/{method}/{seed}/{key}/phase',tables['seed_summary'].loc[(n,method,seed),key],value.mean())
                        close(f'{n}/{method}/{seed}/{key}/points',[tables['seed_metrics_by_point'].loc[(n,method,seed,p),key] for p in cfg['points']],value)
                for p,point in enumerate(cfg['points']):
                    rows=daily[(daily.origin==n)&(daily.method==method)&(daily.point==point)].sort_values('distance')
                    close(f'{n}/{method}/{point}/daily_y',rows.observed,y[n:n+293,p],0)
                    close(f'{n}/{method}/{point}/daily_prediction',rows['mean'],mu[:,p],0)
                    close(f'{n}/{method}/{point}/daily_sigma',rows.sigma,np.repeat(sigmas[method][p],293),0)
                    assert list(rows.date)==list(dates[n:n+293]) and len(rows)==293
        print(f'n={n}: optimizer replay, all weights, panel400 and complete scores verified',flush=True)
    for row in c.o.read_json(ext/'analysis/pairing.json'):
        n,a,b=row['origin'],row['candidate'],row['reference']
        expected=independent_gate(pointmetrics[n,a],pointmetrics[n,b],cfg)
        assert all(row[k]==v for k,v in expected.items())
        count=sum(all(seedmetrics[n,a,s][k].mean()<seedmetrics[n,b,s][k].mean() for k in ['mae','rmse']) for s in cfg['seeds'])
        assert count==row['seed_both_improve']
    events=[json.loads(s) for s in (root/'events.jsonl').read_text().splitlines()]
    first_final=min(i for i,v in enumerate(events) if v['event']=='label_prefix_read' and v['rows']==1461)
    issued={v['origin'] for v in events[:first_final] if v['event']=='extension_trajectory_issued'}
    assert issued==set(cfg['origins']) and fits==24 and actual_updates==9600 and checkpoints==72
    pd.DataFrame(panelrows).to_csv(out/'training400_summary.csv',index=False,float_format='%.17g')
    pd.DataFrame(panelpoints).to_csv(out/'training400_points.csv',index=False,float_format='%.17g')
    receipt=dict(status='passed',checkpoints=checkpoints,verified_fits=fits,verified_actual_updates=actual_updates,
                 new_fits_in_audit=0,optimizer_updates_in_audit=0,checks=len(checks),values=sum(v['values'] for v in checks),
                 max_difference=max(v['max_difference'] for v in checks),all_original200_traces_and_parameters_exact=True,
                 final_labels_after_all_issued=True,independent_numpy_panel_slots=[0,42,85,127],gradient_norm='finite and parameters unchanged; not independently differenced')
    c.o.write_json(out/'checks.json',checks);c.o.write_json(out/'receipt.json',receipt)
    c.o.lock(ext,'audit_lock.json',list(out.glob('*')),status='passed')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:
        c.o.write_json(c.ROOT/c.spec()['out']/'extension_audit_error.json',dict(error=traceback.format_exc(),time_utc=c.o.utc()))
        raise
