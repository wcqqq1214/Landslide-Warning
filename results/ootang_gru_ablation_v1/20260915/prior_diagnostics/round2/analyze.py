"""Frozen-checkpoint failure diagnosis; all writes stay in this temporary folder."""
from pathlib import Path
import hashlib
import json
import time
import numpy as np
import pandas as pd
import torch
import overnight_graph.core as g
import transformer_origin.core as o

OUT = Path(__file__).parent
PRIOR = Path('/tmp/ootang-failure-diagnosis-4UIBFI')
ARMS = ['GRU_LOCAL', 'GRU_GRAPH', 'COND_ATTN']
ORIGINS = [612, 792, 972, 1168]
OLDER = {612: 432, 792: 612, 972: 612, 1168: 792}
POINTS = ['ATU1', 'ATU5', 'MJ3', 'MJ1']
VARIANTS = ['BPLUS', 'ANCHOR_ONLY', 'SHAPE_ONLY', 'ANCHORED_SHAPE', 'ORIGINAL']


def dump(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def age_bin(age):
    return np.select([age == 0, age < 30, age < 90], ['0', '1-29', '30-89'], default='90+')


def metrics(mu, y):
    err = mu-y
    return dict(mae=float(abs(err).mean()), rmse=float(np.sqrt(np.mean(err**2, axis=0)).mean()))


def main():
    start=time.monotonic(); cfg=g.spec(); ocfg=o.spec();g.setup(cfg)
    g.verify_implementation(cfg);o.verify_implementation(ocfg)
    bank=g.bank(cfg); y=g.read_labels(g.ROOT/cfg['data'],1461)
    _,dates=g.read_forcing(g.ROOT/cfg['data'],1461)
    roots={a:g.ROOT/(cfg['out']+'/base' if a.startswith('GRU') else ocfg['out']) for a in ARMS}
    current=lambda n:1168 if n==1168 else g.teacher_id(n)
    rows=[];prows=[];components=[];support=[];support_total=[];factorrows=[];factorpoint=[];factor_effect=[]
    checkpoints={}; checks=[]; arrays={};factors={}
    def close(a,b,label,tol=1e-8):
        a,b=np.asarray(a),np.asarray(b)
        assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),label
        d=float(np.max(abs(a-b))) if a.size else 0.
        assert d<tol,(label,d)
        checks.append(dict(label=label,values=int(a.size),difference=d))
    for n in ORIGINS:
        new=current(n);old=OLDER[n]; b=bank[new]['mean'][n:n+293]
        r0=y[n-1]-bank[new]['mean'][n-1]
        horizon=min(n+293,len(bank[old]['mean']))-n
        for seed in [0,1,2]:
            schedule=g.load_npz(roots['GRU_LOCAL']/f'origin_{n}/GRU_LOCAL/seed_{seed}/schedule.npz')
            ms=schedule['origins'].reshape(-1); hs=schedule['horizons'].reshape(-1,16)
            tids=np.array([g.teacher_id(int(m)) for m in ms]);age=ms-tids; group=age_bin(age)
            ix=ms[:,None]+hs-1
            assert ix.max()<n and np.all(tids<=ms)
            r=np.empty((*hs.shape,4)); r_origin=np.empty((len(ms),4))
            for tid in np.unique(tids):
                sel=tids==tid
                r[sel]=y[ix[sel]]-bank[int(tid)]['mean'][ix[sel]]
                r_origin[sel]=y[ms[sel]-1]-bank[int(tid)]['mean'][ms[sel]-1]
            allr0=np.broadcast_to(r_origin[:,None,:],r.shape)
            scale=g.reload(roots['GRU_LOCAL']/f'origin_{n}/GRU_LOCAL/seed_{seed}/e200.pt',cfg)[1]
            unit=np.asarray(scale['unit']);rr=r/unit;aa=allr0/unit
            l0=float(np.mean(rr**2)); lhalf=float(np.mean((.5*aa-rr)**2+(.5*aa)**2))
            support_total.append(dict(origin=n,seed=seed,origin_draws=len(ms),query_draws=hs.size,
                fresh_origin_draws=int((age==0).sum()),fresh_unique_origins=int(np.unique(ms[age==0]).size),
                fresh_query_fraction=float((age==0).mean()),zero_objective=l0,
                half_origin_objective=lhalf,unconstrained_floor=.5*l0,
                half_origin_gap_reduction=(l0-lhalf)/(.5*l0),
                current_teacher=new,current_teacher_age=n-new,
                maximum_training_teacher_age=int(age.max())))
            for label in ['all','0','1-29','30-89','90+']:
                mask=np.ones(len(ms),bool) if label=='all' else group==label
                if not mask.any():continue
                for p,point in enumerate(POINTS):
                    rr=r[mask,:,p];a=allr0[mask,:,p];change=rr-a
                    init_energy=float(np.mean(a*a)); change_energy=float(np.mean(change*change)); cross=float(2*np.mean(a*change))
                    close([np.mean(rr*rr)],[init_energy+change_energy+cross],'training_target_energy')
                    support.append(dict(origin=n,seed=seed,age_bin=label,point=point,
                        origin_draws=int(mask.sum()),unique_origins=int(np.unique(ms[mask]).size),query_draws=int(rr.size),
                        horizon_mean=float(hs[mask].mean()),horizon_min=int(hs[mask].min()),horizon_max=int(hs[mask].max()),
                        initial_residual_mean_mm=float(a.mean()),future_residual_mean_mm=float(rr.mean()),
                        change_mean_mm=float(change.mean()),future_negative_fraction=float((rr< -1e-6).mean()),
                        initial_energy_mm2=init_energy,change_energy_mm2=change_energy,cross_energy_mm2=cross,
                        residual_energy_mm2=float(np.mean(rr*rr))))
            for arm in ARMS:
                print(f'origin={n} seed={seed} arm={arm}',flush=True)
                root=roots[arm]; path=root/f'origin_{n}/{arm}/seed_{seed}/e200.pt'
                checkpoints[str(path.relative_to(g.ROOT))]=g.sha(path)
                mu=np.load(path.parent/'e200_mean.npy');arrays[n,arm,seed]=mu
                module,config=(g,cfg) if arm.startswith('GRU') else (o,ocfg)
                model,sc,_=module.reload(path,config);model.eval()
                input_versions=[]
                for tid in [old,new]:
                    alias=dict(bank);alias[new]=bank[tid]
                    input_versions.append(module.inputs(alias,y[:n],[n],np.arange(1,horizon+1)[None],sc,current=True))
                obs=slice(10,12) if arm.startswith('GRU') else slice(22,26)
                close(input_versions[0][0][...,obs],input_versions[1][0][...,obs],'same_observed_channels')
                effects=np.empty((2,2,horizon,4))
                with torch.inference_mode():
                    for physics in [0,1]:
                        for residual in [0,1]:
                            tensors=[v.clone() for v in input_versions[physics]]
                            r_slice=slice(12,14) if arm.startswith('GRU') else slice(26,30)
                            tensors[0][...,r_slice]=input_versions[residual][0][...,r_slice]
                            corr=model(*tensors).numpy()[0]
                            if arm.startswith('GRU'):corr=corr.sum(-1)
                            effects[physics,residual]=corr*np.asarray(sc['unit'])
                oldcounter=np.load(PRIOR/f'counter_{n}_{arm}_{seed}.npz')
                close(effects[0,0],oldcounter[f'correction_teacher_{old}'],'prior_old_teacher')
                close(effects[1,1],oldcounter[f'correction_teacher_{new}'],'prior_new_teacher')
                close(effects[1,1]+b[:horizon],mu[:horizon],'saved_issued_forecast')
                factors[n,arm,seed]=effects
                del model
        for arm in ARMS:
            for seed in [0,1,2,'ensemble']:
                mu=arrays[n,arm,seed] if seed!='ensemble' else np.mean([arrays[n,arm,s] for s in [0,1,2]],axis=0)
                corr=mu-b;shape=corr-corr[0];truth=y[n:n+293];rr=truth-b
                paths=dict(BPLUS=b,ANCHOR_ONLY=b+r0,SHAPE_ONLY=b+shape,ANCHORED_SHAPE=b+r0+shape,ORIGINAL=mu)
                for variant,pred in paths.items():
                    scores=metrics(pred,truth)
                    rows.append(dict(origin=n,teacher=new,teacher_age=n-new,arm=arm,seed=seed,variant=variant,
                                     horizon=293,**scores))
                    for p,point in enumerate(POINTS):
                        err=pred[:,p]-truth[:,p]
                        prows.append(dict(origin=n,arm=arm,seed=seed,variant=variant,point=point,
                                          mae=float(abs(err).mean()),rmse=float(np.sqrt(np.mean(err*err))),bias=float(err.mean())))
                bias=corr[0]-r0; shape_error=shape-(rr-r0)
                for p,point in enumerate(POINTS):
                    mse=float(np.mean((mu[:,p]-truth[:,p])**2));a2=float(bias[p]**2)
                    s2=float(np.mean(shape_error[:,p]**2));cross=float(2*bias[p]*shape_error[:,p].mean())
                    close([mse],[a2+s2+cross],'forecast_error_decomposition')
                    components.append(dict(origin=n,arm=arm,seed=seed,point=point,
                        initial_residual_mm=float(r0[p]),first_predicted_correction_mm=float(corr[0,p]),
                        first_actual_residual_mm=float(rr[0,p]),initial_reference_bias_mm=float(bias[p]),
                        original_mse_mm2=mse,reference_bias_energy_mm2=a2,shape_error_energy_mm2=s2,cross_term_mm2=cross,
                        actual_residual_change_mean_mm=float(np.mean(rr[:,p]-r0[p])),predicted_shape_mean_mm=float(shape[:,p].mean())))
                np.savez_compressed(OUT/f'paths_{n}_{arm}_{seed}.npz',**paths)
                effects=factors[n,arm,seed] if seed!='ensemble' else np.mean([factors[n,arm,s] for s in [0,1,2]],axis=0)
                phi_p=.5*((effects[1,0]-effects[0,0])+(effects[1,1]-effects[0,1]))
                phi_r=.5*((effects[0,1]-effects[0,0])+(effects[1,1]-effects[1,0]))
                interaction=effects[1,1]-effects[1,0]-effects[0,1]+effects[0,0]
                close(phi_p+phi_r,effects[1,1]-effects[0,0],'factor_effect_sum')
                for p,point in enumerate(POINTS):
                    factor_effect.append(dict(origin=n,arm=arm,seed=seed,point=point,horizon=horizon,
                        physics_response_mean_mm=float(phi_p[:,p].mean()),residual_response_mean_mm=float(phi_r[:,p].mean()),
                        total_response_mean_mm=float(np.mean(effects[1,1,:,p]-effects[0,0,:,p])),
                        interaction_mean_mm=float(interaction[:,p].mean()),
                        half_target_change_mm=float(.5*np.mean(bank[old]['mean'][n:n+horizon,p]-b[:horizon,p]))))
                for physics in [0,1]:
                    for residual in [0,1]:
                        pred=b[:horizon]+effects[physics,residual]
                        factorrows.append(dict(origin=n,arm=arm,seed=seed,horizon=horizon,
                            physics_teacher=[old,new][physics],residual_teacher=[old,new][residual],baseline_teacher=new,
                            **metrics(pred,y[n:n+horizon])))
                        for p,point in enumerate(POINTS):
                            factorpoint.append(dict(origin=n,arm=arm,seed=seed,horizon=horizon,point=point,
                                physics_teacher=[old,new][physics],residual_teacher=[old,new][residual],
                                correction_mean_mm=float(effects[physics,residual,:,p].mean()),
                                required_residual_mean_mm=float(np.mean(y[n:n+horizon,p]-b[:horizon,p]))))
                np.savez_compressed(OUT/f'factor_{n}_{arm}_{seed}.npz',correction=effects)
    tables=dict(forecast_summary=rows,forecast_by_point=prows,mse_components=components,
                training_teacher_age=support,training_support_summary=support_total,
                teacher_factor_summary=factorrows,teacher_factor_by_point=factorpoint,teacher_factor_effects=factor_effect)
    for name,values in tables.items():
        pd.DataFrame(values).to_csv(OUT/f'{name}.csv',index=False,float_format='%.17g')
    # Do not silently replace any original issue or source.
    for name,digest in checkpoints.items():assert g.sha(g.ROOT/name)==digest
    g.verify_implementation(cfg);o.verify_implementation(ocfg)
    dump('numeric_checks.json',checks)
    dump('receipt.json',dict(status='passed',finished_utc=g.utc(),elapsed_seconds=time.monotonic()-start,
        checkpoints=len(checkpoints),checkpoint_hashes=checkpoints,
        checked_values=sum(c['values'] for c in checks),max_difference=max(c['difference'] for c in checks),
        rows={name:len(values) for name,values in tables.items()},training_updates=0,physical_forwards=0,
        repository_writes=0,probability_changes=0,h0_queries=0,
        input_label_boundary='Only y[:n] supplied to model input builders; all future labels used retrospectively for scoring.',
        source_entries=dict(graph=g.guard(),origin=o.guard())))
    dump('hashes.json',{str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='hashes.json'})
    print('COMPLETE',json.dumps({name:len(values) for name,values in tables.items()}),flush=True)


if __name__=='__main__':
    main()
