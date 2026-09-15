"""Read-only checkpoint/teacher diagnosis. No optimizer, fitting or physics calls."""
from pathlib import Path
import json
import sys
import time
import numpy as np
import pandas as pd
import torch
from torch.nn import functional as F

import overnight_graph.core as g
import transformer_origin.core as o
from overnight_graph.audit import independent_inputs as gi, numpy_forward as gn
from transformer_origin.audit import independent_inputs as oi, numpy_forward as on

OUT = Path(__file__).parent
ROOT = g.ROOT
ARMS = ['GRU_LOCAL', 'GRU_GRAPH', 'COND_ATTN']
ORIGINS = [612, 792, 972, 1168]
STEPS = [0, 50, 100, 200]
SEEDS = [0, 1, 2]
OLDER = {612: 432, 792: 612, 972: 612, 1168: 792}
POINTS = ['ATU1', 'ATU5', 'MJ3', 'MJ1']


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def summary(pred, target, units):
    err = (pred - target) * units
    mse, penalty, zero = np.mean((pred-target)**2), np.mean(pred**2), np.mean(target**2)
    return dict(data_mse_normalized=float(mse), penalty_normalized=float(penalty),
                objective=float(mse+penalty), zero_objective=float(zero),
                unconstrained_floor=float(.5*zero),
                objective_improvement_fraction=float((zero-mse-penalty)/(.5*zero)),
                avg_mae_mm=float(np.abs(err).mean(0).mean()),
                avg_rmse_mm=float(np.sqrt((err**2).mean(0)).mean()),
                bplus_avg_rmse_mm=float(np.sqrt(((target*units)**2).mean(0)).mean()))


def physical_scores(y, mu):
    e=mu-y
    return dict(mae=float(np.abs(e).mean()), rmse=float(np.sqrt((e*e).mean(0)).mean()))


def forward_gru_saved_samples(model, teachers, y, ms, hs, scale):
    """Cache causal prefix states; decode precisely the saved training queries."""
    ms, hs = ms.reshape(-1), hs.reshape(-1, hs.shape[-1])
    result=np.empty((*hs.shape, 4))
    avg, sd=np.array(scale['mean']),np.array(scale['std'])
    tids=np.array([g.teacher_id(int(m)) for m in ms])
    for tid in np.unique(tids):
        select=np.flatnonzero(tids==tid); mm=ms[select]; hh=hs[select]
        raw=(g.history_raw(teachers[int(tid)],y[:int(mm.max())])-avg)/sd
        hist=torch.from_numpy(raw.transpose(1,0,2))
        states,_=model.gru(hist)
        h=states[:,torch.from_numpy(mm-1)].permute(1,0,2)
        context=torch.tanh(model.message(torch.einsum('pq,bqk->bpk',model.adjacency,h)))
        future=(g.own_physics(teachers[int(tid)])[mm[:,None]+hh-1]-avg[:,:10])/sd[:,:10]
        last=torch.from_numpy(raw[mm-1][...,[11,12]])
        distances=np.stack([hh/293,np.log1p(hh)/np.log(294)],-1)
        b,q=hh.shape
        z=torch.cat([h[:,None].expand(-1,q,-1,-1),context[:,None].expand(-1,q,-1,-1),
                     torch.from_numpy(future),last[:,None].expand(-1,q,-1,-1),
                     torch.from_numpy(distances)[:,:,None].expand(-1,-1,4,-1),
                     model.point(torch.arange(4))[None,None].expand(b,q,-1,-1)],-1)
        result[select]=model.head(F.gelu(model.decode(z))).sum(-1).numpy()
    return result.reshape(*hs.shape,4)


def main():
    started=time.monotonic()
    cfg=g.spec(); ocfg=o.spec();g.setup(cfg)
    g.verify_implementation(cfg);o.verify_implementation(ocfg)
    teachers=g.bank(cfg)
    y=g.read_labels(ROOT/cfg['data'],1461)
    _,dates=g.read_forcing(ROOT/cfg['data'],1461)
    roots={'GRU_LOCAL':ROOT/cfg['out']/'base','GRU_GRAPH':ROOT/cfg['out']/'base',
           'COND_ATTN':ROOT/ocfg['out']}
    current=lambda n:1168 if n==1168 else g.teacher_id(n)
    coverage={n:min(n+293,len(teachers[OLDER[n]]['mean']))-n for n in ORIGINS}
    dump('diagnostic_contract.json',dict(started_utc=g.utc(),arms=ARMS,origins=ORIGINS,
         checkpoints=STEPS,seeds=SEEDS,teacher_overlap_days=coverage,
         training_probe='All 200 saved batches, 4 origins and 16 distances per batch, retaining repetitions and exact sampling weights; fixed within each seed/checkpoint sequence.',
         probability='Not recalibrated; mean failure diagnosis only.',
         comparisons='Full 293-day existing forecast for training-vs-time transfer; teacher sensitivity only existing cache overlap and fixed e200; cross teacher baseline/input combinations are arithmetic diagnostics, not new valid candidates.',
         limits='Exposed/overlapping dates; no retraining; total penalized objective has per-sample unconstrained lower bound .5 E[r^2] for lambda=1, not a feasible forecast baseline.',
         optimizer_updates=0,physical_forwards=0,source_guards='301 graph sources plus 253 origin sources and implementation locks'))
    trainrows=[];teacherrows=[];trainpoint=[];outerrows=[];outerpoint=[];counterrows=[];counterpoint=[];shiftrows=[];supportrows=[]
    checks=0;maxdiff=0.;counter={};actual={};paths={}
    def close(a,b,tol=1e-8):
        nonlocal checks,maxdiff
        a,b=np.asarray(a),np.asarray(b)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all()
        delta=float(np.max(np.abs(a-b))) if a.size else 0.
        assert delta<tol,(a.shape,delta)
        checks+=a.size;maxdiff=max(maxdiff,delta)
    with torch.inference_mode():
        for n in ORIGINS:
            for seed in SEEDS:
                schedule=g.load_npz(roots['GRU_LOCAL']/f'origin_{n}/GRU_LOCAL/seed_{seed}/schedule.npz')
                ms,hs=schedule['origins'],schedule['horizons']
                flatm=ms.reshape(-1);flath=hs.reshape(-1,16);tids=np.array([g.teacher_id(int(m)) for m in flatm])
                ix=flatm[:,None]+flath-1
                assert ix.max()<n and all(t<=m for t,m in zip(tids,flatm))
                baseline=np.empty((*flath.shape,4))
                for tid in np.unique(tids):baseline[tids==tid]=teachers[int(tid)]['mean'][ix[tids==tid]]
                residual=y[ix]-baseline
                new_residual=y[ix]-teachers[current(n)]['mean'][ix]
                for p,pname in enumerate(POINTS):
                    shiftrows.append(dict(origin=n,seed=seed,point=pname,current_teacher=current(n),
                        actual_training_target_mean_mm=float(residual[:,:,p].mean()),
                        current_teacher_same_dates_target_mean_mm=float(new_residual[:,:,p].mean()),
                        target_shift_mm=float((new_residual-residual)[:,:,p].mean()),
                        actual_training_target_std_mm=float(residual[:,:,p].std()),
                        actual_training_target_pos_fraction=float((residual[:,:,p]>1e-6).mean()),
                        current_teacher_pos_fraction=float((new_residual[:,:,p]>1e-6).mean())))
                for arm in ARMS:
                    root=roots[arm];dest=root/f'origin_{n}/{arm}/seed_{seed}'
                    same=g.load_npz(dest/'schedule.npz')
                    close(same['origins'],ms,1e-12);close(same['horizons'],hs,1e-12)
                    print(f'origin={n} seed={seed} arm={arm}',flush=True)
                    for step in STEPS:
                        cp=dest/f'e{step}.pt'; paths[str(cp.relative_to(ROOT))]=g.sha(cp)
                        module,conf=(g,cfg) if arm.startswith('GRU') else (o,ocfg)
                        model,scale,saved=module.reload(cp,conf);model.eval()
                        unit=np.array(scale['unit']);target=residual/unit
                        if arm.startswith('GRU'):
                            pred=forward_gru_saved_samples(model,teachers,y[:n],ms,hs,scale)
                            for k in [0,99,199]:
                                ordinary=model(*g.inputs(teachers,y[:n],ms[k],hs[k],scale)).sum(-1).numpy()
                                close(pred[k*4:(k+1)*4],ordinary)
                        else:
                            preds=[]
                            for k in range(200):
                                preds.append(model(*o.inputs(teachers,y[:n],ms[k],hs[k],scale)).numpy())
                            pred=np.concatenate(preds)
                        # Independent NumPy full model on first training episode of each checkpoint.
                        inorm=(gi if arm.startswith('GRU') else oi)(teachers,y[:n],[int(flatm[0])],flath[:1],scale)
                        independent=(gn(saved,inorm,baseline[0])[0] if arm.startswith('GRU') else on(saved,inorm,baseline[0]))
                        close(pred[0]*unit+baseline[0],independent)
                        pr,ta=pred.reshape(-1,4),target.reshape(-1,4)
                        identity=(pr-ta)**2+pr**2-.5*ta**2
                        close(identity,2*(pr-.5*ta)**2)
                        metrics=summary(pr,ta,unit)
                        assert metrics['objective']>=metrics['unconstrained_floor']-1e-12
                        trainrows.append(dict(origin=n,seed=seed,arm=arm,step=step,**metrics))
                        for tid in np.unique(tids):
                            teacherrows.append(dict(origin=n,seed=seed,arm=arm,step=step,teacher=int(tid),
                                query_count=int((tids==tid).sum()*16),
                                **summary(pred[tids==tid].reshape(-1,4),target[tids==tid].reshape(-1,4),unit)))
                        for p,pname in enumerate(POINTS):
                            rr=ta[:,p]*unit[p];cc=pr[:,p]*unit[p];err=cc-rr
                            trainpoint.append(dict(origin=n,seed=seed,arm=arm,step=step,point=pname,
                                residual_mean_mm=float(rr.mean()),correction_mean_mm=float(cc.mean()),
                                rmse_mm=float(np.sqrt(np.mean(err**2))),bplus_rmse_mm=float(np.sqrt(np.mean(rr**2))),
                                mse_normalized=float(((pr[:,p]-ta[:,p])**2).mean()),penalty_normalized=float((pr[:,p]**2).mean())))
                        # Existing full future forecasts; never choose a checkpoint from these scores.
                        mean=np.load(dest/f'e{step}_mean.npy');b=teachers[current(n)]['mean'][n:n+293]
                        rr=y[n:n+293]-b;cc=mean-b
                        actual[(n,arm,seed,step)]=mean
                        outerrows.append(dict(origin=n,seed=seed,arm=arm,step=step,
                            **physical_scores(y[n:n+293],mean),bplus_rmse=physical_scores(y[n:n+293],b)['rmse']))
                        for p,pname in enumerate(POINTS):
                            r0=float(y[n-1,p]-teachers[current(n)]['mean'][n-1,p]);err=cc[:,p]-rr[:,p]
                            cross=float(np.mean(rr[:,p]*cc[:,p]));energy=float(np.mean(cc[:,p]**2))
                            delta=float(np.mean(err**2)-np.mean(rr[:,p]**2));close(np.array([delta]),np.array([energy-2*cross]))
                            mask=(abs(rr[:,p])>1e-6)&(abs(cc[:,p])>1e-6)
                            outerpoint.append(dict(origin=n,seed=seed,arm=arm,step=step,point=pname,
                                initial_residual_mm=r0,true_residual_mean_mm=float(rr[:,p].mean()),
                                residual_change_mean_mm=float(rr[:,p].mean()-r0),correction_mean_mm=float(cc[:,p].mean()),
                                correction_first_mm=float(cc[0,p]),correction_last_mm=float(cc[-1,p]),
                                forecast_bias_mm=float(err.mean()),forecast_rmse_mm=float(np.sqrt(np.mean(err**2))),
                                correction_energy_mm2=energy,residual_alignment_mm2=cross,delta_mse_mm2=delta,
                                opposite_sign_fraction=float((rr[mask,p]*cc[mask,p]<0).mean()) if mask.any() else None))
                        if step==200:
                            h=coverage[n];cf={}
                            for tid in [current(n),OLDER[n]]:
                                alias=dict(teachers);alias[current(n)]=teachers[tid]
                                tens=module.inputs(alias,y[:n],[n],np.arange(1,h+1)[None],scale,current=True)
                                corr=model(*tens).numpy()[0]
                                if arm.startswith('GRU'):corr=corr.sum(-1)
                                corr=corr*unit
                                cf[tid]=corr
                            close(cf[current(n)]+b[:h],mean[:h])
                            counter[(n,arm,seed)]=cf
                        del model
    for n in ORIGINS:
        h=coverage[n];new,old=current(n),OLDER[n]
        for arm in ARMS:
            for seed in SEEDS+['ensemble']:
                cs=counter[(n,arm,seed)] if seed!='ensemble' else {tid:np.mean([counter[(n,arm,s)][tid] for s in SEEDS],0) for tid in [new,old]}
                for baseline_tid in [new,old]:
                    b=teachers[baseline_tid]['mean'][n:n+h]
                    for input_tid in [new,old]:
                        c=cs[input_tid];mu=b+c
                        counterrows.append(dict(origin=n,arm=arm,seed=seed,horizon=h,baseline_teacher=baseline_tid,input_teacher=input_tid,
                            coherent=bool(input_tid==baseline_tid),**physical_scores(y[n:n+h],mu),
                            bplus_rmse=physical_scores(y[n:n+h],b)['rmse'],correction_rms=float(np.sqrt(np.mean(c*c)))))
                        for p,pname in enumerate(POINTS):
                            r=y[n:n+h,p]-b[:,p];delta_b=teachers[new]['mean'][n:n+h,p]-teachers[old]['mean'][n:n+h,p]
                            counterpoint.append(dict(origin=n,arm=arm,seed=seed,horizon=h,point=pname,
                                baseline_teacher=baseline_tid,input_teacher=input_tid,coherent=bool(input_tid==baseline_tid),
                                bplus_mean_residual_mm=float(r.mean()),bplus_rmse_mm=float(np.sqrt(np.mean(r*r))),
                                correction_mean_mm=float(c[:,p].mean()),forecast_rmse_mm=float(np.sqrt(np.mean((c[:,p]-r)**2))),
                                new_minus_old_bplus_mean_mm=float(delta_b.mean()),
                                required_correction_change_mm=float(-delta_b.mean()),
                                actual_correction_change_mm=float((cs[new][:,p]-cs[old][:,p]).mean())))
                np.savez_compressed(OUT/f'counter_{n}_{arm}_{seed}.npz',**{f'correction_teacher_{tid}':c for tid,c in cs.items()})
            # Ensemble primary forecast, scored separately from average seed errors.
            for step in STEPS:
                mean=np.mean([actual[(n,arm,s,step)] for s in SEEDS],0)
                b=teachers[current(n)]['mean'][n:n+293]
                rr=y[n:n+293]-b;cc=mean-b
                outerrows.append(dict(origin=n,seed='ensemble',arm=arm,step=step,
                    **physical_scores(y[n:n+293],mean),bplus_rmse=physical_scores(y[n:n+293],b)['rmse']))
                for p,pname in enumerate(POINTS):
                    e=cc[:,p]-rr[:,p];r0=float(y[n-1,p]-teachers[current(n)]['mean'][n-1,p]);mask=(abs(rr[:,p])>1e-6)&(abs(cc[:,p])>1e-6)
                    outerpoint.append(dict(origin=n,seed='ensemble',arm=arm,step=step,point=pname,
                        initial_residual_mm=r0,true_residual_mean_mm=float(rr[:,p].mean()),residual_change_mean_mm=float(rr[:,p].mean()-r0),
                        correction_mean_mm=float(cc[:,p].mean()),correction_first_mm=float(cc[0,p]),correction_last_mm=float(cc[-1,p]),
                        forecast_bias_mm=float(e.mean()),forecast_rmse_mm=float(np.sqrt(np.mean(e*e))),
                        correction_energy_mm2=float(np.mean(cc[:,p]**2)),residual_alignment_mm2=float(np.mean(cc[:,p]*rr[:,p])),
                        delta_mse_mm2=float(np.mean(e*e)-np.mean(rr[:,p]**2)),
                        opposite_sign_fraction=float((cc[mask,p]*rr[mask,p]<0).mean()) if mask.any() else None))
    # Last residual/velocity states at issuance compared with all allowed training-origin states.
    for n in ORIGINS:
        m=np.arange(432,n);target_teacher=current(n)
        for p,pname in enumerate(POINTS):
            for label in ['residual','residual_change','velocity']:
                values=[]
                for origin in m:
                    t=teachers[g.teacher_id(int(origin))]['mean']; k=origin-1
                    r=y[k,p]-t[k,p]
                    val=r if label=='residual' else (r-(y[k-1,p]-t[k-1,p]) if label=='residual_change' else y[k,p]-y[k-1,p])
                    values.append(val)
                k=n-1;t=teachers[target_teacher]['mean'];r=y[k,p]-t[k,p]
                value=r if label=='residual' else (r-(y[k-1,p]-t[k-1,p]) if label=='residual_change' else y[k,p]-y[k-1,p])
                supportrows.append(dict(origin=n,point=pname,feature=label,actual_value=float(value),
                    training_min=float(np.min(values)),training_p05=float(np.quantile(values,.05)),
                    training_median=float(np.median(values)),training_p95=float(np.quantile(values,.95)),
                    training_max=float(np.max(values)),outside_training_range=bool(value<min(values) or value>max(values))))
    dfs={'training_summary':trainrows,'training_by_teacher':teacherrows,'training_by_point':trainpoint,
         'outer_summary':outerrows,'outer_by_point':outerpoint,'teacher_counterfactual_summary':counterrows,
         'teacher_counterfactual_by_point':counterpoint,'same_date_target_shift':shiftrows,'origin_state_support':supportrows}
    for name,rows in dfs.items():pd.DataFrame(rows).to_csv(OUT/f'{name}.csv',index=False,float_format='%.17g')
    for path,digest in paths.items():assert g.sha(ROOT/path)==digest
    g.verify_implementation(cfg);o.verify_implementation(ocfg)
    dump('receipt.json',dict(status='passed',finished_utc=g.utc(),elapsed_seconds=time.monotonic()-started,
        model_checkpoints=len(paths),checked_values=checks,max_difference=maxdiff,training_updates=0,new_physics=0,
        source_hashes=paths,rows={k:len(v) for k,v in dfs.items()},future_labels='Only retrospective scoring, never model inputs',
        probability_changes=0,source_code_changes=0,original_experiment_changes=0))
    print('COMPLETE',json.dumps({k:len(v) for k,v in dfs.items()}),flush=True)


if __name__=='__main__':main()
